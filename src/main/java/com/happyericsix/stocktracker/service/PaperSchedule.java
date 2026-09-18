package com.happyericsix.stocktracker.service;

import java.time.DayOfWeek;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.LocalTime;
import java.time.ZoneId;
import java.util.Locale;

/**
 * "下一次什么时候评估"—— **唯一口径**。
 *
 * <h3>为什么值得单独一个类</h3>
 * 这个问题的答案会同时出现在三处：模拟盘总览页、详情页的状态条、每日简报的结尾。
 * 各写一遍的后果不是报错，而是三处各说一个时间 —— 用户只能得出"这系统自己都没准数"。
 *
 * <h3>诚实边界：没有交易日历</h3>
 * 项目里**没有**交易日历（设计文档 §7.1 P-5 记为已知缺口），只有"周末不算"这条近似。
 * 所以这里返回的时间一律是"**不早于**"：法定节假日那天任务照跑，
 * 数据源拿不到当日 bar 时会落一条带原因（{@code no_bar} / {@code market_closed}）的痕迹，
 * 而不是在这里假装知道哪天休市。把"不知道"说成"知道"，比给不出一份日历更糟。
 */
public final class PaperSchedule {

    /** 日线结算时间（与 {@code PaperTradingJob} 的 cron 一致：交易日 15:30，Asia/Shanghai）。 */
    public static final LocalTime DAILY_SETTLEMENT_TIME = LocalTime.of(15, 30);

    /** 盘中评估的节奏（与 {@code StockPriceRefreshJob} 的 fixedRate 一致）。 */
    public static final int REALTIME_INTERVAL_MINUTES = 5;

    private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");

    private PaperSchedule() {
    }

    /** 由调用方决定"现在"，便于测试与报告复现。 */
    public static LocalDateTime now() {
        return LocalDateTime.now(ZONE);
    }

    /**
     * 下一次日线结算（不含节假日判断，见类注释）。
     *
     * <p>规则：今天若是工作日且还没到 15:30，就是今天的 15:30；否则顺延到下一个工作日的 15:30。
     */
    public static LocalDateTime nextDailySettlement(LocalDateTime now) {
        LocalDate date = now.toLocalDate();
        if (isWeekday(date) && now.toLocalTime().isBefore(DAILY_SETTLEMENT_TIME)) {
            return LocalDateTime.of(date, DAILY_SETTLEMENT_TIME);
        }
        LocalDate next = date.plusDays(1);
        while (!isWeekday(next)) {
            next = next.plusDays(1);
        }
        return LocalDateTime.of(next, DAILY_SETTLEMENT_TIME);
    }

    /** 工作日近似：只排除周末。**不是**交易日历（见类注释）。 */
    public static boolean isWeekday(LocalDate date) {
        if (date == null) {
            return false;
        }
        DayOfWeek day = date.getDayOfWeek();
        return day != DayOfWeek.SATURDAY && day != DayOfWeek.SUNDAY;
    }

    /**
     * 一条策略的下一次评估：什么时候、以及**为什么是这个时间**。
     *
     * @param decisionMode {@code agent} 只走日线结算；其余（含存量 null）走"盘中 + 日线"
     */
    public static NextEvaluation next(String decisionMode, LocalDateTime now) {
        LocalDateTime daily = nextDailySettlement(now);
        boolean agent = ExecutionContract.DECISION_MODE_AGENT.equals(
                ExecutionContract.normalizeDecisionMode(decisionMode));
        if (agent) {
            return new NextEvaluation("daily", daily,
                    "多角色委员会只在日线结算时决策（盘中不做规则检查）");
        }
        return new NextEvaluation("realtime", daily,
                "盘中随行情刷新评估（约每 " + REALTIME_INTERVAL_MINUTES + " 分钟一次），日线结算在 "
                        + format(daily));
    }

    /**
     * 给界面与报告用的一句话（**唯一措辞**：时间格式、口径说明都在这里，别处不再各写一套）。
     *
     * <p>把"按工作日近似"写在每一句话里，是因为没有交易日历时，
     * 用户看到的日期严格来说只是"不早于"——不写清楚，节假日空跑的那天就会被当成 bug。
     */
    public static String describe(NextEvaluation next) {
        if (next == null) {
            return "";
        }
        return "下一次评估：" + format(next.at()) + "（" + next.note()
                + "；按工作日近似，节假日会空跑一次并留下原因）";
    }

    public static String format(LocalDateTime at) {
        if (at == null) {
            return "N/A";
        }
        return String.format(Locale.ROOT, "%04d-%02d-%02d %02d:%02d",
                at.getYear(), at.getMonthValue(), at.getDayOfMonth(), at.getHour(), at.getMinute());
    }

    /** 下一次评估：类型（realtime / daily）、时间、以及人话说明。 */
    public record NextEvaluation(String kind, LocalDateTime at, String note) {
    }
}
