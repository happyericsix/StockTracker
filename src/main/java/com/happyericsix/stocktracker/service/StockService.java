package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.dto.*;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.exception.FavoriteAlreadyExistsException;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.cache.annotation.Cacheable;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;

@Service
public class StockService {

    private final AkshareStockClient akshareStockClient;
    private final FavoriteStockRepository favoriteStockRepository;
    private final UserRepository userRepository;
    private final ExecutorService priceRefreshExecutor;
    private static final Logger log = LoggerFactory.getLogger(StockService.class);

    /** 单只股票拉取的超时上限：超时即丢弃该只，不拖累整批 */
    private static final long PER_STOCK_TIMEOUT_SECONDS = 10;

    @Autowired
    public StockService(AkshareStockClient akshareStockClient,
                        FavoriteStockRepository favoriteStockRepository,
                        UserRepository userRepository,
                        @Qualifier("priceRefreshExecutor") ExecutorService priceRefreshExecutor) {
        this.akshareStockClient = akshareStockClient;
        this.favoriteStockRepository = favoriteStockRepository;
        this.userRepository = userRepository;
        this.priceRefreshExecutor = priceRefreshExecutor;
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
                .build();
    }

    @Cacheable(value = "stockOverviews", key = "#stockSymbol")
    public StockOverviewResponse getStockOverviewForSymbol(final String stockSymbol) {
        return akshareStockClient.getStockOverview(stockSymbol);
    }

    public StockHistoryResponse getHistory(final String stockSymbol, int days) {
        return getHistory(stockSymbol, days, "day");
    }

    public StockHistoryResponse getHistory(final String stockSymbol, int days, String period) {
        return akshareStockClient.getStockHistory(stockSymbol, period);
    }

    /**
     * 解析股票名称：走 stockOverviews Redis 缓存，失败回退为股票代码本身。
     */
    public String resolveStockName(final String stockSymbol) {
        try {
            StockOverviewResponse overview = getStockOverviewForSymbol(stockSymbol);
            if (overview != null && overview.name() != null && !overview.name().isBlank()
                    && !overview.name().equals(stockSymbol)) {
                return overview.name();
            }
        } catch (Exception e) {
            log.debug("Failed to resolve stock name for {}: {}", stockSymbol, e.getMessage());
        }
        return stockSymbol;
    }

    // ==================== 技术指标（RSI / MACD / 布林带）====================

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
        data.setMa5(asDouble(ind.get("ma5")));
        data.setMa20(asDouble(ind.get("ma20")));

        Object macdObj = ind.get("macd");
        if (macdObj instanceof Map) {
            Map<String, Object> macd = (Map<String, Object>) macdObj;
            data.setMacdDif(asDouble(macd.get("dif")));
            data.setMacdDea(asDouble(macd.get("dea")));
            data.setMacdHist(asDouble(macd.get("hist")));
        }

