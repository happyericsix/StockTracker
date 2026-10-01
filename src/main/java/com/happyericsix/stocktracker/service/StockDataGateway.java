package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.dto.DailyStockResponse;
import com.happyericsix.stocktracker.dto.IndicatorData;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.dto.StockOverviewResponse;
import com.happyericsix.stocktracker.dto.StockQuoteResponse;
import com.happyericsix.stocktracker.dto.StockResponse;
import com.happyericsix.stocktracker.dto.StockSearchResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;

import java.util.Collections;
import java.util.Map;

/**
 * Python 数据服务的**带缓存网关**：行情 / 概况 / 指标 / 搜索的 @Cacheable 入口。
 *
 * <h3>为什么单独一个 Bean（而不是留在 StockService 里）</h3>
 * Spring 的缓存走代理：同类内部 {@code this.xxx()} 直调**绕过代理**，缓存等于不存在。
 * 原来 StockService.resolveStockName / buildRefreshedPrice / getFavoritesWithLivePrices
 * 都是这种自调用 —— "stocks/stockIndicators 缓存"对最热的几条路径完全不生效，
 * 预警列表按行数放大成逐条未缓存的 Python HTTP（单次超时 15s）。
 * 把 @Cacheable 方法挪进独立 Bean 后，任何调用方（含 StockService 自己）都过代理。
 *
 * <p>调用方应注入本类；{@link StockService} 保留同名委托方法以维持对外 API。
 */
@Service
public class StockDataGateway {

    private static final Logger log = LoggerFactory.getLogger(StockDataGateway.class);

    private final AkshareStockClient akshareStockClient;

    public StockDataGateway(AkshareStockClient akshareStockClient) {
        this.akshareStockClient = akshareStockClient;
    }

    // ==================== 股票搜索 ====================

    /**
     * 搜索股票代码/名称，永久缓存到 Redis。
     * 空关键词也缓存空结果，防止缓存穿透。
     */
    @Cacheable(value = "stockSearch", key = "#keyword.trim().toUpperCase()", unless = "#result == null")
    public StockSearchResponse searchStocks(String keyword) {
        if (keyword == null || keyword.trim().isEmpty()) {
            return new StockSearchResponse("", 0, Collections.emptyList());
        }
        return akshareStockClient.searchStocks(keyword.trim());
    }

    // ==================== 实时行情 ====================

    @Cacheable(value = "stocks", key = "#stockSymbol")
    public StockResponse getStockForSymbol(final String stockSymbol) {
        long startTime = System.currentTimeMillis();
        log.info("Fetching stock quote for symbol: {} (cache miss)", stockSymbol);

        StockQuoteResponse response = akshareStockClient.getStockQuote(stockSymbol);

        if (response == null || response.globalQuote() == null) {
            long duration = System.currentTimeMillis() - startTime;
            log.warn("No quote data for symbol {} (empty response). Duration: {}ms", stockSymbol, duration);
            return StockResponse.builder()
                    .symbol(stockSymbol)
                    .name(stockSymbol)
                    .price(null)
                    .lastUpdated(null)
                    .build();
        }

        long duration = System.currentTimeMillis() - startTime;
        log.info("Successfully fetched quote for {}. Duration: {}ms", stockSymbol, duration);
        String quoteName = response.globalQuote().name();
        return StockResponse.builder()
                .symbol(response.globalQuote().symbol())
                .name(quoteName == null || quoteName.isBlank() ? stockSymbol : quoteName)
                .price(response.globalQuote().price())
                .lastUpdated(response.globalQuote().lastTradingDay())
                // 透传涨跌三项：Python 侧已解析，这里必须带上，否则前端拿不到方向
                .previousClose(response.globalQuote().previousClose())
                .change(response.globalQuote().change())
                .changePercent(response.globalQuote().changePercent())
                .build();
    }

    @Cacheable(value = "stockOverviews", key = "#stockSymbol")
    public StockOverviewResponse getStockOverviewForSymbol(final String stockSymbol) {
        return akshareStockClient.getStockOverview(stockSymbol);
    }

    // ==================== 技术指标（RSI / MACD）====================

    /**
     * 拿技术指标：调 Python 服务 /api/v1/indicators/{symbol}，5 分钟缓存
     * 价格类预警不依赖此方法；RSI / MACD 类预警必须用
     * 失败/数据不足时返回 null，evaluator 自行跳过
     */
    @Cacheable(value = "stockIndicators", key = "#stockSymbol")
    public IndicatorData getIndicators(final String stockSymbol) {
        Map<String, Object> raw = akshareStockClient.getStockIndicators(stockSymbol);
        if (raw == null || raw.containsKey("error")) {
            log.debug("Indicators unavailable for {}: {}", stockSymbol, raw == null ? "null" : raw.get("error"));
            return null;
        }
        return parseIndicators(raw);
    }

    @SuppressWarnings("unchecked")
    private IndicatorData parseIndicators(Map<String, Object> raw) {
        Object indicatorsObj = raw.get("indicators");
        if (!(indicatorsObj instanceof Map)) {
            return null;
        }
        Map<String, Object> ind = (Map<String, Object>) indicatorsObj;
        IndicatorData data = new IndicatorData();
        data.setRsi(asDouble(ind.get("rsi")));

        // 只解析真正会读的两个字段。Python 侧同样只返回这两个（见 quant_model.analyze_stock），
        // ma5/ma20/macd.dif/dea/bollinger 以前在这里解析、存进缓存，然后没有任何读取点 ——
        // 已从两侧一起删除。新指标要两处同时加。
        Object macdObj = ind.get("macd");
        if (macdObj instanceof Map) {
            Map<String, Object> macd = (Map<String, Object>) macdObj;
            data.setMacdHist(asDouble(macd.get("hist")));
        }

        return data;
    }

    private Double asDouble(Object o) {
        if (o == null) return null;
        if (o instanceof Number n) return n.doubleValue();
        try {
            return Double.parseDouble(o.toString());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    // ==================== K 线历史（无缓存直通：分页/周期组合太多，缓存命中率不划算）====================

    public StockHistoryResponse getStockHistory(String symbol, String period) {
        return akshareStockClient.getStockHistory(symbol, period);
    }

    public java.util.List<DailyStockResponse> getStockMinuteHistory(String symbol, int period) {
        return akshareStockClient.getStockMinuteHistory(symbol, period);
    }
}
