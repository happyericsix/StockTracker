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

/**
 * 自选股（用户态数据）—— 原先是 {@link StockController} 里的 {@code /api/v1/stocks/favorites}。
 *
 * <p><b>为什么挪出来</b>：{@code /api/v1/stocks/**} 表达的是"市场数据"，同一路径下还挂着
 * {@code /{stockSymbol}} 这个通配路径段 —— 也就是说 {@code /stocks/任意字符串} 都被当成股票代码，
 * {@code /favorites}、{@code /search} 只是靠 Spring 的字面量优先规则胜出才没被吃掉。
 * 而自选股是**按用户隔离的持仓数据**（每个方法都要 {@code Authentication}），
 * 与"查某只股票的行情"根本不是一类资源。混在一起有两个实际后果：
 * <ol>
 *   <li>再加一个 {@code /stocks/xxx} 形式的字面子资源，就要继续和通配段抢优先级，语义越来越糊；</li>
 *   <li>{@code SecurityConfig} 里 {@code /api/v1/stocks/**} 的鉴权意图无法区分
 *       "公开可读的市场数据"与"必须登录的用户数据"。</li>
 * </ol>
 * 现在它落在已有的用户态命名空间下（{@code UserController} 用的是 {@code /api/v1/user/profile}），
 * 靠 {@code anyRequest().authenticated()} 兜住鉴权，不新增任何放行路径。
 *
 * <p><b>契约逐字未变</b>：请求/响应体、状态码、响应形状（GET 仍是裸数组、POST/DELETE 仍是
 * {@code Result} 信封）、以及删除自选时**连带删除该股票预警**的行为，全部与移动前一致。
 * 本次只改路由前缀。
 *
 * <p>调用方（改路由时要一起看）：前端 {@code frontend/src/api/stock.js} 三处、
 * {@code _verify_shots/contract-smoke.ps1}、README / WORKFLOW / CACHING_GUIDE。
 * Python 数据服务**没有** favorites 路由（它只有 {@code /api/v1/stocks/search}），
 * 且 nginx 只把 {@code /api/} 代理到 Java（{@code app:8080}），所以移动不牵动 Python 侧。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/user/favorites")
public class FavoriteController {
    private final StockService stockService;
    private final AlertService alertService;

    private static final Logger log = LoggerFactory.getLogger(FavoriteController.class);

    // ==================== 自选股 ====================

    @PostMapping
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

    @GetMapping
    public List<StockResponse> getFavoriteStocks(Authentication authentication) {
        long startTime = System.currentTimeMillis();
        List<StockResponse> favorites = stockService.getFavoritesWithLivePrices(authentication.getName());
        long duration = System.currentTimeMillis() - startTime;
        log.info("GET /user/favorites returned {} stocks in {}ms", favorites.size(), duration);
        return favorites;
    }

    @DeleteMapping("/{symbol}")
    public Result<String> deleteFavoriteStocks(
            @PathVariable String symbol,
            Authentication authentication) {
        final String normalized = symbol.trim().toUpperCase();
        boolean deleted = stockService.deleteFavorite(normalized, authentication.getName());
        if (deleted) {
            // ⚠️ 级联：删除自选会连带删除该股票的全部预警（用户已确认保留此行为）。
            //    前端确认文案必须把这个后果说出来，否则用户不知道预警也没了。
            alertService.deleteByUserAndSymbol(authentication.getName(), normalized);
            return Result.success("已删除自选", normalized);
        } else {
            // 中文产品不该给用户看英文；这条消息现在会经由 request.js 的拦截器
            // 以 reject 的形式出现在界面上，所以必须是可读的中文。
            return Result.error(404, "自选股中不存在该股票：" + normalized);
        }
    }
}
