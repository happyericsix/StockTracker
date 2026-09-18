package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 净值序列的算法：最大回撤、空仓天数、**相对买入持有的超额**。
 *
 * <h3>为什么这些数字必须逐条钉住</h3>
 * 报告里"超额 -2.50%"是用户据以判断"这套东西值不值得继续跑"的那一行。
 * 算错一个符号或一个基准，它就从"证据"变成"编出来的理由"：
 * 空仓被算成没有代价、回撤被算成收益、不同窗口混在一起比较 —— 每一条都不会报错。
 */
class PaperEquitySeriesTest {

    private static PaperEquitySnapshot point(String date, String equity, String close, double shares) {
        return PaperEquitySnapshot.builder()
                .tradeDate(LocalDate.parse(date))
                .equity(new BigDecimal(equity))
                .cash(new BigDecimal("0.00"))
                .shares(BigDecimal.valueOf(shares))
                .closePrice(new BigDecimal(close))
                .build();
    }

    // ==================== 1. 最大回撤 ====================

    @Test
    void maxDrawdownIsMeasuredFromTheRunningPeak() {
        // 100 → 120 → 90：从峰值 120 回落 25%
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "120000.00", "12.0000", 0),
                point("2026-09-03", "90000.00", "9.0000", 0));

        assertEquals(0, new BigDecimal("-25.0000").compareTo(
                PaperEquitySeries.maxDrawdownPct(series)));
    }

    @Test
    void aSinglePointHasNoDrawdownInsteadOfZero() {
        // 0 会被读成"从未回撤"；null 才是"算不出来"
        assertNull(PaperEquitySeries.maxDrawdownPct(List.of(point("2026-09-01", "100000.00", "10.0000", 0))));
        assertNull(PaperEquitySeries.maxDrawdownPct(List.of()));
        assertNull(PaperEquitySeries.maxDrawdownPct(null));
    }

    @Test
    void aMonotonicRiseHasZeroDrawdown() {
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "101000.00", "10.1000", 0),
                point("2026-09-03", "103000.00", "10.3000", 0));
        assertEquals(0, BigDecimal.ZERO.compareTo(PaperEquitySeries.maxDrawdownPct(series)));
    }

    // ==================== 2. 空仓：这是"不动"的量化 ====================

    @Test
    void flatDaysCountsTheTrailingStreakOfNoPosition() {
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 100),
                point("2026-09-02", "100000.00", "10.0000", 0),
                point("2026-09-03", "100000.00", "10.0000", 0),
                point("2026-09-04", "100000.00", "10.0000", 0));

        assertEquals(3, PaperEquitySeries.flatDays(series));
    }

    @Test
    void holdingTodayMeansZeroFlatDays() {
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "100000.00", "10.0000", 100));
        assertEquals(0, PaperEquitySeries.flatDays(series));
    }

    @Test
    void flatRatioUsesAllSnapshotsNotJustTheTrailingStreak() {
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 100),
                point("2026-09-02", "100000.00", "10.0000", 0),
                point("2026-09-03", "100000.00", "10.0000", 100),
                point("2026-09-04", "100000.00", "10.0000", 0));
        assertEquals(0, new BigDecimal("50.0").compareTo(PaperEquitySeries.flatRatioPct(series)));
    }

    // ==================== 3. 机会成本：空仓不等于没有代价 ====================

    @Test
    void beingFlatWhileTheMarketRisesShowsUpAsNegativeExcess() {
        // 账户一直空仓（净值不动），标的从 10 涨到 11 → 期间收益 0%，买入持有 +10%，
        // 超额 -10%：**这就是"不动"的代价**，账面上完全看不出来
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "100000.00", "10.5000", 0),
                point("2026-09-03", "100000.00", "11.0000", 0));

        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(series);
        assertEquals(0, new BigDecimal("0.0000").compareTo(summary.returnPct()));
        assertEquals(0, new BigDecimal("10.0000").compareTo(summary.buyAndHoldPct()));
        assertEquals(0, new BigDecimal("-10.0000").compareTo(summary.excessVsBuyAndHoldPct()));
    }

    @Test
    void beingFlatWhileTheMarketFallsIsAPositiveContribution() {
        // 反过来也要算对：空仓躲过下跌，超额为正 —— 不能只认坏消息
        List<PaperEquitySnapshot> series = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "100000.00", "9.0000", 0));

        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(series);
        assertEquals(0, new BigDecimal("10.0000").compareTo(summary.excessVsBuyAndHoldPct()));
    }

    @Test
    void withoutAClosePriceTheBenchmarkIsMissingNotZero() {
        // 没有收盘价就算不出基准：返回 null，报告据此只说净值（0 会被读成"打平"）
        List<PaperEquitySnapshot> series = List.of(
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-01"))
                        .equity(new BigDecimal("100000.00")).cash(new BigDecimal("100000.00"))
                        .shares(BigDecimal.ZERO).build(),
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-02"))
                        .equity(new BigDecimal("101000.00")).cash(new BigDecimal("101000.00"))
                        .shares(BigDecimal.ZERO).build());

        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(series);
        assertNull(summary.buyAndHoldPct());
        assertNull(summary.excessVsBuyAndHoldPct());
        assertEquals(0, new BigDecimal("1.0000").compareTo(summary.returnPct()));
    }

    // ==================== 4. 边界与前提 ====================

    @Test
    void onePointHasNoPeriodSoNoExcess() {
        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(
                List.of(point("2026-09-01", "100000.00", "10.0000", 0)));

        assertTrue(summary.hasData());
        assertFalse(summary.hasPeriod(), "只有一天谈不上期间表现");
        assertEquals(1, summary.days());
        assertEquals(LocalDate.parse("2026-09-01"), summary.firstDate());
    }

    @Test
    void anEmptySeriesSaysSoInsteadOfFabricatingZeros() {
        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(List.of());
        assertFalse(summary.hasData());
        assertNull(summary.latestEquity());
        assertNull(summary.maxDrawdownPct());
        assertEquals(0, summary.days());
    }

    @Test
    void theSeriesIsSortedByDateSoAnUnsortedCallerCannotPoisonTheMath() {
        // 入参顺序错了会让回撤与"买入持有"一起算错 —— 算法自己排序，不指望调用方守规矩
        List<PaperEquitySnapshot> unsorted = List.of(
                point("2026-09-03", "90000.00", "9.0000", 0),
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "120000.00", "12.0000", 0));

        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(unsorted);

        assertEquals(LocalDate.parse("2026-09-01"), summary.firstDate());
        assertEquals(LocalDate.parse("2026-09-03"), summary.lastDate());
        // 首 10 → 末 9：期间 -10%；峰值 12 → 9 是 -25%
        assertEquals(0, new BigDecimal("-10.0000").compareTo(summary.returnPct()));
        assertEquals(0, new BigDecimal("-25.0000").compareTo(summary.maxDrawdownPct()));
    }

    @Test
    void theEquityIdentityHoldsForEverySnapshot() {
        // 快照是"现金 + 股数 × 价格"的证据。这里手写两条**账户真会出现**的状态：
        // 满仓（现金 0）与半仓（现金还在），并逐条核对恒等式。
        List<PaperEquitySnapshot> series = List.of(
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-01"))
                        .cash(new BigDecimal("0.00")).shares(new BigDecimal("10000.0000"))
                        .closePrice(new BigDecimal("10.0000")).equity(new BigDecimal("100000.00"))
                        .build(),
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-02"))
                        .cash(new BigDecimal("50000.00")).shares(new BigDecimal("5000.0000"))
                        .closePrice(new BigDecimal("10.1000")).equity(new BigDecimal("100500.00"))
                        .build());

        for (PaperEquitySnapshot item : series) {
            assertTrue(Money.equityIdentityHolds(item.getCash(), item.getShares(),
                    item.getClosePrice(), item.getEquity()),
                    "净值不等于现金 + 股数 × 价格：" + item.getTradeDate());
        }
    }
}
