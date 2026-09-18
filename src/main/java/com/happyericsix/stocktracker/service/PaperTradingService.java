package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperEquityResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;

@Service
public class PaperTradingService {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingService.class);

    private static final double DEFAULT_INITIAL_CAPITAL = 100000.0;
    private static final double DEFAULT_COMMISSION_PCT = 0.1;
    private static final double DEFAULT_SLIPPAGE_PCT = 0.1;
    /** 卖出印花税（A 股，单边卖出；以最新法规为准） */
    private static final double DEFAULT_STAMP_TAX_PCT = 0.05;
    private static final double DEFAULT_MIN_COMMISSION_YUAN = 5.0;
    private static final String DEFAULT_POSITION_TYPE = "full";
    private static final double DEFAULT_SIZE_PCT = 100.0;

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperTraceService paperTraceService;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;
    /** 客观事实写入（W1）。允许为 null：单测没有替身，且"记不进记忆"不该影响结算。 */
    private final MemoryFactService memoryFactService;
    private final MemoryService memoryService;
    /** 盘后复盘报告。允许为 null（单测），失败只是少一条消息。 */
    private final PaperReviewReportService paperReviewReportService;
    /** 样本外验证（周频）。允许为 null：单测里没有这个替身，周任务本身也不该被单测触发。 */
    private final StrategyService strategyService;
    /** 预期登记/回填。允许为 null（单测）；失败只是少一次回填。 */
    private final ExpectationService expectationService;
    private final ObjectMapper mapper = new ObjectMapper();
    private final TransactionTemplate transactionTemplate;

    /**
     * 单一构造函数：**不要为了"测试方便"再加几个重载**。
     * Spring 遇到多个构造函数且没有 {@code @Autowired} 时会去找无参构造，直接起不来
     * （实测踩过：三个重载 → {@code NoSuchMethodException: <init>()} → 整个应用上下文挂掉）。
     * 可选依赖（报告、验证）允许为 null，用 null 判断兜住，而不是靠另一个构造函数。
     */
    /**
     * 单一构造函数：**不要再为了"测试方便"加重载**。
     * 多个构造函数而没有 {@code @Autowired} 时 Spring 会去找无参构造，直接起不来
     * （实测踩过：整个应用上下文挂掉）。可选依赖（报告/验证/预期）允许为 null，
     * 用 null 判断兜住，而不是靠另一个构造函数。
     */
    public PaperTradingService(StrategyRepository strategyRepository,
                               PaperAccountRepository paperAccountRepository,
                               PaperTradeRepository paperTradeRepository,
                               PaperTraceService paperTraceService,
                               PaperEquitySnapshotRepository paperEquitySnapshotRepository,
                               StrategyClient strategyClient,
                               UserRepository userRepository,
                               PlatformTransactionManager transactionManager,
                               MemoryFactService memoryFactService,
                               MemoryService memoryService,
                               PaperReviewReportService paperReviewReportService,
                               StrategyService strategyService,
                               ExpectationService expectationService) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.paperTraceService = paperTraceService;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
        this.paperReviewReportService = paperReviewReportService;
        this.strategyService = strategyService;
        this.expectationService = expectationService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.transactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /**
     * 不包 @Transactional：evaluateRealtimeStrategy 内部会阻塞调用 Python（最长 60s），
     * 若包在长事务里会占住数据库连接；改为各 repository 写操作走自身短事务。
     */
    public PaperAccountResponse startPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);

        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseGet(PaperAccount::new);
        initializeAccount(account, strategy);

        strategy.setPaperEnabled(true);
        account = paperAccountRepository.save(account);
        strategyRepository.save(strategy);

        PaperAccount evaluated = evaluateRealtimeStrategy(strategyId, ExecutionContract.TRIGGER_MANUAL);
        if (evaluated != null) {
            account = evaluated;
        }

        log.info("User {} started paper trading for strategy id={}", username, strategyId);
        return PaperAccountResponse.from(account);
    }

    @Transactional
    public void stopPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);
        strategy.setPaperEnabled(false);
        strategyRepository.save(strategy);
        log.info("User {} stopped paper trading for strategy id={}", username, strategyId);
    }

    public void evaluateDaily() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        LocalDate today = LocalDate.now();
        for (Strategy strategy : strategies) {
            try {
                Long strategyId = strategy.getId();
                PaperAccount settled = transactionTemplate.execute(status -> evaluateStrategy(strategyId, today));
                // 盘后复盘放在结算事务**提交之后**：报告读的是已落库的痕迹与账户，
                // 而报告这一步再怎么出问题，都不可能回滚掉当天的真实成交
                // （发消息用的是自己的独立事务，见 MessageService.saveReport）。
                if (settled != null && paperReviewReportService != null) {
                    transactionTemplate.execute(status -> {
                        Strategy fresh = strategyRepository.findById(strategyId).orElse(null);
                        paperReviewReportService.composeDailyReport(fresh, settled, today);
                        return null;
                    });
                }
            } catch (Exception e) {
                log.error("Paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    public void evaluateRealtime() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        for (Strategy strategy : strategies) {
            try {
                Long strategyId = strategy.getId();
                transactionTemplate.execute(status -> {
                    evaluateRealtimeStrategy(strategyId);
                    return null;
                });
            } catch (Exception e) {
                log.error("Realtime paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    /**
     * 每周复盘：先重跑一次**样本外验证**，再发空转期摘要。
     *
     * <h3>为什么验证是每周而不是每天</h3>
     * ① 换票换段的结论不会因为今天多了一根 K 线就变，每天重跑是纯浪费（每次 9 只票的取数）；
     * ② 更重要的是**别让报告变成每天都换一个数字的东西** —— 那样用户学到的教训是
     * "这些数字不用认真看"。周频 + 明确的"上次验证是哪天"，比日频的噪声更有信息量。
     *
     * <p>两步都各自 try/catch：验证失败不该挡掉报告（报告里正会写"这次验证没有可用样本"）。
     */
    public void runWeeklyReview() {
        LocalDate today = LocalDate.now();
        for (Strategy strategy : strategyRepository.findByPaperEnabledTrue()) {
            try {
                if (strategyService != null) {
                    strategyService.verifyMatrix(strategy.getUser(), strategy);
                }
            } catch (Exception e) {
                log.error("Weekly verification failed for strategy id={}", strategy.getId(), e);
            }
            try {
                if (paperReviewReportService != null) {
                    PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId())
                            .orElse(null);
                    paperReviewReportService.composeIdleWeeklyReport(strategy, account, today);
                }
            } catch (Exception e) {
                log.error("Weekly idle report failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    public PaperAccountResponse getAccount(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("模拟账户不存在"));
        return PaperAccountResponse.from(account);
    }

    public List<PaperTradeResponse> getTrades(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategyId).stream()
                .map(PaperTradeResponse::from)
                .collect(Collectors.toList());
    }

    /**
     * 最近的结算痕迹（含"为什么没成交"）。
     *
     * <p>归属检查走与账户/成交同一条路（`loadStrategy`），痕迹不该成为绕过用户隔离的旁路。
     */
    public List<PaperTradeTraceResponse> getTraces(String username, Long strategyId, int limit) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTraceService.listRecent(strategyId, limit).stream()
                .map(PaperTradeTraceResponse::from)
                .collect(Collectors.toList());
    }

    public List<PaperTradeTraceResponse> getTracesForDay(String username, Long strategyId, LocalDate date) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTraceService.listForDay(strategyId, date).stream()
                .map(PaperTradeTraceResponse::from)
                .collect(Collectors.toList());
    }

    /**
     * 净值曲线（最近 N 个交易日）+ 由同一批点算出的汇总。
     *
     * <p>汇总走 {@link PaperEquitySeries} —— 报告与界面共用同一份算法，
     * 不让"界面一个数、报告另一个数"这种事有机会发生。
     */
    public PaperEquityResponse getEquityCurve(String username, Long strategyId, int days) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        int size = Math.max(1, Math.min(days, 500));
        List<PaperEquitySnapshot> series = new ArrayList<>(
                paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(
                        strategyId, PageRequest.of(0, size)));
        // 仓储按倒序取（要"最近 N 天"），算法要求正序 —— 在这里翻一次，别让算法去猜
        java.util.Collections.reverse(series);
        return new PaperEquityResponse(
                series.stream().map(PaperEquityResponse.Point::from).collect(Collectors.toList()),
                PaperEquitySeries.summarize(series));
    }

    private PaperAccount evaluateStrategy(Long strategyId, LocalDate today) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        // 今天是否已经有成交（盘中那条路成交过）。
        //
        // 它**只决定"这一节还能不能下单"**，不再决定"这一节做不做"：
        // 以前这里直接 return null，防重复下单是对的，但连带把当天的净值快照、客观事实、
        // 到期回填、当日报告一起跳过了 —— 于是"今天有成交"这条策略当天在净值曲线上
        // 就是一个缺口，而那恰好是唯一值得看的一天（净值曲线是判断策略有没有变好的主依据）。
        boolean alreadyTradedToday = paperTradeRepository
                .existsByStrategyIdAndTradeDate(strategy.getId(), today);

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }

        JsonNode position = buildPosition(account);
        // 决策源在这里分岔，**执行路径一行不改**（下面还是同一个 applyBarResult）：
        //   rule  → 策略 DSL 出信号
        //   agent → 多角色委员会出决策
        // 两条路都落进同一套封闭枚举与同一个结算管线；区别写在 fingerprint.decision_mode 里，
        // 于是两段曲线永远不会被当成可比（见 ExecutionContract.compareBlockReason）。
        String decisionMode = ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode());
        JsonNode result;
        if (alreadyTradedToday) {
            // 今天已经成交过：这一节只需要**当日收盘价与证据**，不需要任何决策。
            // 所以这里刻意走规则端点（确定性、零成本），而不是问委员会：
            // 委员会一次约 2 万 token，而它的产出（一个信号）在这一节里本来就不允许执行。
            result = strategyClient.evaluateBar(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position);
        } else if (isAgentMode(strategy)) {
            result = strategyClient.agentDecide(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position, lastBriefingDate(strategy.getId()));
        } else {
            result = strategyClient.evaluateBar(strategy.getConfigJson(), strategy.getSymbol(),
                    today.toString(), position);
        }
        if (result == null) {
            // 之前这里只打一行日志：那天为什么没结算，除了翻日志没有别的办法查。
            log.warn("{} decision returned null for strategy id={}", decisionMode, strategy.getId());
            return traceOnly(strategy, account, ExecutionContract.SETTLEMENT_DAILY,
                    ExecutionContract.TRIGGER_CRON, today, null, null,
                    ExecutionContract.SKIP_DATA_UNAVAILABLE);
        }
        // 休市/数据未出：Python 端明确标记请求日无 bar 时，禁止用旧 bar 冒充当日成交
        if (result.has("bar_date_missing") && result.get("bar_date_missing").asBoolean(false)) {
            log.warn("Paper settlement skipped for strategy id={} on {}: bar missing (休市/数据未出)",
                    strategy.getId(), today);
            PaperTradeTrace trace = newTrace(strategy, account, ExecutionContract.SETTLEMENT_DAILY,
                    ExecutionContract.TRIGGER_CRON, today, null, result);
            trace.setDecision(ExecutionContract.DECISION_SKIP);
            trace.setSkipReason(skipReasonOf(result, ExecutionContract.SKIP_NO_BAR));
            trace.setSignal(textOr(result.get("signal"), null));
            fillAccountAfter(trace, account);
            paperTraceService.record(trace);
            return account;
        }

        PaperAccount settled = applyBarResult(strategy, account, result, today, null,
                ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.TRIGGER_CRON,
                alreadyTradedToday);
        recordEquitySnapshot(strategy, settled, result, today);
        recordPaperFacts(strategy, settled, today);
        // 到期就回填预期（复用同一批净值快照，不额外查库）。
        // 旁路：写不进去只是少一次回填，绝不影响结算（与 recordPaperFacts 同一条纪律）。
        if (expectationService != null) {
            expectationService.evaluate(strategy.getUser() == null ? null : strategy.getUser().getId(),
                    strategy.getId(),
                    paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId()),
                    today);
        }
        return settled;
    }

    /**
     * 记一行每日净值快照 —— 只记**日线结算**，且与账户更新在**同一个事务**里。
     *
     * <h3>为什么必须在同一个事务</h3>
     * 净值曲线是时序查询的唯一真相源，客观事实链是它的"记忆语义"副本。
     * 两者若各写各的，"曲线上的净值"与"记忆里的净值"迟早会不一致，
     * 而这种不一致最难查：两边都看起来对。所以：账户、快照、事实在同一笔里落。
     *
     * <h3>为什么结算被跳过时不写</h3>
     * 休市/取数失败那天账户一个字段都没动，补一行"净值不变"是把**没结算**画成**没变化**。
     * 缺口就是缺口：报告会写清楚曲线有多少个点、从哪天到哪天。
     *
     * <p>失败**不吞**：快照属于结算记录本身（不是痕迹那样的旁路），写不进去就该让这笔结算回滚
     * —— 一次写失败意味着那天既没有成交记录也没有净值，重跑一遍即可；
     * 而"吞掉异常、账户动了但曲线缺一格"是**没法事后发现**的错。
     * 所以这里刻意**不**包 try/catch（与 {@code PaperTraceService} 的 fail-open 相反，
     * 因为两者的角色不同：痕迹是旁路证据，净值曲线是主记录）。
     */
    private void recordEquitySnapshot(Strategy strategy, PaperAccount account, JsonNode result,
                                      LocalDate tradeDate) {
        if (strategy == null || account == null || tradeDate == null) {
            return;
        }
        PaperEquitySnapshot snapshot = paperEquitySnapshotRepository
                .findByStrategyIdAndTradeDate(strategy.getId(), tradeDate)
                .orElseGet(() -> PaperEquitySnapshot.builder()
                        .user(strategy.getUser())
                        .strategy(strategy)
                        .tradeDate(tradeDate)
                        .build());
        snapshot.setAccount(account);
        snapshot.setEquity(Money.equity(account.getEquity()));
        snapshot.setCash(Money.amount(account.getCash()));
        snapshot.setShares(Money.price(account.getShares()));
        snapshot.setClosePrice(Money.price(priceOf(result)));
        paperEquitySnapshotRepository.save(snapshot);
    }

    /** 当日收盘价：优先用引擎回传的成交参考价，缺了就用快照里的 close。 */
    private static Double priceOf(JsonNode result) {
        if (result == null) {
            return null;
        }
        Double price = numberOrNull(result.get("price"));
        if (price != null && price > 0) {
            return price;
        }
        JsonNode bar = result.get("snapshot") == null ? null : result.get("snapshot").get("bar");
        return bar == null ? null : numberOrNull(bar.get("close"));
    }

    /**
     * 把当天的模拟盘结算记成**客观事实**（W1）。
     *
     * <h3>为什么只在"每日结算"里记，不在实时结算里记</h3>
     * {@link #evaluateRealtimeStrategy} 由价格刷新事件触发，一天可能跑几十上百次。
     * 在那里记事实的后果不是"多几条"，而是<b>把取代链冲垮</b>：
     * 同一个键上一天出现上百个值，历史链会变得没法读，
     * "净值什么时候真的变了"这个问题的答案就被噪声淹掉了。
     * 事实的粒度应该是"一个有意义的观测点"（收盘结算），不是"每一次内部计算"。
     *
     * <h3>为什么失败不入库也不影响结算</h3>
     * 与 {@link StrategyService} 的回测路径同一条纪律：结算的结果已经算出来了，
     * 记忆写不进去只是少了一份记录，绝不能让它回滚掉真实的成交与持仓。
     */
    private void recordPaperFacts(Strategy strategy, PaperAccount account, LocalDate tradeDate) {
        try {
            if (memoryFactService == null || memoryService == null || strategy == null
                    || account == null || strategy.getUser() == null) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime at = LocalDateTime.of(tradeDate, java.time.LocalTime.of(15, 0));
            List<MemoryFactRequest> facts = new ArrayList<>();
            addFact(facts, subject, ObjectiveFactKeys.PAPER_EQUITY, account.getEquity(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_CASH, account.getCash(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_SHARES, account.getShares(), at);
            // 收益率的口径只该有一处：初始本金是账户自己的字段，不让调用方各算一遍。
            // 用 BigDecimal 除再量化（收益率 4 位）—— 换成 double 除会让"净值没变"的日子
            // 因为浮点误差写出一个非 0 的收益率，取代链上就多出一条假变更。
            BigDecimal initial = account.getInitialCapital();
            BigDecimal equityNow = account.getEquity();
            if (initial != null && initial.signum() != 0 && equityNow != null) {
                BigDecimal returnPct = equityNow.subtract(initial)
                        .multiply(BigDecimal.valueOf(100))
                        .divide(initial, Money.RETURN_SCALE, RoundingMode.HALF_UP);
                addFact(facts, subject, ObjectiveFactKeys.PAPER_RETURN_PCT, returnPct, at);
            }
            addFact(facts, subject, ObjectiveFactKeys.PAPER_LAST_EVAL_AT,
                    at.withNano(0).toString(), at);
            // 由净值序列算出来的两个量：**回撤**与**连续空仓天数**。
            // 它们回答的是"这段时间最难受的一段有多难受"和"已经多久没动了" ——
            // 账户快照只存当前值，所以这两个数只有曲线在才算得出来。
            PaperEquitySeries.Summary series = PaperEquitySeries.summarize(
                    paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId()));
            if (series.maxDrawdownPct() != null) {
                // 直接送 BigDecimal：值格式由 ObjectiveFactKeys.format **一处**决定
                //（≤3 位小数、整数归一成整数；Python 侧同规则）。
                // 在这里先 doubleValue() 会是"两处各决定一次格式"的第一步。
                addFact(facts, subject, ObjectiveFactKeys.PAPER_MAX_DRAWDOWN_PCT,
                        series.maxDrawdownPct(), at);
            }
            if (series.hasData()) {
                addFact(facts, subject, ObjectiveFactKeys.PAPER_FLAT_DAYS, series.flatDays(), at);
            }
            if (facts.isEmpty()) {
                return;
            }
            memoryFactService.recordObjective(strategy.getUser().getId(),
                    memoryService.currentSessionKey(strategy.getUser().getId()), facts);
        } catch (Exception e) {
            log.warn("模拟盘客观事实入账失败 strategyId={}: {}",
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

    private PaperAccount evaluateRealtimeStrategy(Long strategyId) {
        return evaluateRealtimeStrategy(strategyId, ExecutionContract.TRIGGER_EVENT);
    }

    private PaperAccount evaluateRealtimeStrategy(Long strategyId, String trigger) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        // agent 模式下**盘中不再问规则引擎**（2026-09-18 裁决，方案 A）。
        //
        // 换决策来源就是换决策者：之前这里无条件调 DSL，于是委员会当天说"不动"，
        // 盘中的 DSL 信号照样能建仓 —— 决定被覆盖了，而"agent 段"的曲线里还混着
        // DSL 触发的成交（痕迹会如实标成 rule，所以不算骗人，只是两段再也没法比较）。
        // 代价写在明处：agent 模式下账户的最新价/净值在盘中不再刷新，只在每日结算后更新一次。
        if (isAgentMode(strategy)) {
            log.debug("Realtime check skipped for strategy id={}: decision_mode=agent "
                    + "（只在日线结算时由委员会决策）", strategy.getId());
            return null;
        }

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }

        JsonNode position = buildPosition(account);
        JsonNode result = strategyClient.evaluateBarRealtime(
                strategy.getConfigJson(), strategy.getSymbol(), position);
        if (result == null) {
            log.warn("Realtime evaluateBar returned null for strategy id={}", strategy.getId());
            return account;
        }

        String barTime = textOr(result.get("bar_time"), "");
        if (barTime.isBlank()) {
            log.debug("Realtime evaluateBar missing bar_time for strategy id={}", strategy.getId());
            return account;
        }
        if (barTime.equals(account.getLastBarTime())) {
            log.debug("Realtime bar already settled for strategy id={} at {}", strategy.getId(), barTime);
            return account;
        }
        account.setLastBarTime(barTime);
        // 刻意**不**在这里写客观事实：本方法由每次价格刷新触发，
        // 在这里记会把取代链冲成一天上百个值（见 recordPaperFacts 的说明）。
        // alreadyTradedToday=false：盘中这条**就是**下单的那条路，
        // 它自己要跑的就是"能不能成交"的判断（重复成交由 dedupe 键与 T+1 守卫挡）。
        return applyBarResult(strategy, account, result, LocalDate.now(), barTime,
                ExecutionContract.SETTLEMENT_REALTIME, trigger, false);
    }

    /**
     * 这条策略现在由谁做决定。
     *
     * <p>只留一个判定入口：日线结算、盘中检查、仓位上限三处都要问同一个问题，
     * 各写一遍 {@code normalize + equals} 迟早会出现"这里算了那里没算"，
     * 而那种偏差的表现是"某条路径偷偷用了另一个决策者"。
     */
    private static boolean isAgentMode(Strategy strategy) {
        return strategy != null && ExecutionContract.DECISION_MODE_AGENT.equals(
                ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
    }

    /** 最近一次真的开了会的日子（给门控用）。读不到返回 null（Python 侧按"从没开过"处理）。 */
    private String lastBriefingDate(Long strategyId) {
        try {
            return paperTraceService == null ? null : paperTraceService.lastBriefingDate(strategyId);
        } catch (Exception e) {
            log.debug("读取上次开会日期失败 strategyId={}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    /**
     * 只写痕迹、不改账户（用于"这一轮什么都没做"的路径）。
     *
     * <p>把它单独拿出来，是因为这类分支以前全都是 `return account;` ——
     * 对调用方而言与"结算了但没动"完全一样，而它们的原因可能天差地别
     * （取数不可用 / 休市 / 涨停挡单 / 现金不足一手）。痕迹是唯一能把它们分开的地方。
     */
    private PaperAccount traceOnly(Strategy strategy, PaperAccount account, String settlementKind,
                                   String trigger, LocalDate tradeDate, String barTime,
                                   JsonNode result, String skipReason) {
        PaperTradeTrace trace = newTrace(strategy, account, settlementKind, trigger, tradeDate,
                barTime, result);
        trace.setDecision(ExecutionContract.DECISION_SKIP);
        trace.setSkipReason(ExecutionContract.normalizeSkipReason(skipReason));
        fillAccountAfter(trace, account);
        paperTraceService.record(trace);
        return account;
    }

    /**
     * 按一根 bar 结算。
     *
     * @param alreadyTradedToday 今天已经成交过（盘中那条路成交的）→ **这一节不下单，只重估**。
     *                           它把"同一天不能下两次单"这条保证从"整段跳过"收窄成"禁止下单"：
     *                           账户、净值快照、客观事实、到期回填、当日报告统统照旧。
     */
    private PaperAccount applyBarResult(Strategy strategy, PaperAccount account,
                                        JsonNode result, LocalDate tradeDate, String barTime,
                                        String settlementKind, String trigger,
                                        boolean alreadyTradedToday) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        JsonNode risk = config.get("risk");
        // 费率一律走 BigDecimal：它们是"乘在钱上的数"，用 double 相乘会把二进制误差
        // 带进成交额，而净值恒等式要逐笔对上账（见 Money）。
        BigDecimal commission = rate(field(risk, "commission_pct"), DEFAULT_COMMISSION_PCT);
        BigDecimal slippage = rate(field(risk, "slippage_pct"), DEFAULT_SLIPPAGE_PCT);
        // A 股卖出印花税（0.05%）与整手规则：科创板 688/689 起购 200 股，其余默认 100 股/手
        BigDecimal stampTax = rate(field(risk, "stamp_tax_pct"), DEFAULT_STAMP_TAX_PCT);
        BigDecimal lot = Money.price(numberOr(field(risk, "lot_size"),
                defaultLotFor(strategy.getSymbol())));

        JsonNode positionConfig = config.get("position");
        String positionType = textOr(field(positionConfig, "type"), DEFAULT_POSITION_TYPE);
        BigDecimal sizePct = rate(field(positionConfig, "size_pct"), DEFAULT_SIZE_PCT);

        // agent 模式下"动多大"由模型给（size_fraction ∈ (0,1]），但**上限归调用方**——
        // trading_decision.py 的契约原话就是"上限由调用方（结算侧）裁决，不在这里放大"。
        // 所以这里把模型的比例当作 percent 语义的 size_pct 用，再被策略配置的上限夹一次：
        // 用户设的仓位约束永远压在模型之上，模型只能在它之内决定大小。
        //
        // 不认"非 agent 响应里的 size_fraction"：只有响应自己的指纹声明了 agent 才算数
        // （口径以 Python 为准，读不到才退回策略声明的模式），否则规则那条路的语义一点不变。
        BigDecimal agentSize = agentSizeFraction(strategy, result);
        if (agentSize != null && agentSize.signum() > 0) {
            BigDecimal cap = "percent".equals(positionType) ? sizePct : BigDecimal.ONE;  // full → 上限 100%
            if (agentSize.compareTo(cap) > 0) {
                // 夹住了就说话：否则"配置一直是绑定约束"这件事只会表现为
                // "模型的仓位判断好像从来不起作用"，而这正是最容易被误读成模型无能的情形。
                log.warn("Agent asked for size_fraction={} but strategy id={} caps it at {}; "
                        + "using the cap", agentSize, strategy.getId(), cap);
            }
            sizePct = agentSize.min(cap);
            positionType = "percent";
        }

        String signal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        BigDecimal price = Money.price(priceOf(result));

        // 痕迹在**结算开始前**就建好（含账户的"结算前"状态与这根 bar 的证据）：
        // 这样每一条提前返回的分支都只是补上"结论 + 原因"，不会有哪条路悄悄漏掉痕迹。
        PaperTradeTrace trace = newTrace(strategy, account, settlementKind, trigger, tradeDate,
                barTime, result);
        trace.setSignal(signal.isEmpty() ? null : signal);
        trace.setMatchedConditions(joinMatchedConditions(result.get("matched_conditions")));

        // 没有可用价格就**不结算**（原样返回，什么都不改）。
        //
        // 这里以前会带着 price=0 继续走到底部的 updateEquityAndHighWatermark，
        // 于是"净值 = 现金 + 股数 × 0" = 现金 —— 一次瞬时取数失败就把持仓市值抹掉，
        // 而净值现在还会写进客观事实通道被长期记住。跳过才是正确行为：
        // 宁可不结算（并留下 skip 原因），也不要写一个错的净值。
        if (price == null || price.signum() <= 0) {
            String reason = ExecutionContract.normalizeSkipReason(
                    result.has("error") ? ExecutionContract.SKIP_DATA_UNAVAILABLE
                            : ExecutionContract.SKIP_INVALID_PRICE);
            log.warn("Skipping paper settlement for strategy id={} on {}: no usable price "
                    + "(skip_reason={})", strategy.getId(), tradeDate, reason);
            return skip(account, trace, reason);
        }

        String reason = joinMatchedConditions(result.get("matched_conditions"));
        BigDecimal cash = orZero(account.getCash());

        // 今天已经成交过：**只重估，不下单**，并且这一节照旧走完全程
        // （痕迹、账户、净值快照、客观事实、到期回填、当日报告都由调用方接着做完）。
        //
        // 为什么不能写成"规则没成立"：那天真正发生的事情是"已经动过了"。
        // 一个只有成交那天才有的原因被写成常态原因，审计与统计会同时失真 ——
        // 而这两个字段（decision / skip_reason）正是痕迹存在的理由。
        if (alreadyTradedToday) {
            trace.setDecision(ExecutionContract.DECISION_SKIP);
            trace.setSkipReason(ExecutionContract.SKIP_ALREADY_TRADED_TODAY);
            // 决策来源取**策略声明的模式**，而不是这份响应的指纹：这一节不是一次决策，
            // 而这份响应只是为了拿当日 bar（上面刻意走的规则端点）。
            // 按策略模式落库，才能让它和当天的其余记录落在同一段曲线上。
            trace.setDecisionMode(ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
            updateEquityAndHighWatermark(account, price);
            account.setLastPrice(price);
            // 刻意**不**覆盖 lastSignal：那应当是当天真正做出的那个信号（盘中成交时写的），
            // 而不是这一节用来估值的规则端点返回值。
            account.setLastEvalAt(LocalDateTime.now());
            fillAccountAfter(trace, account);
            paperTraceService.record(trace);
            account = paperAccountRepository.save(account);
            strategy.setLastPaperEvalAt(account.getLastEvalAt());
            strategyRepository.save(strategy);
            return account;
        }

        PaperTrade trade = null;
        if ("buy".equals(signal) && !isHolding(account)) {
            BigDecimal fill = Money.price(price.multiply(BigDecimal.ONE.add(slippage)));
            if (fill.signum() <= 0) {
                log.warn("Invalid fill price for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            }

            BigDecimal budget = "percent".equals(positionType)
                    ? Money.amount(cash.multiply(sizePct)) : cash;
            if (budget.signum() <= 0) {
                log.warn("No cash available for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }

            // 整手取整：按 (预算 / (成交价*(1+佣金)*手数)) **向下**取整手。
            // 用 divide(...,0,DOWN) 而不是 floor(double)：整手这件事的判定不能靠浮点。
            BigDecimal perLot = fill.multiply(BigDecimal.ONE.add(commission)).multiply(lot);
            BigDecimal shares = perLot.signum() <= 0 ? BigDecimal.ZERO
                    : budget.divide(perLot, 0, RoundingMode.DOWN).multiply(lot);
            while (shares.signum() > 0) {
                BigDecimal turnover = shares.multiply(fill);
                if (turnover.add(commissionFee(turnover, commission)).compareTo(cash) <= 0) {
                    break;
                }
                shares = shares.subtract(lot);
            }
            if (shares.signum() <= 0) {
                // 这一条最容易"什么都没发生却没人知道"：涨得越高的票越容易撞上
                //（茅台一手 ~14 万 > 10 万本金），而且账户数字一动不动。
                log.warn("Not enough cash for even one lot on strategy id={} (fill={})",
                        strategy.getId(), fill);
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT);
            }
            BigDecimal turnover = shares.multiply(fill);
            BigDecimal fee = commissionFee(turnover, commission);
            BigDecimal cashAfter = Money.amount(cash.subtract(turnover).subtract(fee));
            if (cashAfter.signum() < 0) {
                log.warn("Buy would exceed cash on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }
            account.setShares(Money.price(shares));
            account.setAvgCost(fill);
            account.setCash(cashAfter);
            account.setHighWatermark(fill);
            account.setLastBuyBar(barTime);   // T+1 同交易日卖出守卫（realtime 按 bar 时间判断）
            trade = buildTrade(strategy, account, tradeDate, "BUY", fill, shares, reason);
        } else if ("sell".equals(signal) && isHolding(account)) {
            // T+1：A 股当日买入当日不可卖（barTime 带日期时按自然日判断）
            boolean t1Blocked = barTime != null && account.getLastBuyBar() != null
                    && sameTradingDay(barTime, account.getLastBuyBar());
            BigDecimal fill = Money.price(price.multiply(BigDecimal.ONE.subtract(slippage)));
            if (t1Blocked) {
                log.debug("Paper: T+1 blocks sell on same trading day (bar {}) for strategy id={}",
                        barTime, strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_T1_BLOCKED);
            } else if (fill.signum() <= 0) {
                log.warn("Invalid fill price for sell on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            } else {
                BigDecimal shares = Money.price(account.getShares());
                BigDecimal turnover = shares.multiply(fill);
                BigDecimal fee = commissionFee(turnover, commission);
                BigDecimal stampAmount = Money.amount(turnover.multiply(stampTax));
                BigDecimal cashAfter = Money.amount(cash.add(turnover).subtract(fee).subtract(stampAmount));
                account.setCash(cashAfter);
                account.setShares(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                account.setAvgCost(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                account.setHighWatermark(BigDecimal.ZERO.setScale(Money.PRICE_SCALE));
                account.setLastBuyBar(null);
                trade = buildTrade(strategy, account, tradeDate, "SELL", fill, shares, reason);
            }
        } else if ("buy".equals(signal) || "sell".equals(signal)) {
            // 信号与账户状态对不上：已持仓时收到买入信号，或空仓时收到卖出信号。
            //
            // 结构上不该发生（引擎按 position 决定评估 entry 还是 exit），所以它一旦出现
            // 往往意味着并发或契约变更。**不能**把它写成"规则没成立"（那是假话），
            // 也不能继续往下走（下行分支按 signal 各自处理，会走到 else 把它标成 hold）。
            log.warn("Paper: signal={} contradicts account state (shares={}) for strategy id={}",
                    signal, account.getShares(), strategy.getId());
            return skip(account, trace, ExecutionContract.SKIP_STATE_MISMATCH);
        }

        updateEquityAndHighWatermark(account, price);
        account.setLastPrice(price);
        account.setLastSignal(signal);
        account.setLastEvalAt(LocalDateTime.now());

        if (trade != null) {
            trace.setDecision(trade.getSide().toLowerCase(Locale.ROOT));
            fillAccountAfter(trace, account);
            PaperTradeTrace savedTrace = paperTraceService.record(trace);
            if (savedTrace != null) {
                trade.setTraceId(savedTrace.getId());
            }
            paperTradeRepository.save(trade);
        } else {
            // 信号是 hold：这一根 bar 的结论是"按策略不动"。
            // 原因由**引擎**给（它有指标值）：`rule_not_met` 与 `warmup` 是两件事，
            // 前者是"验证过、规则没动"，后者是"指标还没算出来、这段没有样本"。
            trace.setDecision(ExecutionContract.DECISION_SKIP);
            trace.setSkipReason(skipReasonOf(result, ExecutionContract.SKIP_RULE_NOT_MET));
            fillAccountAfter(trace, account);
            paperTraceService.record(trace);
        }
        account = paperAccountRepository.save(account);

        strategy.setLastPaperEvalAt(account.getLastEvalAt());
        strategyRepository.save(strategy);
        return account;
    }

    /**
     * 信号触发了、但结算路径把它挡下了：把原因写进痕迹，账户一个字段都不改。
     *
     * <p>这是"7 条静默 return"的统一改写：以前它们对调用方完全不可见，
     * 现在每一条都会留下一行带原因的痕迹。
     */
    private PaperAccount skip(PaperAccount account, PaperTradeTrace trace, String skipReason) {
        trace.setDecision(ExecutionContract.DECISION_SKIP);
        trace.setSkipReason(ExecutionContract.normalizeSkipReason(skipReason));
        fillAccountAfter(trace, account);
        paperTraceService.record(trace);
        return account;
    }

    /** 引擎回传的 `skip_reason`（它有指标值，比 Java 猜得准）；缺失或未知时用兜底值。 */
    private static String skipReasonOf(JsonNode result, String fallback) {
        String raw = result == null ? null : textOrStatic(result.get("skip_reason"), "");
        return ExecutionContract.normalizeSkipReason(raw == null || raw.isBlank() ? fallback : raw);
    }

    private static String textOrStatic(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    /**
     * 建一条痕迹：identity + 证据 + 账户的**结算前**状态。
     *
     * <p>证据全部取自 Python 回传的快照（`snapshot` / `fingerprint` / `fill_basis`）——
     * Java 不自己组装指标值，也不自己推口径：原则 4（单一真相源）。
     */
    private PaperTradeTrace newTrace(Strategy strategy, PaperAccount account, String settlementKind,
                                     String trigger, LocalDate tradeDate, String barTime,
                                     JsonNode result) {
        JsonNode snapshot = result == null ? null : result.get("snapshot");
        JsonNode fingerprint = result == null ? null : result.get("fingerprint");
        JsonNode bar = snapshot == null ? null : snapshot.get("bar");

        PaperTradeTrace trace = PaperTradeTrace.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .settlementKind(ExecutionContract.normalize(settlementKind,
                        ExecutionContract.SETTLEMENT_KINDS))
                .trigger(ExecutionContract.normalize(trigger, ExecutionContract.TRACE_TRIGGERS))
                .tradeDate(tradeDate)
                .barTime(barTime)
                .symbol(strategy.getSymbol())
                .repeatCount(1)
                .build();

        trace.setBarDate(textOrStatic(bar == null ? null : bar.get("date"), null));
        trace.setFillBasis(ExecutionContract.normalize(
                result == null ? null : textOrStatic(result.get("fill_basis"), null),
                ExecutionContract.FILL_BASES));
        trace.setBarClose(Money.price(result == null ? null : numberOrNull(result.get("price"))));
        trace.setEngineVersion(textOrStatic(fingerprint == null ? null : fingerprint.get("engine_version"), null));
        trace.setAdjustMode(ExecutionContract.normalize(
                textOrStatic(fingerprint == null ? null : fingerprint.get("adjust_mode"), null),
                ExecutionContract.ADJUST_MODES));
        trace.setMoneyPolicyVersion(fingerprint == null ? Money.POLICY_VERSION
                : intOr(fingerprint.get("money_policy_version"), Money.POLICY_VERSION));
        trace.setSnapshotJson(snapshot == null || snapshot.isNull() ? null : snapshot.toString());
        trace.setSnapshotSchemaVersion(snapshot == null || snapshot.isNull() ? null
                : intOr(snapshot.get("schema_version"), null));
        // 决策来源与 agent 成本：从**响应**里读（Python 是口径的唯一真相源），
        // 读不到才退回策略上声明的模式 —— 但绝不"默认成 rule"（见 normalizeDecisionMode）。
        trace.setDecisionMode(ExecutionContract.normalizeDecisionMode(
                textOrStatic(fingerprint == null ? null : fingerprint.get("decision_mode"),
                        strategy.getDecisionMode())));
        JsonNode committee = result == null ? null : result.get("committee");
        if (committee != null && committee.isObject()) {
            trace.setAgentLlmCalls(intOr(committee.get("llm_calls"), null));
            trace.setAgentTokens(intOr(committee.get("total_tokens"), null));
        }
        fillAccountBefore(trace, account);
        return trace;
    }

    /** 账户的**结算前**状态：痕迹要能回答"这一笔是从什么状态变成什么状态"。 */
    private void fillAccountBefore(PaperTradeTrace trace, PaperAccount account) {
        if (account == null) {
            return;
        }
        trace.setCashBefore(Money.amount(account.getCash()));
        trace.setSharesBefore(Money.price(account.getShares()));
        trace.setAvgCostBefore(Money.price(account.getAvgCost()));
        trace.setEquityBefore(Money.equity(account.getEquity()));
    }

    private void fillAccountAfter(PaperTradeTrace trace, PaperAccount account) {
        if (account == null) {
            return;
        }
        trace.setCashAfter(Money.amount(account.getCash()));
        trace.setSharesAfter(Money.price(account.getShares()));
        trace.setAvgCostAfter(Money.price(account.getAvgCost()));
        trace.setEquityAfter(Money.equity(account.getEquity()));
    }

    private static Double numberOrNull(JsonNode node) {
        return node == null || !node.isNumber() ? null : node.asDouble();
    }

    private static Integer intOr(JsonNode node, Integer fallback) {
        return node == null || !node.isNumber() ? fallback : node.asInt();
    }

    private void initializeAccount(PaperAccount account, Strategy strategy) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        BigDecimal initialCapital = Money.amount(numberOr(config.get("initial_capital"),
                DEFAULT_INITIAL_CAPITAL));

        if (account.getUser() == null) {
            account.setUser(strategy.getUser());
        }
        if (account.getStrategy() == null) {
            account.setStrategy(strategy);
        }
        if (account.getInitialCapital() == null) {
            account.setInitialCapital(initialCapital);
        }
        if (account.getCash() == null) {
            account.setCash(account.getInitialCapital());
        }
        if (account.getEquity() == null) {
            account.setEquity(account.getCash());
        }
        if (account.getShares() == null) {
            account.setShares(zeroPrice());
        }
        if (account.getAvgCost() == null) {
            account.setAvgCost(zeroPrice());
        }
        if (account.getHighWatermark() == null) {
            account.setHighWatermark(zeroPrice());
        }
    }

    private JsonNode buildPosition(PaperAccount account) {
        if (!isHolding(account)) {
            return null;
        }
        ObjectNode position = mapper.createObjectNode();
        position.put("entry_price", orZero(account.getAvgCost()));
        position.put("shares", orZero(account.getShares()));
        position.put("high_watermark", orZero(account.getHighWatermark()));
        return position;
    }

    /**
     * 净值与最高水位：**唯一**写这两个字段的地方。
     *
     * <p>恒等式 {@code equity = cash + shares × price} 在这里按 {@code Money} 的口径量化（2 位）——
     * 这是"逐笔对上账"的落点：不是"看起来差不多"，而是相等。
     */
    private void updateEquityAndHighWatermark(PaperAccount account, BigDecimal price) {
        BigDecimal shares = orZero(account.getShares());
        BigDecimal cash = orZero(account.getCash());
        account.setEquity(Money.equity(cash.add(shares.multiply(price))));

        if (shares.signum() > 0) {
            BigDecimal currentWatermark = account.getHighWatermark() == null
                    ? (account.getAvgCost() == null ? price : account.getAvgCost())
                    : account.getHighWatermark();
            if (price.compareTo(currentWatermark) > 0) {
                account.setHighWatermark(price);
            }
        }
    }

    private PaperTrade buildTrade(Strategy strategy, PaperAccount account, LocalDate tradeDate,
                                  String side, BigDecimal fill, BigDecimal shares, String reason) {
        return PaperTrade.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .tradeDate(tradeDate)
                .symbol(strategy.getSymbol())
                .side(side)
                .price(fill)
                .shares(Money.price(shares))
                .amount(Money.amount(shares.multiply(fill)))
                .reason(reason)
                .build();
    }

    private boolean isHolding(PaperAccount account) {
        return account.getShares() != null && account.getShares().signum() > 0;
    }

    /**
     * A 股佣金：费率>0 时按 {@code max(成交额 × 费率, 5 元)} 收取；费率=0（测试配置）不收。
     *
     * <p>5 元下限对**小额**成交是隐形门槛：一次只买 500 元时按 0.1% 本该收 0.5 元、实际收 5 元，
     * 单边成本就是 1%。这是真实券商的规则，所以照做（不"优化"掉），
     * 但它意味着小账户的每一笔都更贵 —— 这一点在报告与回测里都应当看得见。
     */
    private BigDecimal commissionFee(BigDecimal turnover, BigDecimal commission) {
        if (commission.signum() <= 0 || turnover.signum() <= 0) {
            return BigDecimal.ZERO.setScale(Money.AMOUNT_SCALE);
        }
        return Money.amount(turnover.multiply(commission)
                .max(BigDecimal.valueOf(DEFAULT_MIN_COMMISSION_YUAN)));
    }

    /** A 股整手：科创板 688/689 起购 200 股/手，其余 100 股/手（可用 risk.lot_size 覆盖） */
    private double defaultLotFor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        return (s.startsWith("688") || s.startsWith("689")) ? 200.0 : 100.0;
    }

    private static BigDecimal zeroPrice() {
        return BigDecimal.ZERO.setScale(Money.PRICE_SCALE);
    }

    private static BigDecimal orZero(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    /** 配置里的费率/比例：百分数 → 小数（0.1 → 0.001）。缺省值走同一个入口，不两处各写一遍。 */
    private static BigDecimal rate(JsonNode node, double defaultPct) {
        return Money.ratePct(numberOr(node, defaultPct)).movePointLeft(2);
    }

    /**
     * agent 决策里的仓位比例（{@code size_fraction}，本来就是小数，**不再除以 100**）。
     *
     * <p>两条不认账的规矩，都是为了"宁可不动，也不要按错的仓位动"：
     * 只有响应自己的指纹声明 {@code decision_mode=agent} 才认（读不到指纹才退回策略声明的模式），
     * 且值必须是 (0,1] 的数字 —— 读不懂就返回 {@code null}，让配置的仓位口径原样生效。
     */
    private static BigDecimal agentSizeFraction(Strategy strategy, JsonNode result) {
        if (result == null || !result.isObject()) {
            return null;
        }
        String mode = ExecutionContract.normalizeDecisionMode(textOrStatic(
                result.path("fingerprint").path("decision_mode"),
                strategy == null ? null : strategy.getDecisionMode()));
        if (!ExecutionContract.DECISION_MODE_AGENT.equals(mode)) {
            return null;
        }
        BigDecimal size = Money.ratePct(numberOr(result.get("size_fraction"), null));
        return size != null && size.signum() > 0 && size.compareTo(BigDecimal.ONE) <= 0 ? size : null;
    }

    /**
     * 数字字段：不是数字就用缺省值（JSON 里可能是 null / "N/A" / 字符串）。
     *
     * <p>三元表达式里的 {@code fallback} **必须显式装箱**（写成 {@code Double.valueOf(...)}）：
     * 另一支是基本类型 {@code double}，二元数值提升会把 {@code Double} 拆箱，
     * 于是"缺省值传 null"的调用在**取值命中缺省那一支**时抛 NPE
     * （实测：agent 响应里没有 {@code size_fraction} 时结算整笔失败）。
     * 这种坑只在"确实走了缺省分支"的那天才炸，平时看不出任何异常。
     */
    private static Double numberOr(JsonNode node, Double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return Double.valueOf(node.asDouble());
    }

    /** bar 时间形如 "yyyy-MM-dd HH:mm:ss"，按自然日前 10 位判断是否同一交易日 */
    private static boolean sameTradingDay(String barA, String barB) {
        if (barA == null || barB == null || barA.length() < 10 || barB.length() < 10) {
            return false;
        }
        return barA.substring(0, 10).equals(barB.substring(0, 10));
    }

    private JsonNode parseConfig(String configJson) {
        try {
            if (configJson == null || configJson.isBlank()) {
                return mapper.createObjectNode();
            }
            return mapper.readTree(configJson);
        } catch (Exception e) {
            log.warn("Failed to parse strategy config JSON: {}", e.getMessage());
            return mapper.createObjectNode();
        }
    }

    private double doubleOr(JsonNode node, double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return node.asDouble();
    }

    private String textOr(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    private JsonNode field(JsonNode node, String name) {
        return node == null ? null : node.get(name);
    }

    private String joinMatchedConditions(JsonNode node) {
        if (node == null || !node.isArray()) {
            return "";
        }
        List<String> conditions = new ArrayList<>();
        for (JsonNode condition : node) {
            if (!condition.isNull()) {
                conditions.add(condition.asText());
            }
        }
        return String.join(",", conditions);
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }

    private Strategy loadStrategy(User user, Long strategyId) {
        return strategyRepository.findByIdAndUserId(strategyId, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
    }
}
