package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 月度小结/季度策略信的语义：区间统计的**基期口径**（上月末那格）、幂等即调度
 * （发过就不再花 LLM 的钱）、季末月升级为季度信、评语失败不挡数字底座。
 */
class PaperPeriodicReviewServiceTest {

    private StrategyRepository strategyRepository;
    private PaperAccountRepository paperAccountRepository;
    private PaperTradeRepository paperTradeRepository;
    private PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private MemoryLessonService memoryLessonService;
    private PaperReviewReportService paperReviewReportService;
    private StrategyClient strategyClient;
    private MessageService messageService;
    private PushService pushService;
    private PaperPeriodicReviewService service;

    @BeforeEach
    void setUp() {
        strategyRepository = mock(StrategyRepository.class);
        paperAccountRepository = mock(PaperAccountRepository.class);
        paperTradeRepository = mock(PaperTradeRepository.class);
        paperEquitySnapshotRepository = mock(PaperEquitySnapshotRepository.class);
        memoryLessonService = mock(MemoryLessonService.class);
        paperReviewReportService = mock(PaperReviewReportService.class);
        strategyClient = mock(StrategyClient.class);
        messageService = mock(MessageService.class);
        pushService = mock(PushService.class);
        service = new PaperPeriodicReviewService(strategyRepository, paperAccountRepository,
                paperTradeRepository, paperEquitySnapshotRepository, memoryLessonService,
                paperReviewReportService, strategyClient, messageService, pushService);
        when(paperReviewReportService.verificationLine(any(), any())).thenReturn("规则证据：（测试占位）");
    }

    private static PaperEquitySnapshot snap(LocalDate date, String equity, String close) {
        return PaperEquitySnapshot.builder()
                .tradeDate(date)
                .equity(new BigDecimal(equity))
                .closePrice(close == null ? null : new BigDecimal(close))
                .build();
    }

    private static PaperTrade trade(LocalDate date, String side, String realizedPnl) {
        return PaperTrade.builder()
                .tradeDate(date)
                .side(side)
                .price(new BigDecimal("10.00"))
                .shares(new BigDecimal("100"))
                .realizedPnl(realizedPnl == null ? null : new BigDecimal(realizedPnl))
                .build();
    }

    // ==================== computeStats：基期与窗口 ====================

    @Test
    void statsUseLastSnapshotBeforeWindowAsBase() {
        // 8/31 净值 100 是基期，9 月两格 110/120 → 区间收益 +20%（不是 +9.09%）
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(List.of(
                snap(LocalDate.of(2026, 8, 31), "100", "10.00"),
                snap(LocalDate.of(2026, 9, 1), "110", "10.50"),
                snap(LocalDate.of(2026, 9, 30), "120", "11.00")));
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(1L)).thenReturn(List.of(
                trade(LocalDate.of(2026, 9, 2), "BUY", null),
                trade(LocalDate.of(2026, 9, 10), "SELL", "50"),
                trade(LocalDate.of(2026, 9, 20), "SELL", "-20"),
                trade(LocalDate.of(2026, 10, 5), "BUY", null)));   // 窗口外，不计入

        PaperPeriodicReviewService.PeriodStats stats = service.computeStats(
                1L, PaperPeriodicReviewService.Period.ofMonth(java.time.YearMonth.of(2026, 9)));

