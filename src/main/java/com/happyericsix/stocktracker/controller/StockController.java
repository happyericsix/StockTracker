package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.*;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.service.AlertService;
import com.happyericsix.stocktracker.service.StockService;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/stocks")
public class StockController {
    private final StockService stockService;
    private final AlertService alertService;

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

    @PostMapping("/favorites")
    public ResponseEntity<Result<FavoriteStockResponse>> saveFavoriteStock(
            @RequestBody FavoriteStockRequest request,
            Authentication authentication) {

        final FavoriteStock saved = stockService.addFavorite(request.getSymbol(),
                authentication.getName(),
                request.getBuyPrice(),
                request.getQuantity());

        FavoriteStockResponse dto = FavoriteStockResponse.from(
                saved.getStockSymbol(), saved.getBuyPrice(), saved.getQuantity(), saved.getBuyDate());
        return ResponseEntity.ok().body(Result.success("已添加自选", dto));
    }

    @GetMapping("/favorites")
    public List<StockResponse> getFavoriteStocks(Authentication authentication) {
        long startTime = System.currentTimeMillis();
        List<StockResponse> favorites = stockService.getFavoritesWithLivePrices(authentication.getName());
        long duration = System.currentTimeMillis() - startTime;
        log.info("GET /favorites returned {} stocks in {}ms", favorites.size(), duration);
        return favorites;
    }

    @DeleteMapping("/favorites/{symbol}")
    public Result<String> deleteFavoriteStocks(
            @PathVariable String symbol,
            Authentication authentication) {
        final String normalized = symbol.trim().toUpperCase();
        boolean deleted = stockService.deleteFavorite(normalized, authentication.getName());
        if (deleted) {
            alertService.deleteByUserAndSymbol(authentication.getName(), normalized);
            return Result.success("已删除自选", normalized);
        } else {
            // 中文产品不该给用户看英文；这条消息现在会经由 request.js 的拦截器
            // 以 reject 的形式出现在界面上，所以必须是可读的中文。
            return Result.error(404, "自选股中不存在该股票：" + normalized);
        }
    }
}
