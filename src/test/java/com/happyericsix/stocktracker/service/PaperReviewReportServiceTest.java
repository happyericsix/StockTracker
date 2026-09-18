package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 盘后复盘报告：**引用痕迹与验证结论**，并且不许假装。
 *
 * <h3>为什么这些用例是这一层的核心</h3>
 * 报告的失败方式不是崩溃，而是"写得像那么回事"：把"没验证过"写成"表现平平"、
 * 把过期的结论当成此刻的证据、把"今天什么都没发生"每天推一条。
 * 这些都不会报错，只会让用户慢慢不再打开它 —— 那正是模拟盘最常见的死法。
 */
class PaperReviewReportServiceTest {

    private final PaperTraceService traceService = mock(PaperTraceService.class);
    private final StrategyService strategyService = mock(StrategyService.class);
    private final MessageService messageService = mock(MessageService.class);
    private final PaperReviewReportService service =
            new PaperReviewReportService(traceService, strategyService, messageService);

    private static final LocalDate DAY = LocalDate.of(2026, 9, 17);

    private static Strategy strategy() {
        return Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .user(User.builder().id(1L).username("alice").email("a@b.c").password("x").build())
                .build();
    }

    private static PaperAccount account() {
        return PaperAccount.builder()
                .id(5L).initialCapital(100000.0).cash(100.0).shares(900.0)
                .avgCost(10.0).equity(100900.0).build();
    }

    private static PaperTradeTrace trace(String decision, String skipReason, String barTime) {
        return PaperTradeTrace.builder()
                .id(1L).strategy(strategy()).settlementKind(ExecutionContract.SETTLEMENT_REALTIME)
                .trigger(ExecutionContract.TRIGGER_EVENT).tradeDate(DAY).barTime(barTime)
                .symbol("600519").decision(decision).skipReason(skipReason)
                .fillBasis(ExecutionContract.FILL_REALTIME_LAST)
                .repeatCount(1)
                .build();
    }

    // ==================== 1. 什么值得推一条消息 ====================

