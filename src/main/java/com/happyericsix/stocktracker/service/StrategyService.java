package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class StrategyService {

    private static final Logger log = LoggerFactory.getLogger(StrategyService.class);

    private final StrategyRepository strategyRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;
    /** 客观事实写入（W1）。**允许为 null**：单测直接 @InjectMocks 时没有这个替身，
     *  而"记忆写不进去"绝不能让一条本来成功的回测失败（见 recordBacktestFacts）。 */
    private final MemoryFactService memoryFactService;
    private final MemoryService memoryService;

    public StrategyService(StrategyRepository strategyRepository,
                           StrategyClient strategyClient,
                           UserRepository userRepository,
                           MemoryFactService memoryFactService,
                           MemoryService memoryService) {
        this.strategyRepository = strategyRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
    }

    @Transactional
    public StrategyResponse createStrategy(String username, StrategyRequest request) {
        User user = getUser(username);
        Strategy strategy = Strategy.builder()
                .name(request.getName())
                .symbol(request.getSymbol())
                .configJson(request.getConfigJson())
                .user(user)
                .paperEnabled(false)
                .build();

        strategy = strategyRepository.save(strategy);
        log.info("User {} created strategy id={}", username, strategy.getId());
        return StrategyResponse.from(strategy);
    }

    public List<StrategyResponse> listStrategies(String username) {
        User user = getUser(username);
        return strategyRepository.findByUserIdOrderByUpdatedAtDesc(user.getId()).stream()
                .map(StrategyResponse::from)
                .collect(Collectors.toList());
    }

    @Transactional
    public StrategyResponse updateStrategy(String username, Long id, StrategyRequest request) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        if (request.getName() != null && !request.getName().isBlank()) {
            strategy.setName(request.getName());
        }
        if (request.getSymbol() != null && !request.getSymbol().isBlank()) {
            strategy.setSymbol(request.getSymbol());
        }
        if (request.getConfigJson() != null && !request.getConfigJson().isBlank()) {
            strategy.setConfigJson(request.getConfigJson());
        }

        strategy = strategyRepository.save(strategy);
        log.info("User {} updated strategy id={}", username, id);
        return StrategyResponse.from(strategy);
    }

    @Transactional
    public void deleteStrategy(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        strategyRepository.delete(strategy);
        log.info("User {} deleted strategy id={}", username, id);
    }

    /**
     * 不包 @Transactional：先跑 Python 回测（最长 60s 的阻塞 HTTP），
     * 成功后再用 repository 自带的短事务落 lastBacktestAt，
     * 避免一次回测把数据库连接/事务占用 60s。
     */
    public JsonNode runBacktest(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        JsonNode result = strategyClient.backtestStrategy(strategy.getConfigJson());
        strategy.setLastBacktestAt(LocalDateTime.now());
        strategyRepository.save(strategy);
        log.info("User {} ran backtest for strategy id={}", username, id);
        recordBacktestFacts(user, strategy, result);
        return result;
    }

    /**
     * 把这次回测的指标记成**客观事实**（W1）。
     *
     * <h3>为什么在这里记</h3>
     * 记忆里原本只有"用户说过的话"。"改完参数是变好还是变差"这个问题必须由可复算的观测回答，
     * 而回测结果正是这种观测 —— 并且只有这条路径同时知道 {@code userId} 与 {@code strategyId}。
     * 记进取代链之后，"这只策略的回撤在变好还是变差"变成一次历史查询，
     * 而不是让模型去比两段自然语言。
     *
     * <h3>三条纪律</h3>
     * <ul>
     *   <li><b>缺字段就不记</b>：不补 0。一个假的 0 会取代掉上一次真实的回撤值，
     *       "回撤突然变成 0"比"这次没有记录"危险得多；</li>
     *   <li><b>失败绝不影响回测</b>：记忆是增强功能（与 ChatService 写账本同一条原则）；</li>
     *   <li><b>依赖允许为 null</b>：单测里没有这些替身，不能让它们 NPE 掉一次成功的回测。</li>
     * </ul>
     */
    private void recordBacktestFacts(User user, Strategy strategy, JsonNode result) {
        try {
            if (memoryFactService == null || memoryService == null || user == null
                    || strategy == null || result == null || !result.isObject()) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime ranAt = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
            List<MemoryFactRequest> facts = new ArrayList<>();
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT,
                    result.get("total_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_BUY_AND_HOLD_RETURN_PCT,
                    result.get("buy_and_hold_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_EXCESS_RETURN_PCT,
                    result.get("excess_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT,
                    result.get("max_drawdown_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_SHARPE,
                    result.get("sharpe_ratio"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_WIN_RATE_PCT,
                    result.get("win_rate"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_TRADE_COUNT,
                    result.get("trade_count"), ranAt);
            MemoryFactRequest ranAtFact = ObjectiveFactKeys.observation(
                    subject, ObjectiveFactKeys.BACKTEST_AT, ranAt.withNano(0).toString(), ranAt);
            if (ranAtFact != null) {
                facts.add(ranAtFact);
            }
            if (facts.isEmpty()) {
                return;
            }
            memoryFactService.recordObjective(user.getId(),
                    memoryService.currentSessionKey(user.getId()), facts);
        } catch (Exception e) {
            // 记忆是增强功能：回测结果已经算出来了，绝不能因为记不进记忆就把它丢掉
            log.warn("回测客观事实入账失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
        }
    }

    /** 只记**确实是数字**的字段：非数字（null、"N/A"、字符串）一律跳过。 */
    private static void addMetric(List<MemoryFactRequest> facts, String subject, String predicate,
                                  JsonNode node, LocalDateTime dataAsOf) {
        if (node == null || !node.isNumber()) {
            return;
        }
        Object value = node.isIntegralNumber() ? node.asLong() : node.asDouble();
        MemoryFactRequest fact = ObjectiveFactKeys.observation(subject, predicate, value, dataAsOf);
        if (fact != null) {
            facts.add(fact);
        }
    }

    public ModelDiagnosticResponse getModelDiagnostic(String username, Long id) {
        Strategy strategy = strategyRepository.findByIdAndUserId(id, getUser(username).getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        JsonNode node = strategyClient.getModelDiagnostic(strategy.getSymbol());
        String symbol = node.hasNonNull("symbol") ? node.get("symbol").asText() : strategy.getSymbol();
        return new ModelDiagnosticResponse(
                symbol,
                node.get("risk"),
                node.get("model_status"),
                node.get("model_consensus"),
                node.hasNonNull("disclaimer") ? node.get("disclaimer").asText() : ""
        );
    }

    @Transactional
    public StrategyResponse createFromAgent(String username, JsonNode strategyJson) {
        User user = getUser(username);
        JsonNode safeJson = strategyJson != null ? strategyJson : null;

        String name = textOr(safeJson == null ? null : safeJson.get("name"), "策略");
        String symbol = textOr(safeJson == null ? null : safeJson.get("symbol"), "");
        String configJson = safeJson == null ? "{}" : safeJson.toString();

        Strategy strategy = Strategy.builder()
                .name(name)
                .symbol(symbol)
                .configJson(configJson)
                .user(user)
                .paperEnabled(false)
                .build();

        strategy = strategyRepository.save(strategy);
        log.info("User {} created strategy id={} from agent", username, strategy.getId());
        return StrategyResponse.from(strategy);
    }

    private String textOr(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return (text == null || text.isBlank()) ? fallback : text;
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }
}
