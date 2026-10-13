package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.ExpectationResponse;
import com.happyericsix.stocktracker.dto.PaperEquityResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.dto.TradeJournalResponse;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.ExpectationService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import com.happyericsix.stocktracker.service.TradeJournalService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 策略模拟盘数据的<b>内部导出接口</b>：给 Python 侧 agent 的复盘工具
 * （{@code review_strategy} / {@code list_user_strategies}）取实际表现数据，不面向前端。
 *
 * <h3>为什么走接口而不是让 Python 直连 MySQL</h3>
 * 与 {@link InternalMemoryController}、{@link InternalNewsController} 同一条纪律：
 * 数据库凭据只存在于 Java 进程，Python 能取到什么被接口形状限制死。
 * 这里导出的是<b>只读快照</b>：策略配置、账户、净值、成交、结算痕迹、决策日志、预期。
 *
 * <h3>与前端读接口的关系</h3>
 * 数据口径完全复用 {@link PaperTradingService} / {@link TradeJournalService} /
 * {@link ExpectationService}（同一套归属检查与汇总算法），只是形状为 LLM 上下文
 * 做了裁剪：成交/痕迹取最近若干条、净值只出汇总与端点。复盘结论由 agent 综合，
 * 数字仍然全部来自这一份确定性数据 —— 与周报"报告是代码拼装的"同一条纪律。
 *
 * <h3>用户隔离</h3>
 * {@code userId} 参数是<b>数字 userId</b>（与 {@link InternalMemoryController} 同一约定：
 * Python 侧 agent chat 的 {@code memory_user_id} 契约）。tool_scope 注入给工具的
 * 身份就是这个数字 id。归属检查走 {@code findByIdAndUserId}：查别人的策略返回 404，
 * 与"不存在"不可区分。
 *
 * <h3>鉴权</h3>
 * 共享密钥放 {@code X-Internal-Token} 头，常量时间比较；未配置密钥一律拒绝（fail closed）。
 * 这里能导出用户的策略与模拟账户数据，敏感度高于资讯导出，同级保护。
 */
@RestController
@RequestMapping("/api/v1/internal/strategies")
public class InternalStrategyController {

    private static final Logger log = LoggerFactory.getLogger(InternalStrategyController.class);

    /** 复盘导出里成交/痕迹/日志条目的上限：够 agent 看出模式，又不撑爆上下文。 */
    private static final int RECENT_TRADES = 20;
    private static final int RECENT_TRACES = 20;
    private static final int RECENT_JOURNAL_ENTRIES = 10;

    private final UserRepository userRepository;
    private final StrategyRepository strategyRepository;
    private final PaperTradingService paperTradingService;
    private final TradeJournalService tradeJournalService;
    private final ExpectationService expectationService;
    private final JobRunService jobRunService;
    private final String internalToken;

    public InternalStrategyController(UserRepository userRepository,
                                      StrategyRepository strategyRepository,
                                      PaperTradingService paperTradingService,
                                      TradeJournalService tradeJournalService,
                                      ExpectationService expectationService,
                                      JobRunService jobRunService,
                                      @Value("${internal.api-token:}") String internalToken) {
        this.userRepository = userRepository;
        this.strategyRepository = strategyRepository;
        this.paperTradingService = paperTradingService;
        this.tradeJournalService = tradeJournalService;
        this.expectationService = expectationService;
        this.jobRunService = jobRunService;
        this.internalToken = internalToken;
    }

