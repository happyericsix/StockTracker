package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.List;

/**
 * 净值序列的**唯一**算法：最大回撤、空仓天数、以及"同期买入持有"的对照。
 *
 * <h3>为什么是一个纯函数类，而不是散在报告与接口里</h3>
 * "这段时间跑赢还是跑输了"这个数会被报告、接口、未来的周报各引用一次。
 * 各算一遍的下场是三个地方给出三个略有差异的数字 —— 而这种差异最后会被解读成
 * "系统在编数据"。算法只写一次，谁需要谁来调。
 *
 * <h3>"机会成本记账"就是从这里来的</h3>
 * 空仓在账面上是 0 收益，于是"什么都不做"看起来没有代价。把同期的
 * **买入持有**摆在旁边，代价立刻显形：{@code excessVsBuyAndHoldPct} 为负的那些日子，
 * 正是"不动"真正花了钱的日子。这是行业里的基准对照，落在单个账户上。
 */
public final class PaperEquitySeries {

    private PaperEquitySeries() {
    }

    /**
     * 一段净值序列的读视图。
     *
     * @param days               有快照的交易日数（**不是**自然日跨度：中间没结算的日子是缺口，不补）
     * @param firstDate          序列起点（报告要写"自哪天起"，否则曲线会被误读成"一直如此"）
     * @param lastDate           序列终点
     * @param latestEquity       最新净值
     * @param returnPct          期间净值收益率（%）
     * @param maxDrawdownPct     期间最大回撤（%，≤0；不足两个点时为 null）
     * @param flatDays           当前连续空仓交易日数（末尾连续 shares == 0）
     * @param flatRatioPct       空仓快照占全部快照的比例（%）
     * @param buyAndHoldPct      同期"买入持有"收益（%，用同一批收盘价序列算）
     * @param excessVsBuyAndHoldPct 期间超额（%，= 净值收益 − 买入持有）
     */
    public record Summary(int days, LocalDate firstDate, LocalDate lastDate,
                          BigDecimal latestEquity, BigDecimal returnPct,
                          BigDecimal maxDrawdownPct, int flatDays, BigDecimal flatRatioPct,
                          BigDecimal buyAndHoldPct, BigDecimal excessVsBuyAndHoldPct) {

        public boolean hasData() {
            return days > 0;
        }

        /** 只有一天时谈不上"期间表现"：报告据此只说净值，不说超额。 */
        public boolean hasPeriod() {
            return days >= 2;
        }
    }

    /**
     * 汇总一段按时间**正序**的净值序列。
     *
     * <p>入参顺序错了会让回撤与买入持有基准一起算错，所以这里显式排序（按 tradeDate），
     * 而不是"相信调用方按顺序传"。
     */
    public static Summary summarize(List<PaperEquitySnapshot> snapshots) {
        if (snapshots == null || snapshots.isEmpty()) {
            return new Summary(0, null, null, null, null, null, 0, null, null, null);
        }
        List<PaperEquitySnapshot> series = snapshots.stream()
                .filter(item -> item != null && item.getTradeDate() != null && item.getEquity() != null)
                .sorted((a, b) -> a.getTradeDate().compareTo(b.getTradeDate()))
                .toList();
        if (series.isEmpty()) {
            return new Summary(0, null, null, null, null, null, 0, null, null, null);
        }

        PaperEquitySnapshot first = series.get(0);
        PaperEquitySnapshot last = series.get(series.size() - 1);
        BigDecimal returnPct = first == last ? BigDecimal.ZERO
                : pct(last.getEquity(), first.getEquity());

        BigDecimal buyAndHoldPct = null;
        if (first.getClosePrice() != null && last.getClosePrice() != null
                && first.getClosePrice().signum() != 0) {
            buyAndHoldPct = pct(last.getClosePrice(), first.getClosePrice());
        }

        BigDecimal excess = (returnPct != null && buyAndHoldPct != null)
                ? returnPct.subtract(buyAndHoldPct).setScale(Money.RETURN_SCALE, RoundingMode.HALF_UP)
                : null;

        return new Summary(
                series.size(), first.getTradeDate(), last.getTradeDate(), last.getEquity(),
                returnPct, maxDrawdownPct(series), flatDays(series), flatRatioPct(series),
                buyAndHoldPct, excess);
    }

