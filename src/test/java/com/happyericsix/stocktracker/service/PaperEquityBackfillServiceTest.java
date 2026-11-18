package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentMatchers;
import org.mockito.Mockito;
import org.springframework.test.util.ReflectionTestUtils;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.when;

/**
 * 净值补账的三条纪律（2026-11-18）：
 * 1. 痕迹回填<b>照抄</b>结算亲笔数字（equityAfter 等），来源标 trace_backfill；
 * 2. 缺口补算从锚点状态出发按成交流水推进，来源标 catchup——补算格绝不冒充结算；
 * 3. 幂等 + 窗口：已有快照的日子永远不碰，窗口外的老洞（无痕迹的）不补。
 *
 * <p>直调 {@code doBackfill}（绕开事务模板与 K 线网关）：K 线由测试直接注入
 * TreeMap，仓储全部 mock —— 这里钉的是<b>补账算法</b>，不是取数。
 */
class PaperEquityBackfillServiceTest {

    private final PaperEquitySnapshotRepository snapshotRepository = Mockito.mock(PaperEquitySnapshotRepository.class);
    private final PaperTradeTraceRepository traceRepository = Mockito.mock(PaperTradeTraceRepository.class);
    private final PaperTradeRepository tradeRepository = Mockito.mock(PaperTradeRepository.class);

    private PaperEquityBackfillService service;
    private Strategy strategy;
    private PaperAccount account;

    /** K 线：11-02 ~ 11-10 共 6 个交易日（11-07/08 周末没 bar，天然非交易日）。 */
    private final TreeMap<LocalDate, BigDecimal> closes = new TreeMap<>(Map.of(
            LocalDate.of(2026, 11, 2), new BigDecimal("10"),
            LocalDate.of(2026, 11, 3), new BigDecimal("11"),
            LocalDate.of(2026, 11, 4), new BigDecimal("12"),
            LocalDate.of(2026, 11, 5), new BigDecimal("13"),
            LocalDate.of(2026, 11, 6), new BigDecimal("14"),
            LocalDate.of(2026, 11, 10), new BigDecimal("15")));

