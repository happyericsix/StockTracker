package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * "下一次什么时候评估"——**唯一口径**，所以要逐条钉住。
 *
 * <h3>为什么这个类值得有测试</h3>
 * 这个答案会同时出现在总览页、详情页状态条和每日简报里。算错的后果不是崩溃，
 * 而是**三处各说一个时间**（用户只能得出"这系统自己都没准数"），
 * 或者更糟：承诺一个永远不会发生的时刻，让"它还在跑"这件事变成一句空话。
 *
 * <p>同时钉住**边界**：没有交易日历，所以只能给"不早于"，而且必须写在返回值里 ——
 * 把"不知道哪天休市"说成"知道"，比给不出一份日历更糟。
 */
class PaperScheduleTest {

    private static LocalDateTime at(int year, int month, int day, int hour, int minute) {
        return LocalDateTime.of(year, month, day, hour, minute);
    }

    @Test
    void aWeekdayBeforeSettlementStillCountsAsToday() {
        // 2026-09-18 是周五，14:00 → 今天 15:30
        assertEquals(at(2026, 9, 18, 15, 30),
                PaperSchedule.nextDailySettlement(at(2026, 9, 18, 14, 0)));
    }

    @Test
    void afterSettlementItRollsToTheNextWeekdaySkippingTheWeekend() {
        // 周五 16:00 → 周末不算 → 周一 15:30
        assertEquals(at(2026, 9, 21, 15, 30),
                PaperSchedule.nextDailySettlement(at(2026, 9, 18, 16, 0)));
    }

    @Test
    void theWeekendItselfRollsToMonday() {
        // 周六、周日任意时刻 → 周一
        assertEquals(at(2026, 9, 21, 15, 30),
                PaperSchedule.nextDailySettlement(at(2026, 9, 19, 9, 0)));
        assertEquals(at(2026, 9, 21, 15, 30),
                PaperSchedule.nextDailySettlement(at(2026, 9, 20, 23, 59)));
    }

    @Test
    void exactlyAtSettlementTimeRollsToTheNextDay() {
        // 恰好 15:30：这一秒的任务已经在跑，说"就是现在"会让人以为还没开始
        assertEquals(at(2026, 9, 21, 15, 30),
                PaperSchedule.nextDailySettlement(at(2026, 9, 18, 15, 30)));
    }

    @Test
    void weekdaysAreApproximatedByTheCalendarOnly() {
        assertTrue(PaperSchedule.isWeekday(java.time.LocalDate.of(2026, 9, 18)));   // 周五
        assertFalse(PaperSchedule.isWeekday(java.time.LocalDate.of(2026, 9, 19)));  // 周六
        // 国庆节那天按日历是工作日 —— 我们**不假装知道**它是节假日，靠痕迹说原因
        assertTrue(PaperSchedule.isWeekday(java.time.LocalDate.of(2026, 10, 1)));
    }

    @Test
    void committeeModeSaysItOnlyDecidesAtSettlement() {
        PaperSchedule.NextEvaluation next = PaperSchedule.next(
                ExecutionContract.DECISION_MODE_AGENT, at(2026, 9, 18, 16, 0));

        assertEquals("daily", next.kind());
        assertTrue(next.note().contains("只在日线结算时决策"), next.note());
        assertFalse(next.note().contains("每 5 分钟"),
                "agent 模式盘中不再做规则检查，不能承诺盘中评估：" + next.note());
    }

    @Test
    void ruleModeMentionsTheIntradayCadence() {
        PaperSchedule.NextEvaluation next = PaperSchedule.next(
                ExecutionContract.DECISION_MODE_RULE, at(2026, 9, 18, 10, 0));

        assertEquals("realtime", next.kind());
        assertTrue(next.note().contains("每 5 分钟"), next.note());
    }

    @Test
    void legacyNullModeIsTreatedAsRule() {
        // 存量策略的 decisionMode 是 null —— 它们从来没切过，就是规则那一套
        PaperSchedule.NextEvaluation next = PaperSchedule.next(null, at(2026, 9, 18, 10, 0));
        assertEquals("realtime", next.kind());
    }

    @Test
    void theSentenceAlwaysAdmitsThereIsNoTradingCalendar() {
        String text = PaperSchedule.describe(
                PaperSchedule.next(ExecutionContract.DECISION_MODE_RULE, at(2026, 9, 18, 10, 0)));

        assertTrue(text.startsWith("下一次评估：2026-09-18 15:30"), text);
        assertTrue(text.contains("按工作日近似"), text);
        assertTrue(text.contains("节假日会空跑一次并留下原因"), text);
    }
}
