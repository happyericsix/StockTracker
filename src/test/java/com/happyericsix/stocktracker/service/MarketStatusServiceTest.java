package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 市场状态服务：**"不知道"必须说成不知道**。
 *
 * <p>这个类的价值全在降级路径上。正常路径（Python 返回一份状态）只是透传，
 * 真正会出事的是"交易日历拿不到"那一支：如果那时给出 {@code tradingDay=false}，
 * 前端会显示"今日休市"；如果给出 {@code true}，前端会把上一交易日的收盘价
 * 当成今天的实时价 —— 前者是虚惊，后者正是本次要修的那个 bug。
 */
@ExtendWith(MockitoExtension.class)
class MarketStatusServiceTest {

    @Mock
    private AkshareStockClient client;

    private MarketStatusService service;

    @BeforeEach
    void setUp() {
        service = new MarketStatusService(client);
    }

    private static MarketStatusResponse status(boolean tradingDay) {
        return new MarketStatusResponse(
                "2026-09-20", "周日", true, true, "1990-12-19", "2026-12-31", "akshare",
                tradingDay, tradingDay ? "morning" : "closed", tradingDay ? "交易中" : "今日休市",
                "2026-09-18", "2026-09-18", "2026-09-21", 2, "2026-09-19", "2026-09-20",
                tradingDay ? "交易中" : "今日周末休市（周日）");
    }

    @Test
    void returnsWhatTheDataServiceSays() {
        when(client.getMarketStatus()).thenReturn(status(false));

        MarketStatusResponse result = service.current();

        assertEquals(Boolean.FALSE, result.tradingDay());
        assertEquals("2026-09-18", result.quoteDate());
        assertEquals(2, result.restDays());
        assertEquals("2026-09-21", result.nextTradingDay());
    }

    @Test
    void cachesSoThatEveryQuoteRequestDoesNotHitTheDataService() {
        // 行情卡与个股页都会问一次；不缓存就是"每次渲染多一次 HTTP 往返"。
        when(client.getMarketStatus()).thenReturn(status(false));

        service.current();
        service.current();

        verify(client, times(1)).getMarketStatus();
    }

    @Test
    void reportsUnknownInsteadOfGuessingWhenTheCalendarIsUnavailable() {
        when(client.getMarketStatus()).thenReturn(null);

        MarketStatusResponse result = service.current();

        assertNotNull(result, "永远不能返回 null：调用方只是想在界面上说明白这是哪天的数据");
        assertFalse(result.known(), "日历不可用 → known 必须是 false");
        assertEquals("none", result.calendarSource());
        // ⚠️ 关键：tradingDay 是 **null**（不知道），不是 false（确认休市）。
        // 写成 false 会让界面显示"今日休市"——那是把一个不确定的事说成确定的。
        assertNull(result.tradingDay(), "不知道就说不知道，不能猜成休市");
        assertTrue(result.note().contains("无法确认"), "必须把原因写在文案里：" + result.note());
        // "今天几号、周几"是 Java 自己能确定的，仍然给出来 —— 界面因此还能显示一个正常的日期
        assertNotNull(result.date());
        assertNotNull(result.weekday());
    }

    @Test
    void swallowsClientFailuresSoThePageStillRenders() {
        // 行情卡只是想在界面上加一句"这是哪天的数据"。这个功能出问题不该让行情消失。
        when(client.getMarketStatus()).thenThrow(new IllegalStateException("连接被重置"));

        MarketStatusResponse result = service.current();

        assertFalse(result.known());
    }
}
