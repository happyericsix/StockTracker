package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.SentimentClient;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.MarketSentimentSnapshot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.MarketSentimentSnapshotRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 散户情绪的 Java 侧口径：参与意愿验证是 Python validate_against_price 的
 * **镜像**——这里的用例钉住镜像语义（样本不足/秩分桶/σ 门槛）与快照幂等键。
 */
@ExtendWith(MockitoExtension.class)
class MarketSentimentServiceTest {

    private static final ObjectMapper MAPPER = new ObjectMapper();

    @Mock
    private SentimentClient sentimentClient;
    @Mock
    private MarketSentimentSnapshotRepository snapshotRepository;
    @Mock
    private FavoriteStockRepository favoriteStockRepository;
    @Mock
    private StrategyRepository strategyRepository;
    @Mock
    private StockDataGateway stockDataGateway;
    @InjectMocks
    private MarketSentimentService service;

    private MarketSentimentSnapshot snap(LocalDate date, double desire) {
        return MarketSentimentSnapshot.builder()
                .symbol("600519").tradeDate(date).desire(desire).focus(90.0).score(80.0)
                .build();
    }

    private StockHistoryResponse closes(Map<String, String> series) {
        Map<String, StockHistoryResponse.DailyPrice> map = new LinkedHashMap<>();
        series.forEach((d, c) -> map.put(d,
                new StockHistoryResponse.DailyPrice(c, c, c, c, "1")));
        return new StockHistoryResponse(null, map);
    }

    @Test
    void desireValidationInsufficientSamplesKeepsHonestNote() {
        when(snapshotRepository.findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
                anyString(), any(LocalDate.class))).thenReturn(List.of(snap(LocalDate.of(2026, 9, 30), 55)));

        Map<String, Object> out = service.desireValidation("600519");

        assertEquals("样本不足", out.get("verdict"));
        assertTrue(String.valueOf(out.get("note")).contains("快照累积"));
    }

    @Test
    void desireValidationMirrorsRankSplitAndSigmaScreen() {
        // 高意愿日次日必涨、低意愿日次日必跌 → diff=1.0，远超小样本噪声门槛 → 有信息量
        List<MarketSentimentSnapshot> snapshots = new java.util.ArrayList<>();
        Map<String, String> series = new LinkedHashMap<>();
        double level = 100.0;
        LocalDate start = LocalDate.of(2026, 7, 1);
        for (int d = 0; d < 24; d++) {
            LocalDate date = start.plusDays(d);
            snapshots.add(snap(date, d % 2 == 0 ? 70.0 : 30.0));
            series.put(date.toString(), String.valueOf(level));
            level += d % 2 == 0 ? 1 : -1;
        }
        series.put(start.plusDays(24).toString(), String.valueOf(level));
        when(snapshotRepository.findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
                anyString(), any(LocalDate.class))).thenReturn(snapshots);
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(closes(series));

        Map<String, Object> out = service.desireValidation("600519");

        assertEquals(24, out.get("samples"));
        assertTrue(String.valueOf(out.get("verdict")).contains("有信息量"), String.valueOf(out));
        assertTrue(String.valueOf(out.get("verdict")).contains("非严格检验"));
        assertTrue(String.valueOf(out.get("verdict")).contains("噪声门槛"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void getSentimentReplacesPythonDesireVerdictWithSnapshotBasedOne() {
        ObjectNode payload = MAPPER.createObjectNode();
        payload.put("symbol", "600519");
        payload.put("disclaimer", "未验证不构成依据");
        payload.putObject("desire").put("latest", 56.4);
        payload.putObject("focus").put("latest", 94.8);
        ObjectNode validation = payload.putObject("validation");
        validation.putObject("desire").put("verdict", "样本不足");
        ObjectNode root = MAPPER.createObjectNode();
        root.put("ok", true);
        root.set("data", payload);

        when(sentimentClient.getSentiment("600519")).thenReturn(root);
        when(snapshotRepository.findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
                anyString(), any(LocalDate.class))).thenReturn(List.of());
        lenient().when(stockDataGateway.getStockHistory(anyString(), anyString())).thenReturn(null);

        JsonNode out = service.getSentiment("600519");

        assertTrue(out.path("ok").asBoolean());
        // Python 的"样本不足"占位被 Java 快照版验证覆盖；focus 的 Python 结论原样保留
        assertTrue(out.path("data").path("validation").path("desire").has("samples"));
    }

    @Test
    void getSentimentPassesThroughWhenPythonUnavailable() {
        when(sentimentClient.getSentiment("600519")).thenReturn(null);
        assertEquals(null, service.getSentiment("600519"));
        verify(snapshotRepository, never()).findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
                anyString(), any(LocalDate.class));
    }

    @Test
    void snapshotUsesUpstreamTradeDateAsIdempotentKey() {
        ObjectNode payload = MAPPER.createObjectNode();
        ObjectNode desire = payload.putObject("desire");
        desire.put("latest", 56.4);
        desire.put("avg5", 50.6);
        ObjectNode desireRow = desire.putArray("series").addObject();
        desireRow.put("date", "2026-09-30");
        desireRow.put("value", 56.4);
        ObjectNode focus = payload.putObject("focus");
        focus.put("latest", 94.8);
        ObjectNode focusRow = focus.putArray("series").addObject();
        focusRow.put("date", "2026-09-30");
        focusRow.put("value", 94.8);
        payload.putObject("score").put("latest", 79.7);
        ObjectNode root = MAPPER.createObjectNode();
        root.put("ok", true);
        root.set("data", payload);

        FavoriteStock fav = new FavoriteStock();
        fav.setStockSymbol("600519");
        when(favoriteStockRepository.findAll()).thenReturn(List.of(fav));
        when(strategyRepository.findAll()).thenReturn(List.of());
        when(sentimentClient.getSentiment("600519")).thenReturn(root);
        // 同一交易日已有快照 → 覆盖更新，不新增行
        MarketSentimentSnapshot existing = MarketSentimentSnapshot.builder()
                .symbol("600519").tradeDate(LocalDate.of(2026, 9, 30)).desire(50.0).build();
        when(snapshotRepository.findBySymbolAndTradeDate(eq("600519"), eq(LocalDate.of(2026, 9, 30))))
                .thenReturn(Optional.of(existing));

        int saved = service.snapshotAll();

        assertEquals(1, saved);
        // 幂等键 = 上游给的最后交易日（2026-09-30），不是"今天"
        verify(snapshotRepository).findBySymbolAndTradeDate("600519", LocalDate.of(2026, 9, 30));
        assertEquals(56.4, existing.getDesire(), 0.0001);
        assertEquals(94.8, existing.getFocus(), 0.0001);
        verify(snapshotRepository).save(existing);
    }
}
