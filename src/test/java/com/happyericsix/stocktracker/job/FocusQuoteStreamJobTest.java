package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.SseEmitterService;
import com.happyericsix.stocktracker.service.StockService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 焦点快车道的语义：**只有**"SSE 订阅者正在图表页看的票"才 3 秒一发，
 * 且只在有行情的相位发。钉住三件事：相位门（closed 不发 / unknown 照发）、
 * 推送形状与 15s 批一致（QuoteStreamListener.toJson 是唯一权威）、
 * 没有焦点时静默返回。
 */
class FocusQuoteStreamJobTest {

    private StockService stockService;
    private SseEmitterService sseEmitterService;
    private MarketStatusService marketStatusService;
    private FocusQuoteStreamJob job;

    @BeforeEach
    void setUp() {
        stockService = mock(StockService.class);
        sseEmitterService = mock(SseEmitterService.class);
        marketStatusService = mock(MarketStatusService.class);
        job = new FocusQuoteStreamJob(stockService, sseEmitterService, marketStatusService);
    }

    private void phase(String value) {
        when(marketStatusService.current())
                .thenReturn(new MarketStatusResponse(
                        "2026-10-15", "THU", true, true, null, null, null,
                        !"closed".equals(value) && !"pre_open".equals(value), value, value,
                        null, null, null, null, null, null, null));
    }

    @Test
    void tradingPhasePushesFocusQuotesInBatchShape() {
        phase("afternoon");
        when(stockService.refreshFocusQuotes())
                .thenReturn(List.of(new RefreshedPrice("601988", 7.05, null)));

        job.pushFocusQuotes();

        // 形状必须与 15s 批完全一致：前端 quoteBus 只认 [{symbol, price}]
        verify(sseEmitterService).pushQuotes("[{\"symbol\":\"601988\",\"price\":7.05}]");
    }

    @Test
    void closedPhaseSkipsQuietly() {
        phase("closed");

        job.pushFocusQuotes();

        verify(stockService, never()).refreshFocusQuotes();
        verify(sseEmitterService, never()).pushQuotes(org.mockito.ArgumentMatchers.anyString());
    }

    @Test
    void unknownPhaseStillRuns() {
        // 日历不可用不能当成休市——与 StockPriceRefreshJob 同一条纪律
        when(marketStatusService.current()).thenThrow(new RuntimeException("calendar down"));
        when(stockService.refreshFocusQuotes())
                .thenReturn(List.of(new RefreshedPrice("601988", 7.05, null)));

        job.pushFocusQuotes();

        verify(sseEmitterService).pushQuotes("[{\"symbol\":\"601988\",\"price\":7.05}]");
    }

    @Test
    void noFocusSymbolsMeansNoPush() {
        phase("morning");
        when(stockService.refreshFocusQuotes()).thenReturn(List.of());

        job.pushFocusQuotes();

        verify(sseEmitterService, never()).pushQuotes(org.mockito.ArgumentMatchers.anyString());
    }

    @Test
    void aFailureDoesNotPropagate() {
        phase("morning");
        when(stockService.refreshFocusQuotes()).thenThrow(new RuntimeException("python down"));

        // 快车道挂了不能把定时线程打挂——下一轮 3 秒后还会再来
        job.pushFocusQuotes();

        verify(sseEmitterService, never()).pushQuotes(org.mockito.ArgumentMatchers.anyString());
        assertTrue(true, "到达这里说明异常被兜住");
    }
}
