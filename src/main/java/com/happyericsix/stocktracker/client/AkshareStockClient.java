package com.happyericsix.stocktracker.client;

import com.happyericsix.stocktracker.dto.DailyStockResponse;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.dto.StockOverviewResponse;
import com.happyericsix.stocktracker.dto.StockQuoteResponse;
import com.happyericsix.stocktracker.dto.StockSearchItem;
import com.happyericsix.stocktracker.dto.StockSearchResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.util.Collections;
import java.util.List;
import java.util.Map;

/**
 * 通过 WebClient 调用 Python akshare 数据微服务的客户端。
 * 替代原有的 ChoiceStockClient（东方财富 DLL 接口）。
 *
 * Python 服务地址由配置属性 akshare.api.base-url 控制，
 * 默认 http://localhost:8000
 */
@Service
public class AkshareStockClient {

    private static final Logger log = LoggerFactory.getLogger(AkshareStockClient.class);

    private final WebClient webClient;

    public AkshareStockClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl) {
        this.webClient = WebClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8")
                .build();
        log.info("AkshareStockClient initialized, base URL: {}", baseUrl);
    }

    // ==================== 股票搜索 (Autocomplete) ====================

    /**
     * 搜索股票代码/名称，用于自动补全下拉提示。
     */
    @SuppressWarnings("unchecked")
    public StockSearchResponse searchStocks(String keyword) {
        try {
            Map<String, Object> raw = webClient.get()
                    .uri(uriBuilder -> uriBuilder.path("/api/v1/stocks/search")
                            .queryParam("keyword", keyword)
                            .build())
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .block();

            if (raw == null) {
                return new StockSearchResponse(keyword, 0, Collections.emptyList());
            }

            int count = raw.get("count") instanceof Number n ? n.intValue() : 0;
            List<Map<String, String>> resultsRaw = (List<Map<String, String>>) raw.get("results");
            List<StockSearchItem> items = resultsRaw == null ? Collections.emptyList()
                    : resultsRaw.stream()
                            .map(m -> new StockSearchItem(m.get("code"), m.get("name")))
                            .toList();

            return new StockSearchResponse(keyword, count, items);
        } catch (WebClientResponseException e) {
            log.warn("akshare search error for {}: HTTP {} {}", keyword, e.getStatusCode(), e.getResponseBodyAsString());
            return new StockSearchResponse(keyword, 0, Collections.emptyList());
        } catch (Exception e) {
            log.warn("akshare search exception for {}: {}", keyword, e.getMessage());
            return new StockSearchResponse(keyword, 0, Collections.emptyList());
        }
    }

    // ==================== 实时行情 ====================

    public StockQuoteResponse getStockQuote(String symbol) {
        try {
            return webClient.get()
                    .uri("/api/v1/quote/{symbol}", symbol)
                    .retrieve()
                    .bodyToMono(StockQuoteResponse.class)
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("akshare quote error for {}: HTTP {} {}", symbol, e.getStatusCode(), e.getResponseBodyAsString());
            return emptyQuote(symbol);
        } catch (Exception e) {
            log.warn("akshare quote exception for {}: {}", symbol, e.getMessage());
            return emptyQuote(symbol);
        }
    }

    private StockQuoteResponse emptyQuote(String symbol) {
        return new StockQuoteResponse(
                new StockQuoteResponse.GlobalQuote(symbol, null, java.time.LocalDate.now().toString(), symbol),
                null
        );
    }

    // ==================== 基本面概况 ====================

    public StockOverviewResponse getStockOverview(String symbol) {
        try {
            return webClient.get()
                    .uri("/api/v1/overview/{symbol}", symbol)
                    .retrieve()
                    .bodyToMono(StockOverviewResponse.class)
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("akshare overview error for {}: HTTP {} {}", symbol, e.getStatusCode(), e.getResponseBodyAsString());
            return emptyOverview(symbol);
        } catch (Exception e) {
            log.warn("akshare overview exception for {}: {}", symbol, e.getMessage());
            return emptyOverview(symbol);
        }
    }

    private StockOverviewResponse emptyOverview(String symbol) {
        return new StockOverviewResponse(symbol, symbol, "", "N/A", "N/A", "N/A", "N/A", "N/A");
    }

    // ==================== 历史 K 线 ====================

    public StockHistoryResponse getStockHistory(String symbol) {
        return getStockHistory(symbol, "day");
    }

    /**
     * @param period day / week / month
     */
    public StockHistoryResponse getStockHistory(String symbol, String period) {
        try {
            final String p = (period == null || period.isBlank()) ? "day" : period;
            return webClient.get()
                    .uri(uriBuilder -> uriBuilder.path("/api/v1/history/{symbol}")
                            .queryParam("period", p)
                            .build(symbol))
                    .retrieve()
                    .bodyToMono(StockHistoryResponse.class)
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("akshare history error for {} (period={}): HTTP {} {}", symbol, period, e.getStatusCode(), e.getResponseBodyAsString());
            return emptyHistory(symbol);
        } catch (Exception e) {
            log.warn("akshare history exception for {} (period={}): {}", symbol, period, e.getMessage());
            return emptyHistory(symbol);
        }
    }

    /**
     * 分钟 K 线（仅 A 股），akshare 数据源
     * @param period 1 / 5 / 15 / 30 / 60
     */
    public List<DailyStockResponse> getStockMinuteHistory(String symbol, int period) {
        try {
            List<DailyStockResponse> data = webClient.get()
                    .uri(uriBuilder -> uriBuilder.path("/api/v1/minute/{symbol}")
                            .queryParam("period", period)
                            .build(symbol))
                    .retrieve()
                    .bodyToFlux(DailyStockResponse.class)
                    .collectList()
                    .block();
            return data == null ? List.of() : data;
        } catch (WebClientResponseException e) {
            log.warn("akshare minute error for {} (period={}): HTTP {} {}", symbol, period, e.getStatusCode(), e.getResponseBodyAsString());
            return List.of();
        } catch (Exception e) {
            log.warn("akshare minute exception for {} (period={}): {}", symbol, period, e.getMessage());
            return List.of();
        }
    }

    private StockHistoryResponse emptyHistory(String symbol) {
        return new StockHistoryResponse(
                new StockHistoryResponse.MetaData(symbol),
                java.util.Map.of()
        );
    }

    // ==================== 技术指标（RSI / MACD / 布林带）====================

    /**
     * 调 Python 服务 /api/v1/indicators/{symbol} 拿量化分析结果
     * 返回 Map 含: indicators.rsi, indicators.macd.{dif,dea,hist}, indicators.bollinger 等
     * 失败时返回 null，由调用方处理降级（指标类预警不触发即可）
     */
    @SuppressWarnings("unchecked")
    public Map<String, Object> getStockIndicators(String symbol) {
        try {
            return webClient.get()
                    .uri("/api/v1/indicators/{symbol}", symbol)
                    .retrieve()
                    .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("akshare indicators error for {}: HTTP {} {}", symbol, e.getStatusCode(), e.getResponseBodyAsString());
            return null;
        } catch (Exception e) {
            log.warn("akshare indicators exception for {}: {}", symbol, e.getMessage());
            return null;
        }
    }
}
