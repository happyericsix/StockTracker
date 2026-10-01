package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.*;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.exception.FavoriteAlreadyExistsException;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.stream.Collectors;

/**
 * 自选股与行情编排。
 *
 * <p>带缓存的行情/概况/指标读取在 {@link StockDataGateway} —— @Cacheable 必须经过
 * Spring 代理才生效，而本类内部的 this 直调会绕过代理（这正是本类曾经
 * "缓存全部失效、预警列表逐行打 Python"的根因）。这里的同名方法只是对外
 * API 的委托，内部路径一律走 gateway。
 */
@Service
public class StockService {

    private final StockDataGateway stockDataGateway;
    private final FavoriteStockRepository favoriteStockRepository;
    private final UserRepository userRepository;
    private final ExecutorService priceRefreshExecutor;
    private static final Logger log = LoggerFactory.getLogger(StockService.class);

    /** 单只股票拉取的超时上限：超时即丢弃该只，不拖累整批 */
    private static final long PER_STOCK_TIMEOUT_SECONDS = 10;

    @Autowired
    public StockService(StockDataGateway stockDataGateway,
                        FavoriteStockRepository favoriteStockRepository,
                        UserRepository userRepository,
                        @Qualifier("priceRefreshExecutor") ExecutorService priceRefreshExecutor) {
        this.stockDataGateway = stockDataGateway;
        this.favoriteStockRepository = favoriteStockRepository;
        this.userRepository = userRepository;
        this.priceRefreshExecutor = priceRefreshExecutor;
    }

    // ==================== 股票搜索（委托，缓存见 StockDataGateway）====================

    public StockSearchResponse searchStocks(String keyword) {
        return stockDataGateway.searchStocks(keyword);
    }

    // ==================== 实时行情（委托，缓存见 StockDataGateway）====================

    public StockResponse getStockForSymbol(final String stockSymbol) {
        return stockDataGateway.getStockForSymbol(stockSymbol);
    }

    public StockOverviewResponse getStockOverviewForSymbol(final String stockSymbol) {
        return stockDataGateway.getStockOverviewForSymbol(stockSymbol);
    }

    /**
     * 解析股票名称：走 gateway 的 stockOverviews 缓存，失败回退为股票代码本身。
     */
    public String resolveStockName(final String stockSymbol) {
        try {
            StockOverviewResponse overview = stockDataGateway.getStockOverviewForSymbol(stockSymbol);
            if (overview != null && overview.name() != null && !overview.name().isBlank()
                    && !overview.name().equals(stockSymbol)) {
                return overview.name();
            }
        } catch (Exception e) {
            log.debug("Failed to resolve stock name for {}: {}", stockSymbol, e.getMessage());
        }
        return stockSymbol;
    }

    // ==================== 技术指标（委托，缓存见 StockDataGateway）====================

    public IndicatorData getIndicators(final String stockSymbol) {
        return stockDataGateway.getIndicators(stockSymbol);
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

    public PagedResponse<DailyStockResponse> getHistoryPaged(String symbol, int page, int size, String period) {
        StockHistoryResponse response = stockDataGateway.getStockHistory(symbol, period);

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

    /**
     * 代码统一规范化：trim + 大写（A/B 股、港股、美股均按此存储与比对）。
     * 避免 "aapl" 与 "AAPL" 被当成两只不同股票、去重/删除失效。
     */
    private static String normalizeSymbol(String s) {
        if (s == null) return null;
        return s.trim().toUpperCase(java.util.Locale.ROOT);
    }

    @Transactional
    public FavoriteStock addFavorite(final String stockSymbol, final String username, final Double buyPrice, final Integer quantity) {
        final String symbol = normalizeSymbol(stockSymbol);
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        if (favoriteStockRepository.existsByStockSymbolAndUserId(symbol, user.getId())) {
            throw new FavoriteAlreadyExistsException(symbol);
        }

        FavoriteStock favoriteStock = FavoriteStock.builder()
                .stockSymbol(symbol)
                .user(user)
                .buyPrice(buyPrice)
                .quantity(quantity)
                .buyDate(CnTime.today().toString())
                .build();
        return favoriteStockRepository.save(favoriteStock);
    }

    @Transactional
    public boolean deleteFavorite(final String stockSymbol, final String username) {
        final String symbol = normalizeSymbol(stockSymbol);
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        if (favoriteStockRepository.existsByStockSymbolAndUserId(symbol, user.getId())) {
            favoriteStockRepository.deleteByStockSymbolAndUserId(symbol, user.getId());
            log.info("Deleted favorite: {} for user: {}", symbol, username);
            return true;
        }
        log.warn("Favorite not found: {} for user: {}", symbol, username);
        return false;
    }

    // 这里原先有一个无参重载 getFavoritesWithLivePrices()，用的是 findAll() —— 也就是
    // **返回所有用户的自选股**。它从来没有被调用过（Controller 用的是下面带 username 的版本），
    // 但它就紧挨着正确的方法，靠自动补全很容易选错，一旦选错就是跨用户数据泄漏。
    // 已删除。如果将来真的要"全站所有自选股的最新价"，那是 refreshFavorites() 的职责
    // （它按 symbol 去重并并行拉取），不要用这个形状。

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
            indicators = stockDataGateway.getIndicators(fav.getStockSymbol());
        } catch (Exception e) {
            log.warn("Indicators fetch failed for {}: {}", fav.getStockSymbol(), e.getMessage());
        }

        return new RefreshedPrice(fav.getStockSymbol(), price, indicators);
    }

    /** 解析价：失败 / 为 0 / 格式异常统一返回 null，让上游决定是否跳过 */
    private Double getCurrentPriceSafe(String symbol) {
        try {
            StockResponse stock = stockDataGateway.getStockForSymbol(symbol);
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
                .map(fav -> stockDataGateway.getStockForSymbol(fav.getStockSymbol()))
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
        return stockDataGateway.getStockMinuteHistory(symbol, period);
    }
}
