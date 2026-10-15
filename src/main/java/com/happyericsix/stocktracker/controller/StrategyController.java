package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.ExpectationResponse;
import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperEquityResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.TradeJournalResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.dto.StrategyRevisionResponse;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.ExpectationService;
import com.happyericsix.stocktracker.service.PaperEquitySeries;
import com.happyericsix.stocktracker.service.PaperTradingService;
import com.happyericsix.stocktracker.service.StrategyRevisionService;
import com.happyericsix.stocktracker.service.StrategyService;
import com.happyericsix.stocktracker.service.TradeJournalService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/strategies")
public class StrategyController {

    private final StrategyService strategyService;
    private final PaperTradingService paperTradingService;
    private final ExpectationService expectationService;
    private final TradeJournalService tradeJournalService;
    private final StrategyRevisionService strategyRevisionService;
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

    /** 单条策略（编辑面板打开时取最新 config，不依赖列表页恰好带着它）。 */
    @GetMapping("/{id}")
    public Result<StrategyResponse> getStrategy(@PathVariable Long id,
                                                Authentication authentication) {
        return Result.success(strategyService.get(authentication.getName(), id));
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

    /**
     * 开启只读分享，返回 token（前端拼成 /share/{token}）。
     * 重新调用会换 token —— 旧链接随即失效，这就是"撤回"的实现。
     */
    @PostMapping("/{id}/share")
    public Result<java.util.Map<String, String>> enableShare(
            @PathVariable Long id,
            Authentication authentication) {
        String token = strategyService.enableShare(authentication.getName(), id);
        return Result.success(java.util.Map.of("token", token));
    }

    /** 关闭分享：token 置空，链接立刻失效。 */
    @DeleteMapping("/{id}/share")
    public Result<String> disableShare(
            @PathVariable Long id,
            Authentication authentication) {
        strategyService.disableShare(authentication.getName(), id);
        return Result.success("已停止分享");
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

    /**
     * 决策日志（trade journal）：当时说了什么 × 后来实际发生了什么。
     *
     * <p>模型的命中率由记录说话，不由自述说话——这是"前向验证通过才可信"
     * 的产品化出口。口径见 {@link TradeJournalResponse} 的诚实边界
     * （方向对错 ≠ 盈亏；算不出就不判）。
     */
    @GetMapping("/{id}/journal")
    public Result<TradeJournalResponse> getJournal(
            @PathVariable Long id,
            @RequestParam(required = false, defaultValue = "90") int days,
            Authentication authentication) {
        return Result.success(tradeJournalService.journal(authentication.getName(), id, days));
    }

    @PostMapping("/{id}/paper/start")
    public Result<PaperAccountResponse> startPaper(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(paperTradingService.startPaper(authentication.getName(), id));
    }

    // ==================== 策略修订闭环（P2，2026-10-15）====================

    /** 健康信号（确定性，无 LLM）：提案的触发理由，也供页面常驻展示。 */
    @GetMapping("/{id}/revision/health")
    public Result<List<Map<String, Object>>> revisionHealth(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(strategyRevisionService.healthSignalsRaw(
                authentication.getName(), id));
    }

    /** 生成修订提案（1 次 LLM + 截至当天的时间衰减加权回测门禁）。 */
    @PostMapping("/{id}/revisions/generate")
    public Result<StrategyRevisionResponse> generateRevision(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(StrategyRevisionResponse.from(
                strategyRevisionService.generate(authentication.getName(), id)));
    }

    @GetMapping("/{id}/revisions")
    public Result<List<StrategyRevisionResponse>> listRevisions(
            @PathVariable Long id,
            Authentication authentication) {
        return Result.success(strategyRevisionService.list(authentication.getName(), id)
                .stream().map(StrategyRevisionResponse::from).toList());
    }

    /** 批准：走既有 updateStrategy 路径（schema 校验 + configUpdatedAt 口径打点）。 */
    @PostMapping("/{id}/revisions/{revisionId}/approve")
    public Result<StrategyRevisionResponse> approveRevision(
            @PathVariable Long id,
            @PathVariable Long revisionId,
            @RequestBody(required = false) Map<String, String> body,
            Authentication authentication) {
        String note = body == null ? null : body.get("note");
        return Result.success(StrategyRevisionResponse.from(
                strategyRevisionService.approve(authentication.getName(), id, revisionId, note)));
    }

    @PostMapping("/{id}/revisions/{revisionId}/reject")
    public Result<StrategyRevisionResponse> rejectRevision(
            @PathVariable Long id,
            @PathVariable Long revisionId,
            @RequestBody(required = false) Map<String, String> body,
            Authentication authentication) {
        String note = body == null ? null : body.get("note");
        return Result.success(StrategyRevisionResponse.from(
                strategyRevisionService.reject(authentication.getName(), id, revisionId, note)));
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
    public Result<ExpectationResponse> registerExpectation(
            @PathVariable Long id,
            @RequestParam String metric,
            @RequestParam java.math.BigDecimal threshold,
            @RequestParam(required = false) Integer horizonDays,
            Authentication authentication) {
        Long userId = currentUserId(authentication, id);
        ExpectationService.Expectation expectation =
                expectationService.register(userId, id, metric, threshold, horizonDays);
        if (expectation == null) {
            return Result.error(400, "预期未登记：度量必须是 " + PaperEquitySeries.METRICS
                    + "，门槛必须是数字");
        }
        return Result.success(ExpectationResponse.from(expectation));
    }

    /**
     * 读回当前有效的预期（含到期后的实际值与达成状态）。
     *
     * <p>没有登记过时返回 {@code null} 数据而不是报错：**"还没有人下过承诺"是一个正常状态**，
     * 界面要能把它和"有承诺但还没到期"分开说。
     */
    @GetMapping("/{id}/expectation")
    public Result<ExpectationResponse> getExpectation(
            @PathVariable Long id,
            Authentication authentication) {
        Long userId = currentUserId(authentication, id);
        return Result.success(ExpectationResponse.from(expectationService.latest(userId, id)));
    }

    /** 归属检查走既有路径：拿不到这条策略就说明不属于该用户（与账户/痕迹同一条规矩）。 */
    private Long currentUserId(Authentication authentication, Long strategyId) {
        User user = userRepository.findByUsername(authentication.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        strategyService.listStrategies(authentication.getName()).stream()
                .filter(item -> strategyId.equals(item.getId()))
                .findFirst()
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        return user.getId();
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
