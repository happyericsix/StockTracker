package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.support.TransactionTemplate;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.stream.Collectors;

/**
 * 净值快照补账（2026-11-18）：把"后端 15:30 没活着"造成的曲线缺口补上，
 * <b>只补曲线、绝不补交易</b>——错过就是错过，不假装当时决策过。
 *
 * <h3>为什么必须有它（原"缺口不补"政策的失效场景）</h3>
 * 快照只在日线结算（15:30 cron）写；既有的补跑（{@code retryFailedDailySettlements}）
 * 只认"当天有 no_bar/data_unavailable 痕迹"的策略。后端整个没跑的那天<b>连痕迹都没有</b>，
 * 任何既有机制都判不到它——缺口永久缺失并随停机天数累积。本地 dev（服务只在开发时
 * 开着）几乎必然踩中；线上容器重启窗口同理。
 *
 * <h3>两条回填路，证据等级不同（写在 {@code source} 列上）</h3>
 * <ol>
 *   <li><b>trace_backfill（精确）</b>：当天有日线痕迹但缺快照（2026-10-15 前快照
 *       独立事务失败留下的老账）——equityAfter/cashAfter/sharesAfter 是结算亲笔数字，
 *       照抄即可。无窗口限制，一次性回填全部老账。</li>
 *   <li><b>catchup（估算）</b>：连痕迹都没有的日子（后端没跑）——从最近的已知状态
 *       （快照或痕迹）出发，按成交流水推进现金/股数，用历史收盘价重估净值。
 *       窗口限最近 N 个交易日（防长停机后一次性拉爆），费用按配置佣金率估算
 *       （有痕迹/快照的日子会对齐回真实值，漂移只可能发生在无痕迹的成交日，
 *       而后端没跑的日子本来就不可能有成交——闭环）。</li>
 * </ol>
 *
 * <h3>触发点</h3>
 * 启动时（ApplicationReady，异步，fail-open）+ 每交易日 20:30（终轮重试之后）
 * + 内部接口手动触发（部署修复后运维补账）。全部幂等：(strategy, tradeDate)
 * 唯一约束 + 已有即跳过，重复跑不会多一行。
 */
@Service
public class PaperEquityBackfillService {

    private static final Logger log = LoggerFactory.getLogger(PaperEquityBackfillService.class);

    /** 补算行的来源标记（与 {@link PaperEquitySnapshot#setSource} 的取值约定一致）。 */
    static final String SOURCE_TRACE_BACKFILL = "trace_backfill";
    static final String SOURCE_CATCHUP = "catchup";

    /** 配置缺失时的佣金率兜底（与结算侧 DEFAULT_COMMISSION_PCT 同量级：0.1%）。 */
    private static final BigDecimal DEFAULT_COMMISSION = new BigDecimal("0.001");

    private static final DateTimeFormatter DAY = DateTimeFormatter.ofPattern("yyyy-MM-dd");

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperTradeTraceRepository paperTradeTraceRepository;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private final StockDataGateway stockDataGateway;
    private final TransactionTemplate transactionTemplate;
    private final ObjectMapper objectMapper;

    /** 补算窗口（交易日）：只补最近这么多天，长停机后的深洞分批消化。 */
    @Value("${paper.equity-backfill.max-trading-days:15}")
    private int maxTradingDays;

    @Value("${paper.equity-backfill.enabled:true}")
    private boolean enabled;

