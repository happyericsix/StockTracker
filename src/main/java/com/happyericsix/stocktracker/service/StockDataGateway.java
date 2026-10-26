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
import org.springframework.cache.annotation.CachePut;
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
        return fetchStockForSymbol(stockSymbol);
    }

    /**
     * 焦点快车道（2026-10-15）：绕过读缓存强制取新价，并把新值写回两级缓存 ——
     * 图表页正在看的票每 3 秒被 {@code FocusQuoteStreamJob} 刷一次，
     * 顺带让 15 秒自选批和其他读者立刻拿到更近的价。
     * 只给"个别票、高频"的路径用；批量路径仍走 {@link #getStockForSymbol} 的缓存。
     */
    @CachePut(value = "stocks", key = "#stockSymbol")
    public StockResponse refreshStockForSymbol(final String stockSymbol) {
        return fetchStockForSymbol(stockSymbol);
    }

    private StockResponse fetchStockForSymbol(final String stockSymbol) {
        long startTime = System.currentTimeMillis();
        log.info("Fetching stock quote for symbol: {}", stockSymbol);

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

    // ==================== K 线历史（短 TTL 缓存 + 同 key 并发合并）====================
    //
    // 曾经"无缓存直通"的理由是"分页/周期组合太多，命中率不划算"，但实际调用
    // 全部收敛在 (symbol, period) 上（页面固定拉 1000 根）。K 线页一次切换要打
    // 这里最多 2 次（行情+历史），高频切换时直通会瞬间放大成对 Python 服务
    // （akshare 外部源，单次秒级）的连环 miss —— 那是"频繁点击变卡"的主链路。
    //
    // TTL 对齐 Python 侧缓存（日 K 近期 60s / 分钟 K 30s），Java 这层只做合并
    // 与挡高频重复，不引入比下游更陈旧的数据。失败返回 null，null 不缓存。
    //
    // sync=true：同 key 并发 miss 只放一个线程去加载（走 TwoLevelCache 的
    // 合并 get），其余线程等同一次结果 —— 没有它，缓存击穿的瞬间每个并发
    // 请求都会各自打一次 Python。

    @Cacheable(value = "stockHistory", key = "#symbol + ':' + #period", sync = true)
    public StockHistoryResponse getStockHistory(String symbol, String period) {
        return akshareStockClient.getStockHistory(symbol, period);
    }

    @Cacheable(value = "stockMinute", key = "#symbol + ':' + #period", sync = true)
    public java.util.List<DailyStockResponse> getStockMinuteHistory(String symbol, int period) {
        return akshareStockClient.getStockMinuteHistory(symbol, period);
    }
}
