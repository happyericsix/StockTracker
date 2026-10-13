package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import com.happyericsix.stocktracker.service.StockService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 定时任务的市场闸门：休市/收盘后不再产生任何"对旧价空转"的副作用。
 *
 * 这组用例钉的是一次真实的线上噪音（2026-10 国庆）：
 * 休市日价格刷新照跑 → 模拟盘盘中评估一天留 186 条"等待授权"痕迹；
 * 15:30 结算假日照跑 → 每天发一条"今天没有 K 线"的复盘消息。
 * 闸门的原则：**只有日历明确说休市才拦**，unknown 一律放行（宁空转不漏算）。
 */
@ExtendWith(MockitoExtension.class)
class MarketSessionGateTest {

    @Mock
    private StockService stockService;
    @Mock
    private ApplicationEventPublisher publisher;
    @Mock
    private MarketStatusService marketStatus;
    @Mock
    private PaperTradingService paperTradingService;
    @Mock
    private com.happyericsix.stocktracker.service.JobRunService jobRunService;

    @BeforeEach
    void executeHeartbeatWrappers() {
        // 心跳包装默认是 mock（直接吞掉任务体）；这里让它像真实现一样执行 lambda，
        // 否则"闸门放行/拦截"根本没机会发生。lenient：价格刷新那几条用不到它。
        org.mockito.Mockito.lenient().doAnswer(invocation -> {
            com.happyericsix.stocktracker.service.JobRunService.Heartbeated action =
                    invocation.getArgument(1);
            action.run();
            return null;
        }).when(jobRunService).runWithHeartbeat(org.mockito.ArgumentMatchers.anyString(),
                org.mockito.ArgumentMatchers.any());
    }

    private static MarketStatusResponse status(Boolean tradingDay, String phase) {
        return new MarketStatusResponse(
                "2026-10-07", "周三", true, true,
                "1990-12-19", "2026-12-31", "akshare",
                tradingDay, phase, "标签", "2026-09-30",
                "2026-09-30", "2026-10-08",
                7, "2026-10-01", "2026-10-07", "");
    }

    // ==================== 价格刷新（每 5 分钟） ====================

    @Test
    void refreshIsSkippedOnHolidayAndAfterClose() {
        StockPriceRefreshJob job = new StockPriceRefreshJob(stockService, publisher, marketStatus);
        for (String phase : new String[]{"closed", "post_close", "pre_open"}) {
            when(marketStatus.current()).thenReturn(status(false, phase));
            job.refreshFavoritePrices();
        }
        verify(stockService, never()).refreshFavorites();
        verify(publisher, never()).publishEvent(any());
    }

    @Test
    void refreshRunsDuringTradingSessions() {
        StockPriceRefreshJob job = new StockPriceRefreshJob(stockService, publisher, marketStatus);
        when(marketStatus.current()).thenReturn(status(true, "morning"));
        when(stockService.refreshFavorites()).thenReturn(List.<RefreshedPrice>of());

        job.refreshFavoritePrices();

        verify(stockService).refreshFavorites();
        ArgumentCaptor<Object> captor = ArgumentCaptor.forClass(Object.class);
        verify(publisher).publishEvent(captor.capture());
        assertThat(captor.getValue()).isInstanceOf(PricesRefreshedEvent.class);
    }

    @Test
    void refreshFailsOpenWhenCalendarUnknown() {
        // unknown = 日历拿不到：宁可对旧价空转，也不能停掉交易时段的刷新
        StockPriceRefreshJob job = new StockPriceRefreshJob(stockService, publisher, marketStatus);
        when(marketStatus.current()).thenReturn(status(null, "unknown"));
        when(stockService.refreshFavorites()).thenReturn(List.<RefreshedPrice>of());

        job.refreshFavoritePrices();

        verify(stockService).refreshFavorites();
    }

    // ==================== 日线结算（15:30）与周频复盘（周五 16:30） ====================

    @Test
    void dailySettlementIsSkippedOnConfirmedHoliday() {
        PaperTradingJob job = new PaperTradingJob(paperTradingService, marketStatus, jobRunService);
        when(marketStatus.current()).thenReturn(status(false, "closed"));

        job.runDailyPaper();

        verify(paperTradingService, never()).evaluateDaily();
    }

    @Test
    void dailySettlementRunsWhenCalendarUnknown() {
        PaperTradingJob job = new PaperTradingJob(paperTradingService, marketStatus, jobRunService);
        when(marketStatus.current()).thenReturn(status(null, "unknown"));

        job.runDailyPaper();

        verify(paperTradingService).evaluateDaily();
    }

    @Test
    void weeklyReviewIsSkippedOnConfirmedHoliday() {
        PaperReviewJob job = new PaperReviewJob(paperTradingService, marketStatus, jobRunService);
        when(marketStatus.current()).thenReturn(status(false, "closed"));

        job.runWeeklyReview();

        verify(paperTradingService, never()).runWeeklyReview();
    }

    @Test
    void weeklyReviewRunsOnTradingDay() {
        PaperReviewJob job = new PaperReviewJob(paperTradingService, marketStatus, jobRunService);
        when(marketStatus.current()).thenReturn(status(true, "post_close"));

        job.runWeeklyReview();

        verify(paperTradingService).runWeeklyReview();
    }
}