    @Test
    void aBlockingSkipIsWorthTelling() {
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED, "2026-09-17 09:40:00")));
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_LIMIT_BLOCKED, "2026-09-17 09:40:00")));
    }

    @Test
    void aQuietDayIsNotWorthATableMessage() {
        // "规则没成立""指标没算出来"是**正常状态**：每天推一条等于训练用户忽略这类消息
        assertFalse(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        assertFalse(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_WARMUP, null)));
    }

    @Test
    void aFillIsAlwaysWorthTelling() {
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SELL, null, null)));
    }

    @Test
    void aQuietDayProducesNoMessage() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));

        Message message = service.composeDailyReport(strategy(), account(), DAY);

        assertNull(message);
        verify(messageService, never()).saveReport(any(), anyString(), anyString(), any(), anyString());
    }

    @Test
    void aDayWithoutAnyTraceProducesNoMessageEither() {
        // 连痕迹都没有 = 结算没走到留痕那一步。这时候发"今天很平静"是**假话**
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of());

        assertNull(service.composeDailyReport(strategy(), account(), DAY));
        verify(messageService, never()).saveReport(any(), anyString(), anyString(), any(), anyString());
    }

    // ==================== 2. 日报内容：引用痕迹 + 验证结论 ====================

    @Test
    void theDailyReportCitesTheTraceAndTheVerification() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED, "2026-09-17 09:40:00"),
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 21, 10, -1.35, 1.883, "2a9dce9df73b"));

        service.composeDailyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        verify(messageService, times(1)).saveReport(any(), eq(PaperReviewReportService.TYPE_PAPER_REPORT),
                key.capture(), eq("600519"), content.capture());

        String text = content.getValue();
        // 引用痕迹：那句人话直接来自痕迹（同一套翻译，不另写一份）
        assertTrue(text.contains("T+1：当日买入的当日不能卖"), text);
        assertTrue(text.contains("2026-09-17"), text);
        // 引用验证结论：样本量 + 结论 + 摩擦 + 口径
        assertTrue(text.contains("21 个有效格子里 10 格跑赢买入持有"), text);
        assertTrue(text.contains("-1.35%"), text);
        assertTrue(text.contains("1.883%"), text);
        assertTrue(text.contains("2a9dce9df73b"), text);
        // 账户一行也在
        assertTrue(text.contains("净值 100900.00"), text);
        // 幂等键带上策略与日期：重跑不会推第二条
        assertEquals("paper_daily:10:2026-09-17", key.getValue());
    }

    @Test
    void aNeverVerifiedStrategyIsSaidToBeUnverifiedNotToBeFlat() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        service.composeDailyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), anyString(), anyString(), any(), content.capture());
        assertTrue(content.getValue().contains("还没有做过样本外验证"), content.getValue());
        assertTrue(content.getValue().contains("偶然"), "没有验证时必须提醒这可能是偶然");
    }

    @Test
    void aVerificationWithoutSampleIsNotPresentedAsAConclusion() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 0, 0, null, null, ""));

        service.composeDailyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), anyString(), anyString(), any(), content.capture());
        assertTrue(content.getValue().contains("没有可用样本"), content.getValue());
        assertTrue(content.getValue().contains("不构成结论"), content.getValue());
    }

    @Test
    void aStaleVerificationIsMarkedStale() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-08-01T09:00:00", 21, 10, -1.35, 1.883, "abc"));

        service.composeDailyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), anyString(), anyString(), any(), content.capture());
        assertTrue(content.getValue().contains("未更新"), content.getValue());
        assertTrue(content.getValue().contains("不能当作此刻的证据"), content.getValue());
    }

    @Test
    void aFreshVerificationIsNotMarkedStale() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 21, 10, -1.35, 1.883, "abc"));

        service.composeDailyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), anyString(), anyString(), any(), content.capture());
        assertFalse(content.getValue().contains("不能当作此刻的证据"), content.getValue());
    }

    // ==================== 3. 空转期：沉默本身也是信息 ====================

    @Test
    void theWeeklyReportStatesTheMinimumSampleGateInsteadOfCryingWolf() {
        // 连续 3 天没动 ≠ "异常沉默"。不到门槛就说"还不能判断"，不制造"它坏了"的暗示
        when(traceService.listRecent(eq(10L), any(Integer.class))).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        service.composeIdleWeeklyReport(strategy(), account(), DAY);

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), anyString(), anyString(), any(), content.capture());
        assertTrue(content.getValue().contains("空转期"), content.getValue());
        assertTrue(content.getValue().contains(String.valueOf(PaperReviewReportService.IDLE_DAYS_THRESHOLD)),
                content.getValue());
    }

    @Test
    void theWeeklyReportIsSkippedWhenTheWeekAlreadyHadANoteworthyDay() {
        PaperTradeTrace blocking = trace(ExecutionContract.DECISION_SKIP,
                ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT, null);
        blocking.setTradeDate(DAY.minusDays(2));
        when(traceService.listRecent(eq(10L), any(Integer.class))).thenReturn(List.of(blocking));

        assertNull(service.composeIdleWeeklyReport(strategy(), account(), DAY));
        verify(messageService, never()).saveReport(any(), anyString(), anyString(), any(), anyString());
    }

    @Test
    void aReportFailureNeverEscapes() {
        // 报告是增强：结算与痕迹都已落库，推不出去只是少一条消息
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(messageService.saveReport(any(), anyString(), anyString(), any(), anyString()))
                .thenThrow(new RuntimeException("db down"));

        assertNull(service.composeDailyReport(strategy(), account(), DAY));
    }

    @Test
    void theSameDayIsNeverDeliveredTwice() {
        // 幂等由消息表上的唯一键兜底（MessageService.saveReport），这里钉住"键是稳定的"
        assertEquals(PaperReviewReportService.dailyDedupeKey(10L, DAY),
                PaperReviewReportService.dailyDedupeKey(10L, DAY));
        assertNotNull(PaperReviewReportService.dailyDedupeKey(10L, DAY));
    }
}
