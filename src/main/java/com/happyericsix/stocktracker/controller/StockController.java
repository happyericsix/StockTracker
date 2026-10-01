package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.*;
import com.happyericsix.stocktracker.service.StockService;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * 市场数据（公开可读的那一半）。
 *
 * <p>自选股（用户态数据）已迁到 {@link FavoriteController}（{@code /api/v1/user/favorites}），
 * 不再挂在本路径下 —— 原因见那个类的注释：{@code /{stockSymbol}} 是通配路径段，
 * 把用户态资源塞进市场数据命名空间会让优先级与鉴权意图都变糊。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/stocks")
public class StockController {
    private final StockService stockService;

    private static final Logger log = LoggerFactory.getLogger(StockController.class);
    // ==================== 股票搜索 (Autocomplete) ====================
    @GetMapping("/search")
    public Result<StockSearchResponse> searchStocks(@RequestParam(defaultValue = "") String keyword) {
        long startTime = System.currentTimeMillis();
        StockSearchResponse response = stockService.searchStocks(keyword);
        long duration = System.currentTimeMillis() - startTime;
        log.info("GET /search?keyword={} returned {} results in {}ms", keyword, response.count(), duration);
        return Result.success(response);
    }
    // ==================== 实时行情 ====================
    @GetMapping("/{stockSymbol}")
    public StockResponse getStock(@PathVariable("stockSymbol") String stockSymbol) {
        long startTime = System.currentTimeMillis();
        StockResponse response = stockService.getStockForSymbol(stockSymbol.toUpperCase());
        long duration = System.currentTimeMillis() - startTime;
        log.info("GET /{} returned in {}ms (price: {})", stockSymbol.toUpperCase(), duration, response.price());
        return response;
    }

    @GetMapping("/{stockSymbol}/overview")
    public StockOverviewResponse getStockOverview(@PathVariable String stockSymbol) {
        long startTime = System.currentTimeMillis();
        StockOverviewResponse response = stockService.getStockOverviewForSymbol(stockSymbol.toUpperCase());
        long duration = System.currentTimeMillis() - startTime;
        log.info("GET /{}/overview returned in {}ms", stockSymbol.toUpperCase(), duration);
        return response;
    }

    @GetMapping("/{stockSymbol}/history")
    public PagedResponse<DailyStockResponse> getStockHistory(
            @PathVariable String stockSymbol,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "30") int size,
            @RequestParam(defaultValue = "day") String period) {
        return stockService.getHistoryPaged(stockSymbol.toUpperCase(), page, size, period);
    }

    /**
     * 分钟 K 线（仅 A 股）
     * @param period 1 / 5 / 15 / 30 / 60
     */
    @GetMapping("/{stockSymbol}/minute")
    public List<DailyStockResponse> getStockMinuteKline(
            @PathVariable String stockSymbol,
            @RequestParam(defaultValue = "5") int period) {
        return stockService.getMinuteKline(stockSymbol.toUpperCase(), period);
    }
}