    public PaperEquityBackfillService(StrategyRepository strategyRepository,
                                      PaperAccountRepository paperAccountRepository,
                                      PaperTradeRepository paperTradeRepository,
                                      PaperTradeTraceRepository paperTradeTraceRepository,
                                      PaperEquitySnapshotRepository paperEquitySnapshotRepository,
                                      StockDataGateway stockDataGateway,
                                      TransactionTemplate transactionTemplate,
                                      ObjectMapper objectMapper) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.paperTradeTraceRepository = paperTradeTraceRepository;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
        this.stockDataGateway = stockDataGateway;
        this.transactionTemplate = transactionTemplate;
        this.objectMapper = objectMapper;
    }

    /** 启动补账：异步跑，失败只记日志——补账是增强功能，不许拖慢或弄崩启动。 */
    @Async("taskExecutor")
    @EventListener(ApplicationReadyEvent.class)
    public void onStartup() {
        try {
            log.info("启动净值补账：{}", catchUpAll());
        } catch (Exception e) {   // noqa: BLE001 —— fail-open
            log.error("启动净值补账失败：{}", e.getMessage());
        }
    }

    /** 全量补账入口（定时/手动/启动共用）。返回人话摘要（给日志与运维端点）。 */
    public String catchUpAll() {
        if (!enabled) {
            return "净值补账已关闭（paper.equity-backfill.enabled=false）";
        }
        int filled = 0;
        int failures = 0;
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        for (Strategy strategy : strategies) {
            try {
                filled += backfillStrategy(strategy);
            } catch (Exception e) {   // noqa: BLE001 —— 单策略失败不影响其他策略
                failures++;
                log.error("净值补账失败 strategy id={} {}: {}", strategy.getId(),
                        strategy.getSymbol(), e.getMessage());
            }
        }
        String summary = String.format("净值补账：%d 只模拟盘策略，补 %d 格，失败 %d 只",
                strategies.size(), filled, failures);
        log.info(summary);
        return summary;
    }

    /**
     * 单策略补账（自带事务：一次补账一个事务，写失败整体回滚，不留半截）。
     *
     * <p>account 引用刻意在事务内用 {@code getReferenceById} 取代理：老账户行的
     * version 列为 NULL（乐观锁字段后加的，历史行没回填），事务外加载的 detached
     * 实体挂进快照保存会被 Hibernate 以"version 未初始化"拒绝（2026-11-18 实测
     * 5 只策略全因此失败）；代理只持主键，快照落库只需要这个外键。
     */
    int backfillStrategy(Strategy strategy) {
        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
        if (account == null) {
            return 0;   // 从没开过模拟盘，没有可补的曲线
        }
        TreeMap<LocalDate, BigDecimal> closes = loadCloses(strategy.getSymbol());
        if (closes.isEmpty()) {
            log.warn("净值补账：{} 取不到历史K线，本轮跳过", strategy.getSymbol());
            return 0;
        }
        Long accountId = account.getId();
        Integer written = transactionTemplate.execute(status -> doBackfill(strategy,
                paperAccountRepository.getReferenceById(accountId), closes));
        return written == null ? 0 : written;
    }

    /**
     * 补账主体（包可见：单测直调，绕开事务模板）。
     *
     * <p>两步：先痕迹回填（精确、无窗口），再缺口补算（估算、限窗口）。
     * 已有快照的日子永远不碰——结算亲笔永远最高优先级。
     */
    int doBackfill(Strategy strategy, PaperAccount account, TreeMap<LocalDate, BigDecimal> closes) {
        Map<LocalDate, PaperEquitySnapshot> snapshotByDay = paperEquitySnapshotRepository
                .findByStrategyIdOrderByTradeDateAsc(strategy.getId()).stream()
                .collect(Collectors.toMap(PaperEquitySnapshot::getTradeDate, s -> s, (a, b) -> a,
                        HashMap::new));

        Map<LocalDate, PaperTradeTrace> lastDailyTraceByDay = new HashMap<>();
        for (PaperTradeTrace trace : paperTradeTraceRepository
                .findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                        strategy.getId(), ExecutionContract.SETTLEMENT_DAILY)) {
            // no_bar / data_unavailable 的日子结算**没有估值**（after 值只是原账照抄，
            // 当天连价格都没有）：用它回填会写出"equity ≠ cash + shares×close"的
            // 恒等式破坏格。这样的日子留给第 2 步补算器用历史收盘价重估。
            if (ExecutionContract.SKIP_NO_BAR.equals(trace.getSkipReason())
                    || ExecutionContract.SKIP_DATA_UNAVAILABLE.equals(trace.getSkipReason())) {
                continue;
            }
            lastDailyTraceByDay.put(trace.getTradeDate(), trace);   // 后写覆盖 = 当天最后一条
        }

        List<PaperTrade> trades = paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId()).stream()
                .sorted(Comparator.comparing(PaperTrade::getTradeDate)
                        .thenComparing(PaperTrade::getId))
                .collect(Collectors.toList());
        Map<LocalDate, List<PaperTrade>> tradesByDay = trades.stream()
                .collect(Collectors.groupingBy(PaperTrade::getTradeDate));

        int written = 0;

        // ===== 第 1 步：痕迹回填（精确）。老账一次性，不限窗口 =====
        for (Map.Entry<LocalDate, PaperTradeTrace> entry : lastDailyTraceByDay.entrySet()) {
            LocalDate day = entry.getKey();
            PaperTradeTrace trace = entry.getValue();
            if (snapshotByDay.containsKey(day)
                    || trace.getEquityAfter() == null || trace.getCashAfter() == null
                    || trace.getSharesAfter() == null) {
                continue;
            }
            BigDecimal close = trace.getBarClose() != null ? trace.getBarClose() : closes.get(day);
            if (close == null) {
                continue;   // 当天连价格证据都没有，缺就是缺
            }
            PaperEquitySnapshot snapshot = writeSnapshot(strategy, account, day,
                    trace.getEquityAfter(), trace.getCashAfter(), trace.getSharesAfter(),
                    close, SOURCE_TRACE_BACKFILL);
            snapshotByDay.put(day, snapshot);
            written++;
        }

        // ===== 第 2 步：缺口补算（估算）。限最近 N 个交易日 =====
        List<LocalDate> tradingDays = new ArrayList<>(closes.keySet());
        int windowStart = Math.max(0, tradingDays.size() - Math.max(1, maxTradingDays));
        List<LocalDate> windowDays = new ArrayList<>(tradingDays.subList(windowStart, tradingDays.size()));
        Set<LocalDate> window = Set.copyOf(windowDays);

        boolean anyMissing = windowDays.stream().anyMatch(day -> !snapshotByDay.containsKey(day));
        if (!anyMissing) {
            return written;
        }

        // 锚点：窗口内第一个缺格之前最近的已知快照；没有就从初始本金空仓起步
        LocalDate anchorDate = null;
        BigDecimal cash = account.getInitialCapital() != null ? account.getInitialCapital() : BigDecimal.ZERO;
        BigDecimal shares = BigDecimal.ZERO;
        LocalDate firstMissing = windowDays.stream()
                .filter(day -> !snapshotByDay.containsKey(day))
                .findFirst().orElse(null);
        if (firstMissing != null) {
            for (LocalDate day : tradingDays) {
                if (!day.isBefore(firstMissing)) {
                    break;
                }
                PaperEquitySnapshot snapshot = snapshotByDay.get(day);
                if (snapshot != null) {
                    anchorDate = day;
                    cash = snapshot.getCash();
                    shares = snapshot.getShares();
                }
            }
        }

        BigDecimal commission = commissionFraction(strategy.getConfigJson());
        for (LocalDate day : tradingDays) {
            if (anchorDate != null && !day.isAfter(anchorDate)) {
                continue;   // 锚点之前的状态已包含在锚点里
            }
            // 当日成交推进状态（无痕迹的老成交费用按佣金率估算）
            List<PaperTrade> dayTrades = tradesByDay.get(day);
            if (dayTrades != null) {
                for (PaperTrade trade : dayTrades) {
                    BigDecimal turnover = trade.getAmount() != null ? trade.getAmount() : BigDecimal.ZERO;
                    BigDecimal fee = turnover.multiply(commission);
                    if ("BUY".equalsIgnoreCase(trade.getSide())) {
                        shares = shares.add(trade.getShares() != null ? trade.getShares() : BigDecimal.ZERO);
                        cash = cash.subtract(turnover.add(fee));
                    } else if ("SELL".equalsIgnoreCase(trade.getSide())) {
                        shares = shares.subtract(trade.getShares() != null ? trade.getShares() : BigDecimal.ZERO);
                        cash = cash.add(turnover.subtract(fee));
                    }
                }
            }
            // 当天有痕迹/快照就对齐回真实值：估算漂移在每个已知点被清零
            PaperTradeTrace trace = lastDailyTraceByDay.get(day);
            PaperEquitySnapshot snapshot = snapshotByDay.get(day);
            if (trace != null && trace.getCashAfter() != null && trace.getSharesAfter() != null) {
                cash = trace.getCashAfter();
                shares = trace.getSharesAfter();
            } else if (snapshot != null) {
                cash = snapshot.getCash();
                shares = snapshot.getShares();
            }
            if (!snapshotByDay.containsKey(day) && window.contains(day)) {
                PaperEquitySnapshot filled = writeSnapshot(strategy, account, day,
                        cash.add(closes.get(day).multiply(shares)), cash, shares,
                        closes.get(day), SOURCE_CATCHUP);
                snapshotByDay.put(day, filled);
                written++;
            }
        }
        return written;
    }

    /** 写一行快照（金额按 Money 口径量化；调用方保证 (strategy, day) 尚不存在）。 */
    private PaperEquitySnapshot writeSnapshot(Strategy strategy, PaperAccount account,
                                              LocalDate day, BigDecimal equity, BigDecimal cash,
                                              BigDecimal shares, BigDecimal close, String source) {
        PaperEquitySnapshot snapshot = PaperEquitySnapshot.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .tradeDate(day)
                .equity(Money.equity(equity))
                .cash(Money.amount(cash))
                .shares(Money.price(shares))
                .closePrice(Money.price(close))
                .source(source)
                .build();
        snapshot = paperEquitySnapshotRepository.save(snapshot);
        log.info("净值补账 strategy id={} {} ← {} (equity={})", strategy.getId(), day,
                source, snapshot.getEquity());
        return snapshot;
    }

    /** 历史 K 线 → 日期→收盘价（正序）。取不到返回空表（fail-open，本轮跳过）。 */
    private TreeMap<LocalDate, BigDecimal> loadCloses(String symbol) {
        TreeMap<LocalDate, BigDecimal> closes = new TreeMap<>();
        try {
            StockHistoryResponse history = stockDataGateway.getStockHistory(symbol, "day");
            if (history == null || history.timeSeries() == null) {
                return closes;
            }
            for (Map.Entry<String, StockHistoryResponse.DailyPrice> entry
                    : history.timeSeries().entrySet()) {
                try {
                    LocalDate day = LocalDate.parse(entry.getKey(), DAY);
                    BigDecimal close = entry.getValue() != null && entry.getValue().close() != null
                            ? new BigDecimal(entry.getValue().close()) : null;
                    if (close != null && close.signum() > 0) {
                        closes.put(day, close);
                    }
                } catch (Exception ignore) {   // noqa: BLE001 —— 脏行跳过，不挡整批
                }
            }
        } catch (Exception e) {   // noqa: BLE001 —— fail-open
            log.warn("净值补账：{} 历史K线取数失败：{}", symbol, e.getMessage());
        }
        return closes;
    }

    /** 配置里的佣金率（小数）。解析失败/缺省按 0.1% 兜底——只影响估算格的费用近似。 */
    BigDecimal commissionFraction(String configJson) {
        try {
            JsonNode config = configJson == null ? null : objectMapper.readTree(configJson);
            JsonNode rate = config == null ? null
                    : config.path("risk").path("commission_pct");
            if (rate.isNumber() && rate.asDouble() > 0) {
                return BigDecimal.valueOf(rate.asDouble() / 100.0);
            }
        } catch (Exception ignore) {   // noqa: BLE001
        }
        return DEFAULT_COMMISSION;
    }
}