    @BeforeEach
    void setUp() {
        service = new PaperEquityBackfillService(Mockito.mock(StrategyRepository.class),
                Mockito.mock(PaperAccountRepository.class), tradeRepository, traceRepository,
                snapshotRepository, null, null, new ObjectMapper());
        ReflectionTestUtils.setField(service, "maxTradingDays", 5);
        ReflectionTestUtils.setField(service, "enabled", true);

        User user = User.builder().id(1L).username("alice").build();
        strategy = Strategy.builder().id(4L).user(user).symbol("300750")
                .configJson("{\"risk\":{\"commission_pct\":0.1}}").build();
        account = PaperAccount.builder().id(9L).strategy(strategy).user(user)
                .initialCapital(new BigDecimal("100000")).build();

        when(snapshotRepository.findByStrategyIdOrderByTradeDateAsc(4L)).thenReturn(new ArrayList<>());
        when(traceRepository.findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                any(), any())).thenReturn(new ArrayList<>());
        when(tradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(4L))
                .thenReturn(new ArrayList<>());
        when(snapshotRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));
    }

    private static PaperTradeTrace dailyTrace(LocalDate day, String cashAfter, String sharesAfter,
                                              String equityAfter) {
        return PaperTradeTrace.builder()
                .tradeDate(day)
                .settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                .cashAfter(cashAfter == null ? null : new BigDecimal(cashAfter))
                .sharesAfter(sharesAfter == null ? null : new BigDecimal(sharesAfter))
                .equityAfter(equityAfter == null ? null : new BigDecimal(equityAfter))
                .barClose(new BigDecimal("12"))
                .build();
    }

    /** no_bar 那天结算没估值（after 只是原账照抄）：不许回填——写了就是恒等式破坏格。 */
    private static PaperTradeTrace noBarTrace(LocalDate day) {
        return PaperTradeTrace.builder()
                .tradeDate(day)
                .settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                .decision("skip")
                .skipReason(ExecutionContract.SKIP_NO_BAR)
                .cashAfter(new BigDecimal("90000.00"))
                .sharesAfter(new BigDecimal("800"))
                .equityAfter(new BigDecimal("99600.00"))
                .build();
    }

    private static PaperEquitySnapshot settlementSnapshot(LocalDate day, String cash, String shares) {
        return PaperEquitySnapshot.builder()
                .tradeDate(day)
                .cash(new BigDecimal(cash))
                .shares(new BigDecimal(shares))
                .equity(new BigDecimal("100000"))
                .build();
    }

    /** 有当天结算痕迹的老账：照抄 after 值，来源 trace_backfill，精确到分。 */
    @Test
    void traceBackfillCopiesSettlementNumbersExactly() {
        when(traceRepository.findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                any(), any())).thenReturn(List.of(
                dailyTrace(LocalDate.of(2026, 11, 2), "90000.00", "800", "99600.00")));

        int written = service.doBackfill(strategy, account, closes);

        // 11-02 痕迹回填 1 格 + 窗口（11-03 起的 5 个交易日）缺口补算 5 格
        assertEquals(6, written);
        Mockito.verify(snapshotRepository).save(ArgumentMatchers.argThat(
                s -> LocalDate.of(2026, 11, 2).equals(s.getTradeDate())
                        && "trace_backfill".equals(s.getSource())
                        && new BigDecimal("99600.00").compareTo(s.getEquity()) == 0
                        && new BigDecimal("90000.00").compareTo(s.getCash()) == 0
                        && new BigDecimal("800").compareTo(s.getShares()) == 0));
    }

    /** 缺口补算：从锚点快照出发，跨成交日推进现金/股数，用收盘价重估净值（佣金 0.1%）。 */
    @Test
    void catchupWalksFromAnchorApplyingTrades() {
        // 锚点：11-02 有结算快照（现金 9 万、持仓 800 股）；11-03 买了 100 股花 1100 元
        when(snapshotRepository.findByStrategyIdOrderByTradeDateAsc(4L))
                .thenReturn(List.of(settlementSnapshot(LocalDate.of(2026, 11, 2), "90000", "800")));
        when(tradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(4L))
                .thenReturn(List.of(PaperTrade.builder()
                        .id(7L).tradeDate(LocalDate.of(2026, 11, 3)).side("BUY")
                        .price(new BigDecimal("11")).shares(new BigDecimal("100"))
                        .amount(new BigDecimal("1100")).build()));

        int written = service.doBackfill(strategy, account, closes);

        // 窗口 5 个交易日（11-03 起）全部缺格 → 5 行补算
        assertEquals(5, written);
        Mockito.verify(snapshotRepository, Mockito.times(5)).save(ArgumentMatchers.argThat(
                s -> "catchup".equals(s.getSource())));
        // 11-04：现金 = 90000 - 1100 - 1.1 佣金；净值 = 现金 + 900 股 × 12
        Mockito.verify(snapshotRepository).save(ArgumentMatchers.argThat(
                s -> LocalDate.of(2026, 11, 4).equals(s.getTradeDate())
                        && new BigDecimal("88898.90").compareTo(s.getCash()) == 0
                        && new BigDecimal("99698.90").compareTo(s.getEquity()) == 0
                        && new BigDecimal("900").compareTo(s.getShares()) == 0));
    }

    /** 幂等：已有快照（结算亲笔）的日子绝不重写——亲笔永远最高优先级。 */
    @Test
    void existingSnapshotDaysAreNeverTouched() {
        when(traceRepository.findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                any(), any())).thenReturn(List.of(
                dailyTrace(LocalDate.of(2026, 11, 2), "90000.00", "800", "99600.00")));
        when(snapshotRepository.findByStrategyIdOrderByTradeDateAsc(4L))
                .thenReturn(List.of(settlementSnapshot(LocalDate.of(2026, 11, 2), "90000", "800")));

        int written = service.doBackfill(strategy, account, closes);

        // 11-02 已有亲笔 → 痕迹回填跳过；窗口 11-03 起补 5 格，且没有任何一行是 11-02
        assertEquals(5, written);
        Mockito.verify(snapshotRepository, Mockito.times(5)).save(ArgumentMatchers.argThat(
                s -> !LocalDate.of(2026, 11, 2).equals(s.getTradeDate())));
    }

    /** 窗口边界：窗口外的老洞（无痕迹）不补——宁缺毋假，深洞等窗口滚过去再消化。 */
    @Test
    void oldGapsBeyondWindowAreLeftAsHoles() {
        int written = service.doBackfill(strategy, account, closes);

        // 全缺时只补最近 5 个交易日，11-02 留洞
        assertEquals(5, written);
        Mockito.verify(snapshotRepository, Mockito.times(5)).save(ArgumentMatchers.argThat(
                s -> !LocalDate.of(2026, 11, 2).equals(s.getTradeDate())));
    }

    /** 状态对齐：走查经过有痕迹的日子时对齐回亲笔值——估算漂移在每个已知点清零。 */
    @Test
    void walkSnapsToKnownStatesAndClearsDrift() {
        when(snapshotRepository.findByStrategyIdOrderByTradeDateAsc(4L))
                .thenReturn(List.of(settlementSnapshot(LocalDate.of(2026, 11, 2), "90000", "800")));
        // 11-03 有结算痕迹（after 亲笔）：当天有买有卖，现金股数以痕迹为准
        when(traceRepository.findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                any(), any())).thenReturn(List.of(
                dailyTrace(LocalDate.of(2026, 11, 3), "88000.00", "700", "96100.00")));

        service.doBackfill(strategy, account, closes);

        // 11-04 的净值 = 88000 + 700 × 12（状态来自 11-03 痕迹亲笔，而非估算）
        Mockito.verify(snapshotRepository).save(ArgumentMatchers.argThat(
                s -> LocalDate.of(2026, 11, 4).equals(s.getTradeDate())
                        && new BigDecimal("88000.00").compareTo(s.getCash()) == 0
                        && new BigDecimal("700").compareTo(s.getShares()) == 0
                        && new BigDecimal("96400.00").compareTo(s.getEquity()) == 0));
    }

    /**
     * no_bar 的痕迹不回填（2026-11-18 实测教训）：那天结算没估值，after 值是原账
     * 照抄，回填会写出"equity ≠ cash + shares × close"的假格。这种日子交给
     * 补算器用历史收盘价重估（本例 11-02 在窗口外，保持为洞也是正确行为）。
     */
    @Test
    void noBarTracesAreNotBackfilled() {
        when(traceRepository.findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
                any(), any())).thenReturn(List.of(noBarTrace(LocalDate.of(2026, 11, 2))));

        int written = service.doBackfill(strategy, account, closes);

        // 11-02（窗口外）不因 no_bar 痕迹而多出一格；窗口内 5 格全是 catchup
        assertEquals(5, written);
        Mockito.verify(snapshotRepository, Mockito.times(5)).save(ArgumentMatchers.argThat(
                s -> "catchup".equals(s.getSource())
                        && !LocalDate.of(2026, 11, 2).equals(s.getTradeDate())));
    }
}
