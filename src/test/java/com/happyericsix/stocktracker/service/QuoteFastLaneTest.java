package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.listener.PaperTradingListener;
import com.happyericsix.stocktracker.listener.QuoteStreamListener;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;

/**
 * 快车道（2026-10-08）的两条纪律：
 * ① 价格刷新升到 15 秒一次后，模拟盘结算**不能**跟着 15 秒一轮 —— 45 秒节流；
 * ② 每次刷新的快照要变成一批最小形状的 JSON 推给行情通道。
 */
@ExtendWith(MockitoExtension.class)
class QuoteFastLaneTest {

    @Mock
    private PaperTradingService paperTradingService;

    @Mock
    private SseEmitterService sseEmitterService;

    @InjectMocks
    private PaperTradingListener paperTradingListener;

    @Test
    void rapidPriceEventsTriggerAtMostOneSettlementPerThrottleWindow() {
        PricesRefreshedEvent event = new PricesRefreshedEvent(List.of(), Instant.now());

        // 15 秒节奏下连发的四个事件：只应触发一轮结算（其余是对同一窗口的重复）
        paperTradingListener.onPricesRefreshed(event);
        paperTradingListener.onPricesRefreshed(event);
        paperTradingListener.onPricesRefreshed(event);
        paperTradingListener.onPricesRefreshed(event);

        verify(paperTradingService, times(1)).evaluateRealtime();
    }

    @Test
    void everyRefreshBecomesOneQuoteBatchPush() {
        QuoteStreamListener listener = new QuoteStreamListener(sseEmitterService);
        PricesRefreshedEvent event = new PricesRefreshedEvent(List.of(
                new RefreshedPrice("601988", 6.81, null),
                new RefreshedPrice("600519", 1680.0, null)
        ), Instant.now());

        listener.onPricesRefreshed(event);

        ArgumentCaptor<String> json = ArgumentCaptor.forClass(String.class);
        verify(sseEmitterService, times(1)).pushQuotes(json.capture());
        assertTrue(json.getValue().contains("601988"));
        assertTrue(json.getValue().contains("6.81"));
        assertTrue(json.getValue().contains("600519"));

        // 空批次不推：没有连接该安静，别发空数组空耗
        listener.onPricesRefreshed(new PricesRefreshedEvent(List.of(), Instant.now()));
        verify(sseEmitterService, times(1)).pushQuotes(anyString());
    }

    @Test
    void pushFailureNeverBreaksTheRefreshPipeline() {
        QuoteStreamListener listener = new QuoteStreamListener(sseEmitterService);
        org.mockito.Mockito.doThrow(new RuntimeException("emitter dead"))
                .when(sseEmitterService).pushQuotes(anyString());

        // 旁路纪律：推送挂了只记日志，异常绝不能冒回事件发布方（那会拖垮刷新主流程）
        listener.onPricesRefreshed(new PricesRefreshedEvent(List.of(
                new RefreshedPrice("601988", 6.81, null)), Instant.now()));

        verify(sseEmitterService, times(1)).pushQuotes(anyString());
    }
}
