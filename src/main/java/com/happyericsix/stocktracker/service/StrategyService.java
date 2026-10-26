package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.event.DecisionModeSwitchedEvent;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
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
    /** 决策来源切换的广播（模拟盘侧监听后立刻开第一次委员会）。 */
    private final ApplicationEventPublisher eventPublisher;

    public StrategyService(StrategyRepository strategyRepository,
                           StrategyClient strategyClient,
                           UserRepository userRepository,
                           MemoryFactService memoryFactService,
                           MemoryService memoryService,
                           ApplicationEventPublisher eventPublisher) {
        this.strategyRepository = strategyRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
        this.eventPublisher = eventPublisher;
    }

    // 不包 @Transactional：校验要先调 Python（阻塞 HTTP），包进事务会占着一个
    // 数据库连接等网络 —— 与结算侧"决策调用不进事务"同一条纪律（updateStrategy 同款）。
    // 单条 save() 自身原子，不需要外层事务。
    public StrategyResponse createStrategy(String username, StrategyRequest request) {
        User user = getUser(username);
        // 创建与编辑同一道门（2026-10-15 补）：此前只有编辑路径 fail-closed 校验，
        // 坏 config 能从创建进来 —— 最危险的是符号写反的风控参数：stop_loss_pct 写成
        // +8 在引擎里是"涨幅 ≤ 8%"恒真，而止损走硬通道不等模型，等于入场即卖的碎钞机。
        if (request.getConfigJson() == null || request.getConfigJson().isBlank()) {
            throw new IllegalArgumentException("configJson 不能为空：策略 DSL（entry/exit）与风控参数是必填的");
        }
        String createError = validateConfigOrReject(request.getConfigJson());
        if (createError != null) {
            throw new IllegalArgumentException("规则校验未通过：" + createError);
        }
        Strategy strategy = Strategy.builder()
                .name(request.getName())
                .symbol(request.getSymbol())
                .configJson(request.getConfigJson())
                .user(user)
                .paperEnabled(false)
                // 2026-10-15 起不再有"纯 rule 模式"：新策略一律由 agent（多角色委员会）
                // 做决策；规则 DSL 是策略定义与确定性降级底座，不是可选的决策来源
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .decisionModeSince(LocalDate.now(ZoneId.of("Asia/Shanghai")))
                .build();

        strategy = strategyRepository.save(strategy);
        log.info("User {} created strategy id={} (decision_mode=agent)", username, strategy.getId());
        return StrategyResponse.from(strategy);
    }

    public List<StrategyResponse> listStrategies(String username) {
        User user = getUser(username);
        return strategyRepository.findByUserIdOrderByUpdatedAtDesc(user.getId()).stream()
                .map(StrategyResponse::from)
                .collect(Collectors.toList());
    }

    @Transactional
    public StrategyResponse get(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        return StrategyResponse.from(strategy);
    }

    public StrategyResponse updateStrategy(String username, Long id, StrategyRequest request) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        // 换标的 = 换了一条策略：模拟账户、成交历史、验证结论全部挂在原标的上，
        // 就地改 symbol 会把这些数据变成"不是同一只票跑出来的"。换票请新建。
        if (request.getSymbol() != null && !request.getSymbol().isBlank()
                && !request.getSymbol().equalsIgnoreCase(strategy.getSymbol())) {
            throw new IllegalArgumentException(
                    "暂不支持更换标的：账户与历史表现都挂在原标的上，换票请新建一条策略");
        }
        if (request.getName() != null && !request.getName().isBlank()) {
            strategy.setName(request.getName());
        }
        if (request.getConfigJson() != null && !request.getConfigJson().isBlank()) {
            String error = validateConfigOrReject(request.getConfigJson());
            if (error != null) {
                throw new IllegalArgumentException("规则校验未通过：" + error);
            }
            strategy.setConfigJson(request.getConfigJson());
            // 口径分界（与 decisionModeSince 同一条纪律）：改过规则之后的净值
            // 与之前不可比，报告与页面据此画线。只打规则变更，改名不动它。
            strategy.setConfigUpdatedAt(java.time.LocalDateTime.now());
        }

        strategy = strategyRepository.save(strategy);
        log.info("User {} updated strategy id={} (configChanged={})",
                username, id, strategy.getConfigUpdatedAt() != null);
        return StrategyResponse.from(strategy);
    }

    /**
     * 规则 DSL 校验（编辑保存的守门员）。返回 null = 通过；非空 = 给用户看的错误。
     * 校验服务不可用时**fail-closed**：宁可不保存，也不把改坏的规则放进
     * 下一次结算 —— 那会在痕迹里炸出一串 unknown，而用户以为已经改好了。
     */
    private String validateConfigOrReject(String configJson) {
        try {
            tools.jackson.databind.JsonNode result = strategyClient.validateConfig(configJson);
            if (result != null && result.path("valid").asBoolean(false)) {
                return null;
            }
            String error = result == null ? null : result.path("error").asText("");
            return error == null || error.isBlank() ? "规则结构无法识别" : error;
        } catch (Exception e) {
            log.warn("规则校验服务不可用，编辑被拒绝: {}", e.getMessage());
            return "校验服务暂不可用，请稍后再试";
        }
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
     * 开启只读分享：生成新 token（旧 token 随之失效——重新生成就是"撤回旧链接"）。
     * 公开视图只含策略本身与模拟盘表现，**绝不含**用户名/邮箱等身份信息。
     */
    @Transactional
    public String enableShare(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        String token = java.util.UUID.randomUUID().toString().replace("-", "");
        strategy.setShareToken(token);
        strategyRepository.save(strategy);
        log.info("User {} enabled share link for strategy id={}", username, id);
        return token;
    }

    /** 关闭分享（token 置空，旧链接立刻失效）。 */
    @Transactional
    public void disableShare(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        strategy.setShareToken(null);
        strategyRepository.save(strategy);
        log.info("User {} disabled share link for strategy id={}", username, id);
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
            // Python /api/v1/strategies/backtest 返回的是 {"valid": bool, "backtest": {...}} 信封，
            // 指标嵌在 backtest 字段下 —— 与 ChatService.buildBacktestSummary 是同一份解释。
            // 失败响应（valid=false）只有 error 字段、没有任何指标，path 不到对象自然整条跳过。
            JsonNode metrics = result.path("backtest");
            if (!metrics.isObject()) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime ranAt = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
            List<MemoryFactRequest> facts = new ArrayList<>();
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT,
                    metrics.get("total_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_BUY_AND_HOLD_RETURN_PCT,
                    metrics.get("buy_and_hold_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_EXCESS_RETURN_PCT,
                    metrics.get("excess_return_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT,
                    metrics.get("max_drawdown_pct"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_SHARPE,
                    metrics.get("sharpe_ratio"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_WIN_RATE_PCT,
                    metrics.get("win_rate"), ranAt);
            addMetric(facts, subject, ObjectiveFactKeys.BACKTEST_TRADE_COUNT,
                    metrics.get("trade_count"), ranAt);
            MemoryFactRequest ranAtFact = ObjectiveFactKeys.observation(
                    subject, ObjectiveFactKeys.BACKTEST_AT, ranAt.withNano(0).toString(), ranAt);
            if (ranAtFact != null) {
                facts.add(ranAtFact);
            }
            // 双层记忆（2026-10-09）：上面那些谓词是给取代链与 recall 的；
            // 用户看到的是这一句人话结论（userVisible=true）
            String story = backtestStory(metrics);
            MemoryFactRequest storyFact = ObjectiveFactKeys.observationStory(subject, story, ranAt);
            if (storyFact != null) {
                facts.add(storyFact);
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

        // 2026-10-15：决策来源已统一为 agent（多角色委员会）。规则引擎保留为策略定义
        // 与确定性降级底座，不再作为可切换的独立模式——保留历史解释依据比提供一个
        // 通往旧口径的开关更重要。
        throw new IllegalArgumentException(
                "决策来源已统一为多角色委员会（agent），不再支持切换；"
                        + "规则引擎保留为策略定义与降级底座");
    }
    /*
        旧实现留档（口径变更前的行为）：
        String normalized = ExecutionContract.normalizeDecisionMode(decisionMode);
        String current = ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode());
        if (!normalized.equals(current)) {
            strategy.setDecisionMode(normalized);
            strategy.setDecisionModeSince(LocalDate.now(ZoneId.of("Asia/Shanghai")));
            strategyRepository.save(strategy);
            eventPublisher.publishEvent(new DecisionModeSwitchedEvent(
                    strategy.getId(), normalized, Boolean.TRUE.equals(strategy.getPaperEnabled())));
        }
        return StrategyResponse.from(strategy);
    */

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
            // 双层记忆：验证的人话结论（validCells=0 也要说清"这次没验证成"）
            MemoryFactRequest storyFact = ObjectiveFactKeys.observationStory(
                    subject, verificationStory(validCells, summary), ranAt);
            if (storyFact != null) {
                facts.add(storyFact);
            }
            memoryFactService.recordObjective(user.getId(),
                    memoryService.currentSessionKey(user.getId()), facts);
        } catch (Exception e) {
            log.warn("验证客观事实入账失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
        }
    }

    /** 回测指标 → 一句话结论。字段缺失时退化为短句，绝不抛（记忆是旁路）。 */
    static String backtestStory(JsonNode metrics) {
        try {
            StringBuilder story = new StringBuilder("回测结论：");
            if (metrics.hasNonNull("total_return_pct")) {
                story.append("总收益 ").append(metrics.get("total_return_pct").asDouble()).append("%");
            }
            if (metrics.hasNonNull("buy_and_hold_return_pct")) {
                story.append("（同期买入持有 ").append(metrics.get("buy_and_hold_return_pct").asDouble())
                        .append("%）");
            }
            if (metrics.hasNonNull("max_drawdown_pct")) {
                story.append("，最大回撤 ").append(metrics.get("max_drawdown_pct").asDouble()).append("%");
            }
            if (metrics.hasNonNull("trade_count")) {
                story.append("，").append(metrics.get("trade_count").asInt()).append(" 笔交易");
            }
            if (metrics.hasNonNull("win_rate")) {
                story.append("，胜率 ").append(metrics.get("win_rate").asDouble()).append("%");
            }
            return story.length() > "回测结论：".length() ? story.toString() : "";
        } catch (Exception e) {
            return "";
        }
    }

    /** 验证指标 → 一句话结论；0 样本明确说"这次没验证成"（与"表现平平"分开）。 */
    static String verificationStory(int validCells, JsonNode summary) {
        try {
            if (validCells <= 0 || summary == null || !summary.isObject()) {
                return "样本外验证：本次没有可用样本（数据不足），此前的验证结论未被取代";
            }
            StringBuilder story = new StringBuilder("样本外验证结论：").append(validCells).append(" 个有效样本");
            if (summary.hasNonNull("beat_buy_and_hold")) {
                story.append("，其中 ").append(summary.get("beat_buy_and_hold").asInt()).append(" 个跑赢买入持有");
            }
            if (summary.hasNonNull("avg_excess_pct")) {
                story.append("，平均超额 ").append(summary.get("avg_excess_pct").asDouble()).append("%");
            }
            if (summary.hasNonNull("avg_cost_pct_of_capital")) {
                story.append("（摩擦损耗约 ").append(summary.get("avg_cost_pct_of_capital").asDouble()).append("%）");
            }
            return story.toString();
        } catch (Exception e) {
            return "";
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

    // 同 createStrategy：校验要调 Python，不进事务（阻塞 HTTP 不占数据库连接）。
    public StrategyResponse createFromAgent(String username, JsonNode strategyJson) {
        User user = getUser(username);
        JsonNode safeJson = strategyJson != null ? strategyJson : null;

        String name = textOr(safeJson == null ? null : safeJson.get("name"), "策略");
        String symbol = textOr(safeJson == null ? null : safeJson.get("symbol"), "");
        String configJson = safeJson == null ? "{}" : safeJson.toString();

        // AI 输出是 schema 偏差率最高的一类 config（2026-10-15 前这里零校验直接落库），
        // 与手建路径同一道 fail-closed 门：坏 config 的策略比没有策略更糟。
        if (symbol.isBlank()) {
            throw new IllegalArgumentException("AI 生成的策略缺少标的代码（symbol），无法保存");
        }
        String agentError = validateConfigOrReject(configJson);
        if (agentError != null) {
            throw new IllegalArgumentException("AI 生成的策略未通过规则校验：" + agentError);
        }

        Strategy strategy = Strategy.builder()
                .name(name)
                .symbol(symbol)
                .configJson(configJson)
                .user(user)
                .paperEnabled(false)
                // 与 createStrategy 同一条纪律（2026-10-15 起无纯 rule 模式）：AI 建的策略
                // 同样由 agent 委员会决策。此前漏设 → decisionMode=null 按 rule 读，
                // 启动后既无首会也无授权，盘中只能哑等到出事。
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .decisionModeSince(LocalDate.now(ZoneId.of("Asia/Shanghai")))
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
