package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperEquityResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.ExpectationService;
import com.happyericsix.stocktracker.service.PaperEquitySeries;
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
    private final ExpectationService expectationService;
    private final UserRepository userRepository;

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

    /**
     * 手动跑一次样本外验证（多标的 × 多时段 + 成本归因）。
     *
     * <p>与周频自动验证**同一个入口**：结论一致，且都会覆盖客观事实里的那一组键 ——
     * 两条路径各写一份数字，迟早会出现"报告引用的结论和手动跑的对不上"。
     */
    @PostMapping("/{id}/verify")
    public Result<JsonNode> verifyStrategy(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(strategyService.verifyMatrix(authentication.getName(), id));
    }

    /**
     * 切换决策来源：{@code rule}（策略 DSL）或 {@code agent}（多角色委员会）。
     *
     * <p>这是一次**会改变历史解释依据**的操作（曲线从生效日起分成两段），
     * 所以它是一条显式路径，并且记录生效日 —— 而不是让调用方直接改字段。
     */
    @PostMapping("/{id}/decision-mode")
    public Result<StrategyResponse> switchDecisionMode(
            @PathVariable Long id,
            @RequestParam String mode,
            Authentication authentication) {
        return Result.success(
                strategyService.switchDecisionMode(authentication.getName(), id, mode));
    }

    /**
     * 登记一条**可验证预期**：到 {@code horizonDays} 天后，指定度量是否 ≥ 门槛。
     *
     * <p>度量是封闭集（超额 / 收益 / 回撤），全部是**账户层面可观测值** ——
     * 不涉及股价预测（本项目第一条原则）。到期后由每日结算自动回填，
     * 复盘报告会写"达成 / 未达成 / 算不出来"。
     *
     * <p>刻意做成显式动作：一个承诺得有人下。Agent 的"继续观察"也应该走到这里来，
     * 否则"不表态"永远是安全策略。
     */
    @PostMapping("/{id}/expectation")
    public Result<String> registerExpectation(
            @PathVariable Long id,
            @RequestParam String metric,
            @RequestParam java.math.BigDecimal threshold,
            @RequestParam(required = false) Integer horizonDays,
            Authentication authentication) {
        User user = userRepository.findByUsername(authentication.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        // 归属检查走既有路径：拿不到这条策略就说明不属于该用户（与账户/痕迹同一条规矩）
        strategyService.listStrategies(authentication.getName()).stream()
                .filter(item -> id.equals(item.getId()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        ExpectationService.Expectation expectation =
                expectationService.register(user.getId(), id, metric, threshold, horizonDays);
        if (expectation == null) {
            return Result.error(400, "预期未登记：度量必须是 " + PaperEquitySeries.METRICS
                    + "，门槛必须是数字");
        }
        return Result.success(ExpectationService.describe(expectation));
    }

    /**
     * 净值曲线：点 + 由同一份点算出的汇总（回撤、空仓比例、相对买入持有的超额）。
     *
     * <p>汇总里的 {@code excessVsBuyAndHoldPct} 就是"不动"的代价 —— 空仓那些天，
     * 账面上是 0，相对基准却是负的。
     */
    @GetMapping("/{id}/paper/equity")
    public Result<PaperEquityResponse> getPaperEquity(
            @PathVariable Long id,
            @RequestParam(defaultValue = "120") int days,
            Authentication authentication) {
        return Result.success(paperTradingService.getEquityCurve(authentication.getName(), id, days));
    }
}
