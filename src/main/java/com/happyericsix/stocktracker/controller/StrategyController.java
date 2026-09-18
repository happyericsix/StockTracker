package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.service.PaperTradingService;
import com.happyericsix.stocktracker.service.StrategyService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/strategies")
public class StrategyController {

    private final StrategyService strategyService;
    private final PaperTradingService paperTradingService;

    @GetMapping
    public Result<List<StrategyResponse>> listStrategies(Authentication authentication) {
        return Result.success(strategyService.listStrategies(authentication.getName()));
    }

    @PostMapping
    public Result<StrategyResponse> createStrategy(
            @Valid @RequestBody StrategyRequest request,
            Authentication authentication) {
        return Result.success(strategyService.createStrategy(authentication.getName(), request));
    }

    @PutMapping("/{id}")
    public Result<StrategyResponse> updateStrategy(
            @PathVariable Long id,
            @Valid @RequestBody StrategyRequest request,
            Authentication authentication) {
        return Result.success(strategyService.updateStrategy(authentication.getName(), id, request));
    }

    @DeleteMapping("/{id}")
    public Result<String> deleteStrategy(
            @PathVariable Long id,
            Authentication authentication) {
        strategyService.deleteStrategy(authentication.getName(), id);
        return Result.success("删除成功");
    }

    @PostMapping("/{id}/backtest")
    public Result<JsonNode> runBacktest(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(strategyService.runBacktest(authentication.getName(), id));
    }

    @GetMapping("/{id}/diagnostic")
    public Result<ModelDiagnosticResponse> getModelDiagnostic(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(strategyService.getModelDiagnostic(authentication.getName(), id));
    }

    @PostMapping("/{id}/paper/start")
    public Result<PaperAccountResponse> startPaper(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(paperTradingService.startPaper(authentication.getName(), id));
    }

    @PostMapping("/{id}/paper/stop")
    public Result<String> stopPaper(
            @PathVariable Long id,
            Authentication authentication) {
        paperTradingService.stopPaper(authentication.getName(), id);
        return Result.success("已停止模拟盘");
    }

    @GetMapping("/{id}/paper/account")
    public Result<PaperAccountResponse> getPaperAccount(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(paperTradingService.getAccount(authentication.getName(), id));
    }

    @GetMapping("/{id}/paper/trades")
    public Result<List<PaperTradeResponse>> getPaperTrades(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(paperTradingService.getTrades(authentication.getName(), id));
    }

    /**
     * 最近的结算痕迹：每一行 = 一根 bar 上的一个结论（含"为什么没成交"）。
     *
     * <p>这是"随机抽 5 次阻塞跳过，不看代码能明白为什么"的入口。
     */
    @GetMapping("/{id}/paper/traces")
    public Result<List<PaperTradeTraceResponse>> getPaperTraces(
            @PathVariable Long id,
            @RequestParam(defaultValue = "50") int limit,
            Authentication authentication) {
        return Result.success(paperTradingService.getTraces(authentication.getName(), id, limit));
    }

    /** 某一天的痕迹（按时间正序）：排查"那天到底发生了什么"用。 */
    @GetMapping("/{id}/paper/traces/{date}")
    public Result<List<PaperTradeTraceResponse>> getPaperTracesForDay(
            @PathVariable Long id,
            @PathVariable @DateTimeFormat(iso = DateTimeFormat.ISO.DATE) LocalDate date,
            Authentication authentication) {
        return Result.success(
                paperTradingService.getTracesForDay(authentication.getName(), id, date));
    }
}
