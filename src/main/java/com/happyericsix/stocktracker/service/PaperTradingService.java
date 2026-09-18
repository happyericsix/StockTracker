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
    private final ObjectMapper mapper = new ObjectMapper();
    private final TransactionTemplate transactionTemplate;

    /**
     * 单一构造函数：**不要为了"测试方便"再加几个重载**。
     * Spring 遇到多个构造函数且没有 {@code @Autowired} 时会去找无参构造，直接起不来
     * （实测踩过：三个重载 → {@code NoSuchMethodException: <init>()} → 整个应用上下文挂掉）。
     * 可选依赖（报告、验证）允许为 null，用 null 判断兜住，而不是靠另一个构造函数。
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
                               StrategyService strategyService) {
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

        if (paperTradeRepository.existsByStrategyIdAndTradeDate(strategy.getId(), today)) {
            log.debug("Paper settlement already ran for strategy id={} on {}", strategy.getId(), today);
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
        JsonNode result = strategyClient.evaluateBar(
                strategy.getConfigJson(), strategy.getSymbol(), today.toString(), position);
        if (result == null) {
            // 之前这里只打一行日志：那天为什么没结算，除了翻日志没有别的办法查。
            log.warn("Strategy evaluateBar returned null for strategy id={}", strategy.getId());
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
                ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.TRIGGER_CRON);
        recordEquitySnapshot(strategy, settled, result, today);
        recordPaperFacts(strategy, settled, today);
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
            // 收益率的口径只该有一处：初始本金是账户自己的字段，不让调用方各算一遍
            Double initial = account.getInitialCapital();
            Double equity = account.getEquity();
            if (initial != null && initial != 0.0 && equity != null) {
                addFact(facts, subject, ObjectiveFactKeys.PAPER_RETURN_PCT,
                        (equity - initial) / initial * 100.0, at);
            }
            addFact(facts, subject, ObjectiveFactKeys.PAPER_LAST_EVAL_AT,
                    at.withNano(0).toString(), at);
            // 由净值序列算出来的两个量：**回撤**与**连续空仓天数**。
            // 它们回答的是"这段时间最难受的一段有多难受"和"已经多久没动了" ——
            // 账户快照只存当前值，所以这两个数只有曲线在才算得出来。
            PaperEquitySeries.Summary series = PaperEquitySeries.summarize(
                    paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId()));
            if (series.maxDrawdownPct() != null) {
                // 走 doubleValue()：事实通道的值格式有统一口径（≤3 位小数、整数归一成整数），
                // 由 ObjectiveFactKeys.format 一处决定，Python 侧同规则。
                // 直接塞 BigDecimal 会绕过它，于是同一个量在两边写成 "-25.0000" 与 "-25" ——
                // 那正是"值没变、取代链却多一条"的来源。
                addFact(facts, subject, ObjectiveFactKeys.PAPER_MAX_DRAWDOWN_PCT,
                        series.maxDrawdownPct().doubleValue(), at);
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
        return applyBarResult(strategy, account, result, LocalDate.now(), barTime,
                ExecutionContract.SETTLEMENT_REALTIME, trigger);
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

    private PaperAccount applyBarResult(Strategy strategy, PaperAccount account,
                                        JsonNode result, LocalDate tradeDate, String barTime,
                                        String settlementKind, String trigger) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        JsonNode risk = config.get("risk");
        double commission = doubleOr(field(risk, "commission_pct"), DEFAULT_COMMISSION_PCT) / 100.0;
        double slippage = doubleOr(field(risk, "slippage_pct"), DEFAULT_SLIPPAGE_PCT) / 100.0;
        // A 股卖出印花税（0.05%）与整手规则：科创板 688/689 起购 200 股，其余默认 100 股/手
        double stampTax = doubleOr(field(risk, "stamp_tax_pct"), DEFAULT_STAMP_TAX_PCT) / 100.0;
        double lot = doubleOr(field(risk, "lot_size"), defaultLotFor(strategy.getSymbol()));

        JsonNode positionConfig = config.get("position");
        String positionType = textOr(field(positionConfig, "type"), DEFAULT_POSITION_TYPE);
        double sizePct = doubleOr(field(positionConfig, "size_pct"), DEFAULT_SIZE_PCT) / 100.0;

        String signal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        double price = doubleOr(result.get("price"), 0.0);

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
        if (price <= 0.0) {
            String reason = ExecutionContract.normalizeSkipReason(
                    result.has("error") ? ExecutionContract.SKIP_DATA_UNAVAILABLE
                            : ExecutionContract.SKIP_INVALID_PRICE);
            log.warn("Skipping paper settlement for strategy id={} on {}: no usable price "
                    + "(skip_reason={})", strategy.getId(), tradeDate, reason);
            return skip(account, trace, reason);
        }

        String reason = joinMatchedConditions(result.get("matched_conditions"));
        double cash = account.getCash() == null ? 0.0 : account.getCash();

        PaperTrade trade = null;
        if ("buy".equals(signal) && !isHolding(account)) {
            double fill = price * (1.0 + slippage);
            if (fill <= 0.0) {
                log.warn("Invalid fill price for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            }

            double budget = "percent".equals(positionType) ? cash * sizePct : cash;
            if (budget <= 0.0) {
                log.warn("No cash available for buy on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }

            // 整手取整：按 (预算 / (成交价*(1+佣金)*手数)) 向下取整手
            double shares = Math.floor(budget / (fill * (1.0 + commission) * lot)) * lot;
            while (shares > 0) {
                double fee = commissionFee(shares * fill, commission);
                if (shares * fill + fee <= cash) {
                    break;
                }
                shares -= lot;
            }
            if (shares <= 0.0) {
                // 这一条最容易"什么都没发生却没人知道"：涨得越高的票越容易撞上
                //（茅台一手 ~14 万 > 10 万本金），而且账户数字一动不动。
                log.warn("Not enough cash for even one lot on strategy id={} (fill={})", strategy.getId(), fill);
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT);
            }
            double fee = commissionFee(shares * fill, commission);
            double cashAfter = cash - shares * fill - fee;
            if (cashAfter < 0.0) {
                log.warn("Buy would exceed cash on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INSUFFICIENT_CASH);
            }
            account.setShares(shares);
            account.setAvgCost(fill);
            account.setCash(cashAfter);
            account.setHighWatermark(fill);
            account.setLastBuyBar(barTime);   // T+1 同交易日卖出守卫（realtime 按 bar 时间判断）
            trade = buildTrade(strategy, account, tradeDate, "BUY", fill, shares, reason);
        } else if ("sell".equals(signal) && isHolding(account)) {
            // T+1：A 股当日买入当日不可卖（barTime 带日期时按自然日判断）
            boolean t1Blocked = barTime != null && account.getLastBuyBar() != null
                    && sameTradingDay(barTime, account.getLastBuyBar());
            double fill = price * (1.0 - slippage);
            if (t1Blocked) {
                log.debug("Paper: T+1 blocks sell on same trading day (bar {}) for strategy id={}",
                        barTime, strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_T1_BLOCKED);
            } else if (fill <= 0.0) {
                log.warn("Invalid fill price for sell on strategy id={}", strategy.getId());
                return skip(account, trace, ExecutionContract.SKIP_INVALID_PRICE);
            } else {
                double shares = account.getShares();
                double fee = commissionFee(shares * fill, commission);
                double stampAmount = shares * fill * stampTax;
                double cashAfter = cash + shares * fill - fee - stampAmount;
                account.setCash(cashAfter);
                account.setShares(0.0);
                account.setAvgCost(0.0);
                account.setHighWatermark(0.0);
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
        double initialCapital = doubleOr(config.get("initial_capital"), DEFAULT_INITIAL_CAPITAL);

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
            account.setShares(0.0);
        }
        if (account.getAvgCost() == null) {
            account.setAvgCost(0.0);
        }
        if (account.getHighWatermark() == null) {
            account.setHighWatermark(0.0);
        }
    }

    private JsonNode buildPosition(PaperAccount account) {
        if (!isHolding(account)) {
            return null;
        }
        ObjectNode position = mapper.createObjectNode();
        position.put("entry_price", account.getAvgCost() == null ? 0.0 : account.getAvgCost());
        position.put("shares", account.getShares() == null ? 0.0 : account.getShares());
        position.put("high_watermark", account.getHighWatermark() == null ? 0.0 : account.getHighWatermark());
        return position;
    }

    private void updateEquityAndHighWatermark(PaperAccount account, double price) {
        double shares = account.getShares() == null ? 0.0 : account.getShares();
        double cash = account.getCash() == null ? 0.0 : account.getCash();
        account.setEquity(cash + shares * price);

        if (shares > 0.0) {
            double currentWatermark = account.getHighWatermark() == null
                    ? (account.getAvgCost() == null ? price : account.getAvgCost())
                    : account.getHighWatermark();
            if (price > currentWatermark) {
                account.setHighWatermark(price);
            }
        }
    }

    private PaperTrade buildTrade(Strategy strategy, PaperAccount account, LocalDate tradeDate,
                                  String side, double fill, double shares, String reason) {
        return PaperTrade.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .tradeDate(tradeDate)
                .symbol(strategy.getSymbol())
                .side(side)
                .price(fill)
                .shares(shares)
                .amount(shares * fill)
                .reason(reason)
                .build();
    }

    private boolean isHolding(PaperAccount account) {
        return account.getShares() != null && account.getShares() > 0.0;
    }

    /** A 股佣金：费率>0 时按 max(成交额*费率, 5元) 收取；费率=0（测试配置）不收 */
    private double commissionFee(double turnover, double commission) {
        if (commission <= 0.0 || turnover <= 0.0) {
            return 0.0;
        }
        return Math.max(turnover * commission, DEFAULT_MIN_COMMISSION_YUAN);
    }

    /** A 股整手：科创板 688/689 起购 200 股/手，其余 100 股/手（可用 risk.lot_size 覆盖） */
    private double defaultLotFor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        return (s.startsWith("688") || s.startsWith("689")) ? 200.0 : 100.0;
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