        Object bollObj = ind.get("bollinger");
        if (bollObj instanceof Map) {
            Map<String, Object> boll = (Map<String, Object>) bollObj;
            data.setBollUpper(asDouble(boll.get("upper")));
            data.setBollMiddle(asDouble(boll.get("middle")));
            data.setBollLower(asDouble(boll.get("lower")));
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

    /**
     * Parse volume from Python service: it may send "20622.0" / "20622.000" (decimal string),
     * which Long.parseLong cannot handle. Parse as double first, then round to long.
     * Returns 0 for null / blank / unparseable values so one bad record does not fail the whole page.
     */
    private long parseVolumeLong(String volume) {
        if (volume == null || volume.isBlank()) return 0L;
        try {
            return Math.round(Double.parseDouble(volume.trim()));
        } catch (NumberFormatException e) {
            log.warn("parseVolumeLong: bad volume format '{}'", volume);
            return 0L;
        }
    }

    public PagedResponse<DailyStockResponse> getHistoryPaged(String symbol, int page, int size) {
        return getHistoryPaged(symbol, page, size, "day");
    }

    public PagedResponse<DailyStockResponse> getHistoryPaged(String symbol, int page, int size, String period) {
        StockHistoryResponse response = akshareStockClient.getStockHistory(symbol, period);

        List<DailyStockResponse> allData = response.timeSeries().entrySet().stream()
                .map(entry -> new DailyStockResponse(
                        entry.getKey(),
                        entry.getValue().open(),
                        entry.getValue().close(),
                        entry.getValue().high(),
                        entry.getValue().low(),
                        parseVolumeLong(entry.getValue().volume())
                ))
                .collect(Collectors.toList());

        int totalElements = allData.size();
        int totalPages = (int) Math.ceil((double) totalElements / size);
        int fromIndex = page * size;
        int toIndex = Math.min(fromIndex + size, totalElements);
        List<DailyStockResponse> pageContent = fromIndex >= totalElements
                ? List.of()
                : allData.subList(fromIndex, toIndex);

        return new PagedResponse<>(pageContent, page, size, totalElements, totalPages);
    }

    @Transactional
    public FavoriteStock addFavorite(final String stockSymbol, final String username, final Double buyPrice, final Integer quantity) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        if (favoriteStockRepository.existsByStockSymbolAndUserId(stockSymbol, user.getId())) {
            throw new FavoriteAlreadyExistsException(stockSymbol);
        }

        FavoriteStock favoriteStock = FavoriteStock.builder()
                .stockSymbol(stockSymbol)
                .user(user)
                .buyPrice(buyPrice)
                .quantity(quantity)
                .buyDate(java.time.LocalDate.now().toString())
                .build();
        return favoriteStockRepository.save(favoriteStock);
    }

    @Transactional
    public boolean deleteFavorite(final String stockSymbol, final String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        if (favoriteStockRepository.existsByStockSymbolAndUserId(stockSymbol, user.getId())) {
            favoriteStockRepository.deleteByStockSymbolAndUserId(stockSymbol, user.getId());
            log.info("Deleted favorite: {} for user: {}", stockSymbol, username);
            return true;
        }
        log.warn("Favorite not found: {} for user: {}", stockSymbol, username);
        return false;
    }

    public List<StockResponse> getFavoritesWithLivePrices() {
        List<FavoriteStock> allFavorites = favoriteStockRepository.findAll();
        return allFavorites.stream()
                .map(fav -> getStockForSymbol(fav.getStockSymbol()))
                .collect(Collectors.toList());
    }

    /**
     * 拉所有自选股的最新价 + 指标，打包成 RefreshedPrice 列表
     *  - 用于 StockPriceRefreshJob 定时刷新后发事件
     *  - 拉不到的股票直接跳过（price 为 null 的不算入结果）
     *  - 指标拉取失败不致命（price 类预警不需要）
     *
     * 按 symbol 去重：多个用户加同一只股票时只拉一次（下游 toMap 也不冲突）
     * buyPrice 不在这里——那是用户私有的，由 evaluator 从 alert.getUser() 拿
     *
     * 一次性把 alert 评估需要的所有数据准备好，监听器收到后直接用，
     * 不再各自走 @Cacheable，避免"刷新"和"评估"读到不同时点的价
     *
     * 并发模型：8 线程并行拉，单只超 10s 直接丢弃，不拖累整批
     *  - 30 只股票从 30+ 秒降到 ~3-5 秒
     *  - 避免一只卡住、所有股票排队的问题
     */
    public List<RefreshedPrice> refreshFavorites() {
        List<FavoriteStock> allFavorites = favoriteStockRepository.findAll();
        if (allFavorites.isEmpty()) return List.of();

        // 按 symbol 去重：N 个用户加同一只 → 只取第一条（buyPrice 反正不用了，保留也无所谓）
        Map<String, FavoriteStock> uniqueBySymbol = allFavorites.stream()
                .collect(Collectors.toMap(
                        FavoriteStock::getStockSymbol,
                        fav -> fav,
                        (a, b) -> a));   // 重复 symbol：保留第一条

        long t0 = System.currentTimeMillis();

        // 并行提交：8 个线程并发跑 buildRefreshedPrice
        List<CompletableFuture<RefreshedPrice>> futures = uniqueBySymbol.values().stream()
                .map(fav -> CompletableFuture.supplyAsync(() -> buildRefreshedPrice(fav), priceRefreshExecutor))
                .toList();

        // 收集结果：每只单独自带超时，超时丢弃 + cancel，线程不浪费
        List<RefreshedPrice> result = futures.stream()
                .map(future -> awaitWithTimeout(future, PER_STOCK_TIMEOUT_SECONDS))
                .filter(Objects::nonNull)
                .toList();

        long cost = System.currentTimeMillis() - t0;
        log.info("refreshFavorites: {}/{} unique symbols (from {} favorite rows) in {}ms (parallel, pool=8)",
                result.size(), uniqueBySymbol.size(), allFavorites.size(), cost);
        return result;
    }

