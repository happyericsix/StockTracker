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

import java.time.LocalDate;
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

    /**
     * 切换**决策来源**（rule ⇄ agent），并记下生效日。
     *
     * <h3>为什么要专门做一条路径，而不是让调用方直接改字段</h3>
     * 换决策方式是**改历史解释依据**的事：曲线从这一天起分成两段。
     * 所以这里做三件必须一起发生的事：
     * <ol>
     *   <li>归一（认不出的模式一律 {@code unknown_*}，**不默认成 rule**）；</li>
     *   <li>记生效日 —— 报告要写"自 X 起由 agent 决策"，否则用户会把两段看成一条线；</li>
     *   <li>**没变化就不改**：重复设成同一个模式不该刷新生效日，
     *       否则分界点会往后漂，每一次"确认一下"都变成一次"重新分段"。</li>
     * </ol>
     */
    @Transactional
    public StrategyResponse switchDecisionMode(String username, Long id, String decisionMode) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        String normalized = ExecutionContract.normalizeDecisionMode(decisionMode);
        String current = ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode());
        if (!normalized.equals(current)) {
            strategy.setDecisionMode(normalized);
            strategy.setDecisionModeSince(LocalDate.now(ZoneId.of("Asia/Shanghai")));
            strategyRepository.save(strategy);
            log.info("User {} switched strategy id={} decision mode {} -> {}",
                    username, id, current, normalized);
        }
        return StrategyResponse.from(strategy);
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

    // ==================== 样本外验证（多标的 × 多时段） ====================

    /**
     * 换票换段的验证池 —— **由代码固定，不由模型挑**。
     *
     * <h3>为什么必须写死在这里</h3>
     * "挑选样本＝挑选证据"。让模型（或让报告临时凑一份）决定拿哪几只票、哪几段行情去验证，
     * 就等于允许"挑到结论为止"：同一份策略换一批标的，平均超额可以从 -1.35% 翻成 +1.08%
     * （实测，见 §7.1.1）。固定池子的代价是样本与策略无关，收益是**结论可比**。
     *
     * <p>池子选的是流动性好的主板大票：不是为了"好看"，而是避免结论被
     * 流动性风险、ST、退市这些与规则无关的因素主导。
     */
    public static final List<String> VERIFICATION_PEERS = List.of(
            "600519", "000858", "600036", "000001", "601318", "002594", "600030", "000651");

    /** 验证时段长度（年）。**3 年是刻意的**：够跨一次风格切换，又不至于让前复权数据失真太大。 */
    public static final int VERIFICATION_YEARS = 3;
    /** 切成几段。3 段是"样本外"的最小可用形态（能看出结论是否只在某一段成立）。 */
    public static final int VERIFICATION_SEGMENTS = 3;
    /** 验证结果超过这么多天就不再被报告当作"当前结论"引用。 */
    public static final int VERIFICATION_STALE_DAYS = 14;

    /**
     * 跑一次样本外验证，并把**汇总结论**记成客观事实。
     *
     * <p>与 {@link #runBacktest} 并列而不是合并：单标的单段回测回答"这段行情里数字是多少"，
     * 验证回答"这条规则换票换段还成不成立" —— 后者才有资格支撑"要不要改"的讨论。
     *
     * @return Python 端点的原始响应（含逐标的明细，供需要时深挖）
     */
    public JsonNode verifyMatrix(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        return verifyMatrix(user, strategy);
    }

    /** 定时任务用（不经过 username 解析）。 */
    public JsonNode verifyMatrix(User user, Strategy strategy) {
        if (user == null || strategy == null || strategy.getId() == null) {
            return null;
        }
        java.time.LocalDate today = java.time.LocalDate.now(ZoneId.of("Asia/Shanghai"));
        JsonNode result = strategyClient.backtestMatrix(
                strategy.getConfigJson(),
                verificationSymbols(strategy),
                VERIFICATION_SEGMENTS,
                today.minusYears(VERIFICATION_YEARS).toString(),
                today.toString());
        recordVerificationFacts(user, strategy, result);
        log.info("Verified strategy id={} across {} symbols x {} segments",
                strategy.getId(), verificationSymbols(strategy).size(), VERIFICATION_SEGMENTS);
        return result;
    }

    /**
     * 这一轮验证要跑哪些标的：策略自己的标的 + 固定池（去重）。
     *
     * <p>为什么把策略自己的标的放进去：报告里说的"这条规则"首先是跑在那个标的上的规则，
     * 只验证池子里别的票会答成另一个问题。
     */
    public static List<String> verificationSymbols(Strategy strategy) {
        List<String> symbols = new ArrayList<>();
        if (strategy != null && strategy.getSymbol() != null && !strategy.getSymbol().isBlank()) {
            symbols.add(strategy.getSymbol().trim());
        }
        for (String peer : VERIFICATION_PEERS) {
            if (!symbols.contains(peer)) {
                symbols.add(peer);
            }
        }
        return symbols;
    }

    /**
     * 验证结论 → 客观事实。
     *
     * <h3>"没有可用样本"必须和"表现平平"分开</h3>
     * 取不到数据时只写 {@code verify_at} 与 {@code verify_valid_cells_count=0}，
     * **其余键不写**：写了就会取代掉上一次的真实数字，于是报告会把
     * "这次没验证成"讲成上次的结论。读的一侧（报告）以 {@code valid_cells_count=0} 为准，
     * 见 {@code PaperReviewReportService}。
     */
    private void recordVerificationFacts(User user, Strategy strategy, JsonNode result) {
        try {
            if (memoryFactService == null || memoryService == null || result == null
                    || !result.isObject()) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime ranAt = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
            JsonNode summary = result.get("summary");
            int validCells = result.hasNonNull("cells_valid") ? result.get("cells_valid").asInt(0) : 0;

            List<MemoryFactRequest> facts = new ArrayList<>();
            addFact(facts, subject, ObjectiveFactKeys.VERIFY_AT, ranAt.withNano(0).toString(), ranAt);
            addFact(facts, subject, ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT, validCells, ranAt);
            if (validCells > 0 && summary != null && summary.isObject()) {
                addMetric(facts, subject, ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT,
                        summary.get("beat_buy_and_hold"), ranAt);
                addMetric(facts, subject, ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT,
                        summary.get("avg_excess_pct"), ranAt);
                addMetric(facts, subject, ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT,
                        summary.get("avg_cost_pct_of_capital"), ranAt);
                String engineVersion = textOr(result.get("engine_version"), "");
                if (!engineVersion.isBlank()) {
                    addFact(facts, subject, ObjectiveFactKeys.VERIFY_ENGINE_VERSION, engineVersion, ranAt);
                }
            }
            memoryFactService.recordObjective(user.getId(),
                    memoryService.currentSessionKey(user.getId()), facts);
        } catch (Exception e) {
            log.warn("验证客观事实入账失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
        }
    }

    private static void addFact(List<MemoryFactRequest> facts, String subject, String predicate,
                                Object value, LocalDateTime dataAsOf) {
        MemoryFactRequest fact = ObjectiveFactKeys.observation(subject, predicate, value, dataAsOf);
        if (fact != null) {
            facts.add(fact);
        }
    }

    /**
     * 读回最近一次验证结论（报告用）。
     *
     * <p>读的是**当前有效**的那条事实（取代链末端）——"验证结论"这件事在记忆里只有一份，
     * 而不是"每次都追加一行让读者自己找最新的"。
     */
    public VerificationSummary latestVerification(Long userId, Long strategyId) {
        if (memoryFactService == null || userId == null || strategyId == null) {
            return null;
        }
        try {
            String subject = ObjectiveFactKeys.strategySubject(strategyId);
            java.util.Map<String, String> values = memoryFactService.activeFactValues(
                    userId, subject, List.of(
                            ObjectiveFactKeys.VERIFY_AT,
                            ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT,
                            ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT,
                            ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT,
                            ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT,
                            ObjectiveFactKeys.VERIFY_ENGINE_VERSION));
            if (values.isEmpty() || !values.containsKey(ObjectiveFactKeys.VERIFY_AT)) {
                return null;
            }
            return VerificationSummary.from(values);
        } catch (Exception e) {
            log.warn("读取验证结论失败 strategyId={}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    /** 一份验证结论的**读视图**（值都是事实通道里的字符串，解析失败即视为缺失）。 */
    public record VerificationSummary(String ranAt, int validCells, int beatBuyAndHold,
                                      Double avgExcessPct, Double feeDragPct, String engineVersion) {

        static VerificationSummary from(java.util.Map<String, String> values) {
            return new VerificationSummary(
                    values.get(ObjectiveFactKeys.VERIFY_AT),
                    parseInt(values.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT)),
                    parseInt(values.get(ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT)),
                    parseDouble(values.get(ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT)),
                    parseDouble(values.get(ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT)),
                    values.get(ObjectiveFactKeys.VERIFY_ENGINE_VERSION));
        }

        /** 没有可用样本：这次验证不成结论（与"表现平平"是两回事）。 */
        public boolean hasSample() {
            return validCells > 0;
        }

        private static int parseInt(String raw) {
            try {
                return raw == null ? 0 : (int) Double.parseDouble(raw.trim());
            } catch (NumberFormatException e) {
                return 0;
            }
        }

        private static Double parseDouble(String raw) {
            try {
                return raw == null ? null : Double.valueOf(raw.trim());
            } catch (NumberFormatException e) {
                return null;
            }
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
