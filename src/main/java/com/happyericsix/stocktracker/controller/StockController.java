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
            @RequestParam(defaultValue = "30") int size) {
        return stockService.getHistoryPaged(stockSymbol.toUpperCase(), page, size);
    }

    @PostMapping("/favorites")
    public ResponseEntity<FavoriteStock> saveFavoriteStock(
            @RequestBody FavoriteStockRequest request,
            Authentication authentication) {

        final FavoriteStock saved = stockService.addFavorite(request.getSymbol(),
                authentication.getName(),
                request.getBuyPrice(),
                request.getQuantity());

        return ResponseEntity.ok().body(saved);

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
        boolean deleted = stockService.deleteFavorite(symbol.toUpperCase(), authentication.getName());
        if (deleted) {
            alertService.deleteByUserAndSymbol(authentication.getName(), symbol);
            return Result.success("Favorite deleted successfully", symbol.toUpperCase());
        } else {
            return Result.error(404, "Favorite not found: " + symbol.toUpperCase());
        }
    }
}