    private RefreshedPrice awaitWithTimeout(CompletableFuture<RefreshedPrice> future, long timeoutSeconds) {
        try {
            return future.get(timeoutSeconds, TimeUnit.SECONDS);
        } catch (TimeoutException e) {
            future.cancel(true);   // 中断执行，释放线程
            log.warn("refreshFavorites: stock fetch timeout after {}s, dropped", timeoutSeconds);
            return null;
        } catch (Exception e) {
            // 异常已经在 buildRefreshedPrice 内部 try-catch，理论上走不到这里
            log.error("refreshFavorites: future failed: {}", e.getMessage());
            return null;
        }
    }

    private RefreshedPrice buildRefreshedPrice(FavoriteStock fav) {
        Double price = getCurrentPriceSafe(fav.getStockSymbol());
        if (price == null) {
            log.debug("Skip {}: no current price", fav.getStockSymbol());
            return null;
        }

        IndicatorData indicators = null;
        try {
            indicators = getIndicators(fav.getStockSymbol());
        } catch (Exception e) {
            log.warn("Indicators fetch failed for {}: {}", fav.getStockSymbol(), e.getMessage());
        }

        return new RefreshedPrice(fav.getStockSymbol(), price, indicators);
    }

    /** 解析价：失败 / 为 0 / 格式异常统一返回 null，让上游决定是否跳过 */
    private Double getCurrentPriceSafe(String symbol) {
        try {
            StockResponse stock = getStockForSymbol(symbol);
            if (stock == null || stock.price() == null) return null;
            String p = stock.price().trim();
            if (p.isEmpty() || "0.0".equals(p) || "0".equals(p)) return null;
            return Double.parseDouble(p);
        } catch (NumberFormatException e) {
            log.warn("getCurrentPrice: bad price format for {}: {}", symbol, e.getMessage());
            return null;
        } catch (Exception e) {
            log.warn("getCurrentPrice failed for {}: {}", symbol, e.getMessage());
            return null;
        }
    }

    public List<StockResponse> getFavoritesWithLivePrices(final String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        List<FavoriteStock> favoriteStocks = favoriteStockRepository.findByUserId(user.getId());
        return favoriteStocks.stream()
                .map(fav -> getStockForSymbol(fav.getStockSymbol()))
                .collect(Collectors.toList());
    }

    // ==================== 分钟 K 线（仅 A 股）====================

    /**
     * 获取 A 股分钟 K 线（akshare 数据源）。
     * @param symbol 股票代码
     * @param period 1 / 5 / 15 / 30 / 60
     */
    public List<DailyStockResponse> getMinuteKline(String symbol, int period) {
        if (period != 1 && period != 5 && period != 15 && period != 30 && period != 60) {
            log.warn("Invalid minute period {}, fallback to 5", period);
            period = 5;
        }
        return akshareStockClient.getStockMinuteHistory(symbol, period);
    }
}