        assertEquals(3, stats.snapshotDays());            // 基期 + 9 月两格
        assertEquals(0, BigDecimal.valueOf(20.0).compareTo(stats.returnPct()));
        assertEquals(1, stats.buys());
        assertEquals(2, stats.sells());
        assertEquals(2, stats.closedSells());
        assertEquals(50, stats.winRatePct());             // 一笔 +50 一笔 -20 → 50%
        assertEquals(0, BigDecimal.valueOf(30).compareTo(stats.realizedPnl()));
        // 超额 = 20% − 10%（买入持有 10.00 → 11.00）
        assertEquals(0, BigDecimal.valueOf(10.0).compareTo(stats.excessVsBuyAndHoldPct()));
    }

    @Test
    void noSnapshotsInsideWindowMeansNotRunning() {
        // 只有 8 月的快照：9 月策略没在跑，统计必须返回 null（不假装）
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(List.of(
                snap(LocalDate.of(2026, 8, 31), "100", "10.00")));
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(1L))
                .thenReturn(List.of());

        assertNull(service.computeStats(1L,
                PaperPeriodicReviewService.Period.ofMonth(java.time.YearMonth.of(2026, 9))));
    }

    // ==================== 幂等即调度 ====================

    private void stubStrategy() {
        User user = User.builder().id(7L).username("alice").build();
        Strategy strategy = Strategy.builder()
                .id(1L).user(user).symbol("601988").name("银行底仓").decisionMode("agent")
                .build();
        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(paperAccountRepository.findByStrategyId(1L)).thenReturn(Optional.of(
                PaperAccount.builder().equity(new BigDecimal("120000"))
                        .cash(new BigDecimal("20000")).shares(new BigDecimal("10000")).build()));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(List.of(
                snap(LocalDate.of(2026, 8, 31), "100000", "6.90"),
                snap(LocalDate.of(2026, 9, 30), "105000", "7.10"),
                snap(LocalDate.of(2026, 10, 30), "106000", "7.15")));
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(1L))
                .thenReturn(List.of());
    }

    @Test
    void alreadySentMonthSkipsWithoutLlmCall() {
        stubStrategy();
        when(messageService.reportSent(anyString())).thenReturn(true);

        service.runPeriodicReports(LocalDate.of(2026, 11, 20));   // 上月 = 10 月（非季末）

        verify(messageService, never()).saveReport(any(), anyString(), anyString(), anyString(), anyString());
        verify(strategyClient, never()).quarterlyReview(any(), any());
        verify(pushService, never()).push(anyString(), anyString(), anyString());
    }

    @Test
    void nonQuarterMonthSendsMonthlySummaryWithoutLlm() {
        stubStrategy();
        when(messageService.reportSent(anyString())).thenReturn(false);

        service.runPeriodicReports(LocalDate.of(2026, 11, 20));   // 上月 = 10 月

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), eq("STRATEGY_REVIEW"),
                key.capture(), eq("601988"), content.capture());
        assertTrue(key.getValue().startsWith("periodic:monthly:1:2026-10"));
        assertTrue(content.getValue().contains("月度小结"));
        assertTrue(content.getValue().contains("区间收益"));       // 数字底座在场
        verify(strategyClient, never()).quarterlyReview(any(), any());  // 月度小结不调模型
        verify(pushService).push(eq("alice"), anyString(), anyString());
    }

    @Test
    void quarterEndMonthSendsQuarterlyLetterWithCommentary() {
        stubStrategy();
        when(messageService.reportSent(anyString())).thenReturn(false);
        tools.jackson.databind.ObjectMapper m = new tools.jackson.databind.ObjectMapper();
        ObjectNode letter = m.createObjectNode();
        ObjectNode block = letter.putObject("letter");
        block.put("verdict", "本季逻辑仍然成立。");
        block.put("suggestions", "提议：把止损线从 5% 收到 4%（需你确认）。");
        when(strategyClient.quarterlyReview(any(), any())).thenReturn(letter);

        service.runPeriodicReports(LocalDate.of(2026, 10, 16));   // 上月 = 9 月（季末）→ Q3 信

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), eq("STRATEGY_REVIEW"),
                key.capture(), eq("601988"), content.capture());
        assertTrue(key.getValue().startsWith("periodic:quarterly:1:2026-Q3"));
        assertTrue(content.getValue().contains("季度策略信"));
        assertTrue(content.getValue().contains("本季逻辑仍然成立"));
        assertTrue(content.getValue().contains("需你确认"));      // 提议不是动作
    }

    @Test
    void strategyBornAfterPeriodIsSkippedEntirely() {
        // 策略 10-08 才有第一个快照：Q3（截至 9-30）它还不存在 —— 不发"本期没有快照"的空信
        stubStrategy();
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(List.of(
                snap(LocalDate.of(2026, 10, 8), "100000", "6.90"),
                snap(LocalDate.of(2026, 10, 30), "106000", "7.15")));
        when(messageService.reportSent(anyString())).thenReturn(false);

        service.runPeriodicReports(LocalDate.of(2026, 10, 16));   // 上月 = 9 月（季末）

        verify(messageService, never()).saveReport(any(), anyString(), anyString(), anyString(), anyString());
        verify(strategyClient, never()).quarterlyReview(any(), any());
    }

    @Test
    void silentPeriodWhileRunningStillSends() {
        // 策略 8 月在跑、9 月一格快照都没有（异常沉默）：那封"本期没有结算快照"的信必须发
        stubStrategy();
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(List.of(
                snap(LocalDate.of(2026, 8, 31), "100000", "6.90")));
        when(messageService.reportSent(anyString())).thenReturn(false);

        service.runPeriodicReports(LocalDate.of(2026, 10, 16));

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), eq("STRATEGY_REVIEW"),
                anyString(), eq("601988"), content.capture());
        assertTrue(content.getValue().contains("本期没有结算快照"));
    }

    @Test
    void commentaryFailureStillSendsDeterministicLetter() {
        stubStrategy();
        when(messageService.reportSent(anyString())).thenReturn(false);
        when(strategyClient.quarterlyReview(any(), any())).thenThrow(new RuntimeException("python down"));

        service.runPeriodicReports(LocalDate.of(2026, 10, 16));

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), eq("STRATEGY_REVIEW"),
                anyString(), eq("601988"), content.capture());
        assertTrue(content.getValue().contains("策略评语：本季未生成"));   // 明说，不假装
        assertTrue(content.getValue().contains("区间收益"));        // 数字底座照发
    }
}