    /** 用户的策略清单（id/名称/标的/状态）——agent 复盘前先用它把"茅台策略"解析成 id。 */
    @GetMapping("/list")
    public ResponseEntity<Map<String, Object>> list(
            @RequestParam Long userId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
        List<Map<String, Object>> items = new ArrayList<>();
        for (Strategy s : strategyRepository.findByUserIdOrderByUpdatedAtDesc(user.getId())) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", s.getId());
            row.put("name", s.getName());
            row.put("symbol", s.getSymbol());
            row.put("paperEnabled", Boolean.TRUE.equals(s.getPaperEnabled()));
            row.put("decisionMode", s.getDecisionMode());
            row.put("updatedAt", s.getUpdatedAt() == null ? null : s.getUpdatedAt().toString());
            items.add(row);
        }
        return ResponseEntity.ok(Map.of("strategies", items, "count", items.size()));
    }

    /**
     * 单条策略的复盘数据包：配置 + 账户 + 净值汇总 + 最近成交 + 痕迹（含 skip 原因分布）
     * + 决策日志 + 当前预期。各节独立 try/catch：一节取不到就置 null 并记原因，
     * 不让单节故障废掉整份复盘。
     */
    @GetMapping("/{id}/review")
    public ResponseEntity<Map<String, Object>> review(
            @PathVariable Long id,
            @RequestParam Long userId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId()).orElse(null);
        if (strategy == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
        String username = user.getUsername();

        Map<String, Object> strategyNode = new LinkedHashMap<>();
        strategyNode.put("id", strategy.getId());
        strategyNode.put("name", strategy.getName());
        strategyNode.put("symbol", strategy.getSymbol());
        strategyNode.put("configJson", strategy.getConfigJson());
        strategyNode.put("paperEnabled", Boolean.TRUE.equals(strategy.getPaperEnabled()));
        strategyNode.put("decisionMode", strategy.getDecisionMode());
        strategyNode.put("decisionModeSince",
                strategy.getDecisionModeSince() == null ? null : strategy.getDecisionModeSince().toString());
        strategyNode.put("createdAt", strategy.getCreatedAt() == null ? null : strategy.getCreatedAt().toString());
        strategyNode.put("lastBacktestAt",
                strategy.getLastBacktestAt() == null ? null : strategy.getLastBacktestAt().toString());

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("strategy", strategyNode);
        body.put("account", section("account", () ->
                paperTradingService.getAccount(username, id) == null ? null
                        : accountNode(username, id)));
        body.put("equity", section("equity", () -> equityNode(username, id)));
        body.put("trades", section("trades", () -> tradesNode(username, id)));
        body.put("traces", section("traces", () -> tracesNode(username, id)));
        body.put("journal", section("journal", () -> journalNode(username, id)));
        body.put("expectation", section("expectation", () -> {
            ExpectationService.Expectation e = expectationService.latest(user.getId(), id);
            return e == null ? null : ExpectationResponse.from(e);
        }));

        log.info("internal strategy review-export: user={}, strategyId={}, name={}",
                username, id, strategy.getName());
        return ResponseEntity.ok(body);
    }

    // ==================== 各节数据的紧凑形状 ====================

    private Map<String, Object> accountNode(String username, Long id) {
        var a = paperTradingService.getAccount(username, id);
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("initialCapital", a.getInitialCapital());
        node.put("cash", a.getCash());
        node.put("shares", a.getShares());
        node.put("avgCost", a.getAvgCost());
        node.put("equity", a.getEquity());
        node.put("highWatermark", a.getHighWatermark());
        node.put("lastPrice", a.getLastPrice());
        node.put("lastSignal", a.getLastSignal());
        node.put("lastEvalAt", a.getLastEvalAt() == null ? null : a.getLastEvalAt().toString());
        return node;
    }

    private Map<String, Object> equityNode(String username, Long id) {
        PaperEquityResponse curve = paperTradingService.getEquityCurve(username, id, 250);
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("summary", curve.getSummary());
        List<Map<String, Object>> points = new ArrayList<>();
        // 只给首尾各一点：汇总里已有收益/回撤/天数，明细点对复盘结论没有增量
        List<PaperEquityResponse.Point> all = curve.getPoints();
        if (!all.isEmpty()) {
            points.add(pointNode(all.get(0)));
            if (all.size() > 1) {
                points.add(pointNode(all.get(all.size() - 1)));
            }
        }
        node.put("pointCount", all.size());
        node.put("endpoints", points);
        return node;
    }

    private Map<String, Object> pointNode(PaperEquityResponse.Point p) {
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("tradeDate", p.tradeDate() == null ? null : p.tradeDate().toString());
        node.put("equity", p.equity());
        return node;
    }

    private List<Map<String, Object>> tradesNode(String username, Long id) {
        List<Map<String, Object>> rows = new ArrayList<>();
        for (PaperTradeResponse t : paperTradingService.getTrades(username, id)) {
            if (rows.size() >= RECENT_TRADES) {
                break;
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tradeDate", t.getTradeDate() == null ? null : t.getTradeDate().toString());
            row.put("side", t.getSide());
            row.put("price", t.getPrice());
            row.put("shares", t.getShares());
            row.put("amount", t.getAmount());
            row.put("reason", t.getReason());
            rows.add(row);
        }
        return rows;
    }

    /**
     * 痕迹节是复盘的核心：skip_reason 的分布回答"为什么没成交"。
     * 直方图由代码数出来（不是模型数），最近痕迹给足上下文（触发器、信号、bar 收盘价）。
     */
    private Map<String, Object> tracesNode(String username, Long id) {
        List<PaperTradeTraceResponse> traces =
                paperTradingService.getTraces(username, id, RECENT_TRACES * 3);
        Map<String, Integer> histogram = new LinkedHashMap<>();
        for (PaperTradeTraceResponse t : traces) {
            String reason = t.getSkipReason();
            if (reason != null && !reason.isBlank()) {
                histogram.merge(reason, 1, Integer::sum);
            }
        }
        List<Map<String, Object>> recent = new ArrayList<>();
        for (PaperTradeTraceResponse t : traces) {
            if (recent.size() >= RECENT_TRACES) {
                break;
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tradeDate", t.getTradeDate() == null ? null : t.getTradeDate().toString());
            row.put("settlementKind", t.getSettlementKind());
            row.put("trigger", t.getTrigger());
            row.put("decision", t.getDecision());
            row.put("skipReason", t.getSkipReason());
            row.put("signal", t.getSignal());
            row.put("barClose", t.getBarClose());
            recent.add(row);
        }
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("recentCount", traces.size());
        node.put("skipReasonHistogram", histogram);
        node.put("recent", recent);
        return node;
    }

    private Map<String, Object> journalNode(String username, Long id) {
        TradeJournalResponse j = tradeJournalService.journal(username, id, 90);
        Map<String, Object> node = new LinkedHashMap<>();
        node.put("evaluated", j.evaluated());
        node.put("hits", j.hits());
        node.put("misses", j.misses());
        node.put("pending", j.pending());
        node.put("skips", j.skips());
        node.put("hitRate", j.hitRate());
        List<Map<String, Object>> entries = new ArrayList<>();
        for (TradeJournalResponse.Entry e : j.entries()) {
            if (entries.size() >= RECENT_JOURNAL_ENTRIES) {
                break;
            }
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("tradeDate", e.tradeDate() == null ? null : e.tradeDate().toString());
            row.put("decision", e.decision());
            row.put("signal", e.signal());
            row.put("skipReason", e.skipReason());
            row.put("nextChangePct", e.nextChangePct());
            row.put("verdict", e.verdict());
            entries.add(row);
        }
        node.put("recentEntries", entries);
        return node;
    }

    // ==================== 鉴权与工具 ====================

    private interface SectionSupplier {
        Object get() throws Exception;
    }

    /** 单节失败只置 null + 记日志：复盘是增强功能，半份数据也比"接口 500"强。 */
    private Object section(String name, SectionSupplier supplier) {
        try {
            return supplier.get();
        } catch (Exception e) {
            log.warn("review-export 节 {} 取数失败：{}", name, e.getMessage());
            return null;
        }
    }

    private boolean authorized(String token) {
        if (internalToken == null || internalToken.isBlank()) {
            log.error("internal.api-token 未配置，拒绝内部策略复盘导出接口访问");
            return false;
        }
        if (token == null) {
            return false;
        }
        return MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                internalToken.getBytes(StandardCharsets.UTF_8));
    }

    /**
     * 手动触发"当天结算失败重试"（运维入口）：部署修复后当晚补跑一次，
     * 让委员会的首会/当天的净值格不必等到下一个交易日。幂等 ——
     * 只碰当天 no_bar / data_unavailable 的策略，成功过的不会重跑。
     * 终轮语义（finalAttemptOfDay=true）：手动补跑是运维下的定论，允许发日报。
     */
    @PostMapping("/paper/retry-settlement")
    public ResponseEntity<Map<String, Object>> retrySettlement(
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        String summary = paperTradingService.retryFailedDailySettlements(true);
        jobRunService.record(JobRunService.JOB_PAPER_RETRY, JobRunService.STATUS_OK,
                "手动补跑：" + summary);
        return ResponseEntity.ok(Map.of("ok", true, "summary", summary));
    }

    private <T> ResponseEntity<T> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
    }
}