    /**
     * 预期度量：**封闭集**，每一项都用已有的一段净值序列算出来。
     *
     * <h3>为什么度量只有这几个</h3>
     * 它们全都是**账户层面可观测**的量（超额、收益、回撤），没有一个是"股价会涨到多少"。
     * 这是本项目第一条原则的落点：可以承诺"未来 20 个交易日超额不低于 0"（到期能验），
     * 不可以承诺"会涨 8%"（既不可验，也不该做）。
     *
     * <p>比较方向一律是"**实际 ≥ 门槛**记为达成"（回撤是负数，所以"回撤不超过 8%"
     * 写成门槛 −8 就落在同一个方向上）—— 一个比较方向，少一处能写反的地方。
     */
    public static final String METRIC_EXCESS_VS_BUY_AND_HOLD = "excess_vs_buy_and_hold_pct";
    public static final String METRIC_RETURN = "return_pct";
    public static final String METRIC_MAX_DRAWDOWN = "max_drawdown_pct";

    public static final java.util.List<String> METRICS = java.util.List.of(
            METRIC_EXCESS_VS_BUY_AND_HOLD, METRIC_RETURN, METRIC_MAX_DRAWDOWN);

    public static boolean isMetric(String metric) {
        return metric != null && METRICS.contains(metric.trim());
    }

    /**
     * 从 `from`（含）到序列末尾算一个度量。返回 {@code null} = **算不出来**（样本不足），
     * 而不是 0 —— 0 会被读成"达标了"。
     */
    public static BigDecimal metricValue(List<PaperEquitySnapshot> series, String metric,
                                         LocalDate from) {
        if (series == null || series.isEmpty()) {
            return null;
        }
        List<PaperEquitySnapshot> window = series.stream()
                .filter(item -> item != null && item.getTradeDate() != null
                        && (from == null || !item.getTradeDate().isBefore(from)))
                .toList();
        // 至少要两个点：一个点谈不上"期间表现"，也谈不上回撤
        if (window.size() < 2) {
            return null;
        }
        Summary summary = summarize(window);
        String key = metric == null ? "" : metric.trim();
        return switch (key) {
            case METRIC_EXCESS_VS_BUY_AND_HOLD -> summary.excessVsBuyAndHoldPct();
            case METRIC_RETURN -> summary.returnPct();
            case METRIC_MAX_DRAWDOWN -> summary.maxDrawdownPct();
            default -> null;
        };
    }

    /**
     * 最大回撤：净值从历史高点的最大回落（%，≤0）。
     *
     * <p>用它而不是"最大单日跌幅"：前者是"最难受的那一段"，后者只是噪声。
     * 只用一个点算不出来，所以返回 null 而不是 0 —— 0 会被读成"没有回撤"。
     */
    public static BigDecimal maxDrawdownPct(List<PaperEquitySnapshot> series) {
        if (series == null || series.size() < 2) {
            return null;
        }
        BigDecimal peak = null;
        BigDecimal worst = BigDecimal.ZERO;
        for (PaperEquitySnapshot item : series) {
            BigDecimal equity = item.getEquity();
            if (equity == null) {
                continue;
            }
            if (peak == null || equity.compareTo(peak) > 0) {
                peak = equity;
                continue;
            }
            if (peak.signum() == 0) {
                continue;
            }
            BigDecimal drawdown = equity.subtract(peak)
                    .multiply(BigDecimal.valueOf(100))
                    .divide(peak, Money.RETURN_SCALE, RoundingMode.HALF_UP);
            if (drawdown.compareTo(worst) < 0) {
                worst = drawdown;
            }
        }
        return worst;
    }

    /** 末尾连续空仓的交易日数：回答"已经空仓多久了"。 */
    public static int flatDays(List<PaperEquitySnapshot> series) {
        if (series == null || series.isEmpty()) {
            return 0;
        }
        int days = 0;
        for (int i = series.size() - 1; i >= 0; i--) {
            BigDecimal shares = series.get(i).getShares();
            if (shares != null && shares.signum() > 0) {
                break;
            }
            days++;
        }
        return days;
    }

    /** 空仓快照占全部快照的比例（%）：回答"这条规则有多少时间不在场"。 */
    public static BigDecimal flatRatioPct(List<PaperEquitySnapshot> series) {
        if (series == null || series.isEmpty()) {
            return null;
        }
        long flat = series.stream()
                .filter(item -> item.getShares() == null || item.getShares().signum() == 0)
                .count();
        return BigDecimal.valueOf(flat * 100.0 / series.size())
                .setScale(1, RoundingMode.HALF_UP);
    }

    /** (to − from) / from × 100，保留 4 位（与 MoneyPolicy 的收益率口径一致）。 */
    private static BigDecimal pct(BigDecimal to, BigDecimal from) {
        if (to == null || from == null || from.signum() == 0) {
            return null;
        }
        return to.subtract(from)
                .multiply(BigDecimal.valueOf(100))
                .divide(from, Money.RETURN_SCALE, RoundingMode.HALF_UP);
    }
}
