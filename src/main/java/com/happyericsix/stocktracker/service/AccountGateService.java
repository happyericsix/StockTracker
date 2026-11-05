package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperAuthorization;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperAuthorizationRepository;
import com.happyericsix.stocktracker.repository.PaperPositionLotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 账户守门员（ACCOUNT-GATE 阶段1/2，2026-11-04）：agent 模式盘中的**账户级裁决者**。
 *
 * <h3>职责边界（与 {@link PaperTradingService} 的分工）</h3>
 * 本类是"脑子"：账户面板组装、唤醒源检测（电平触发）、冷却/预算、1 次 LLM 调用、
 * 保守解析。**不执行任何交易**——verdicts 由结算侧（PaperTradingService）走既有
 * 守卫栈（授权边界/组合熔断/往返守卫/T+1/整手）与执行管线落账留痕。
 *
 * <h3>取代了什么</h3>
 * 取代 per-strategy 盯盘交易员（intraday_watch）成为 agent 模式盘中主决策路径：
 * 一个 agent、多种唤醒源（信号事件 + 持仓风险阈值）、同一份**全账户**面板——
 * "agent 只看单策略切片、事后诸葛亮"的结构缺陷由此修复（docs/ACCOUNT-GATE.md §4.3）。
 *
 * <h3>为什么面板组装在 Java 而不在 Python</h3>
 * 持仓/授权/批次全在库里，Java 读库零成本；Python 端点保持薄（只补 market_context
 * 与 LLM 调用），与 portfolioContextQuietly 的"Java 组装、fail-open"同一约定。
 */
@Slf4j
@Service
public class AccountGateService {

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperAuthorizationRepository paperAuthorizationRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperPositionLotRepository lotRepository;
    private final PortfolioService portfolioService;
    private final StrategyClient strategyClient;
    /**
     * 守门员裁决事件（Tier 1b，2026-11-05）：面板喂回**自己的历史裁决**——
     * "上次说等反弹到 6.9 再看，现在 6.9 了"这类自我一致性信息。可空（单测）：
     * null 时面板少这一块（fail-open）。
     */
    private final com.happyericsix.stocktracker.repository.PaperGateEventRepository gateEventRepository;
    private final ObjectMapper mapper = new ObjectMapper();

    // ===== 配置（与 docs/ACCOUNT-GATE.md §B7 对齐；默认影子模式起步 =====
    /** 总开关：false = agent 模式盘中回到盯盘（intraday_watch）的现行行为。 */
    @Value("${paper.account-gate.enabled:true}")
    private boolean enabled;
    /** 影子模式：守门员照常裁决与留痕，但 verdicts 不执行（上线观察期用）。 */
    @Value("${paper.account-gate.shadow:true}")
    private boolean shadow;
    /** 盘中开仓权限：默认只减不加——开仓决定权留在 15:30 委员会（慢层）。 */
    @Value("${paper.account-gate.allow-open:false}")
    private boolean allowOpen;
    /** 每用户每日守门员 LLM 预算（成本止损线）。 */
    @Value("${paper.account-gate.daily-budget:10}")
    private int dailyBudget;
    /** 同一唤醒签名的冷却窗口（毫秒）。 */
    @Value("${paper.account-gate.cooldown-ms:900000}")
    private long cooldownMs;
    /** 近止损唤醒阈值（百分点）：任一持仓距止损线 ≤ 此值即唤醒。 */
    @Value("${paper.account-gate.near-stop-pct:1.0}")
    private BigDecimal nearStopPct;
    /** 同标的跨策略叠加敞口唤醒阈值（%）。 */
    @Value("${paper.account-gate.concentration-pct:30}")
    private BigDecimal concentrationPct;
    /** Tier 2 挑战者总开关（2026-11-05）：高注时刻的第二双眼睛。 */
    @Value("${paper.account-gate.challenger-enabled:true}")
    private boolean challengerEnabled;
    /** Tier 2 触发①：涉事仓位占账户净值比例 ≥ 此值（%）。 */
    @Value("${paper.account-gate.challenge-position-pct:30}")
    private BigDecimal challengePositionPct;
    /** Tier 2 触发②：距止损/移动止损线 ≤ 此值（百分点）。 */
    @Value("${paper.account-gate.challenge-near-stop-pct:0.5}")
    private BigDecimal challengeNearStopPct;

    // ===== 度量闭环（2026-11-05）：守门员的"值不值"要靠数据回答，不靠感觉 =====
    private final java.util.concurrent.atomic.AtomicLong gateCalls = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong gateDegraded = new java.util.concurrent.atomic.AtomicLong();
    /** 疑似截断：verdicts 为空且 warnings 非空（解析失败是截断的最常见表征）。 */
    private final java.util.concurrent.atomic.AtomicLong gateTruncationSuspect = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong gateTokens = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong challengeCalls = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong challengeDowngrades = new java.util.concurrent.atomic.AtomicLong();
    private final java.util.concurrent.atomic.AtomicLong challengeTokens = new java.util.concurrent.atomic.AtomicLong();

    // 冷却与预算：userId:signature → 上次唤醒时刻；userId:date → 已用预算。
    // 重启丢失无害（最多多问几次模型）——与盯盘的 watchEventLastFired 同一条纪律。
    private final Map<String, Long> wakeLastFired = new ConcurrentHashMap<>();
    private final Map<String, Integer> gateCallCount = new ConcurrentHashMap<>();

    /** 与 PaperTradingService 的授权 TTL 常量同值同义（那边是 private，不复用即复制并注明）。 */
    private static final int AUTH_TTL_DAYS_HOLDING = 3;
    private static final int AUTH_TTL_DAYS_FLAT = 5;

    public AccountGateService(StrategyRepository strategyRepository,
                              PaperAccountRepository paperAccountRepository,
                              PaperAuthorizationRepository paperAuthorizationRepository,
                              PaperTradeRepository paperTradeRepository,
                              PaperPositionLotRepository lotRepository,
                              PortfolioService portfolioService,
                              StrategyClient strategyClient,
                              com.happyericsix.stocktracker.repository.PaperGateEventRepository gateEventRepository) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperAuthorizationRepository = paperAuthorizationRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.lotRepository = lotRepository;
        this.portfolioService = portfolioService;
        this.strategyClient = strategyClient;
        this.gateEventRepository = gateEventRepository;
    }

    public boolean enabled() {
        return enabled;
    }

    public boolean shadow() {
        return shadow;
    }

    public boolean allowOpen() {
        return allowOpen;
    }

    /**
     * Tier 2 触发判定（纯代码，规则来自花费梯度设计 2026-11-05）：
     * 涉事仓位 ≥ 账户净值 30% / 任一持仓距硬线 ≤0.5pt / 账户日回撤 ≥ 熔断线一半。
     * 任一命中即值得为这次裁决多花一次挑战调用（+3~5k tokens，一个月几次）。
     */
    public java.util.List<String> challengeTriggers(ObjectNode panel) {
        var triggers = new java.util.ArrayList<String>();
        JsonNode positions = panel.path("account").path("positions");
        for (JsonNode pos : positions) {
            try {
                BigDecimal share = new BigDecimal(pos.path("position_share_pct").asText(
                        pos.path("positionSharePct").asText("0")));
                if (share.compareTo(challengePositionPct) >= 0) {
                    triggers.add(String.format("position_share:%s=%.1f%%",
                            pos.path("symbol").asText(""), share));
                }
            } catch (NumberFormatException ignored) {
                // 面板脏值跳过
            }
            JsonNode stops = pos.path("stop_lines");
            for (String key : List.of("distance_to_stop_pct", "distance_to_trailing_pct")) {
                String distance = stops.path(key).asText(null);
                if (distance != null) {
                    try {
                        if (new BigDecimal(distance).compareTo(challengeNearStopPct) <= 0) {
                            triggers.add(String.format("near_hard_line:%s=%.2fpt",
                                    pos.path("symbol").asText(""), new BigDecimal(distance)));
                        }
                    } catch (NumberFormatException ignored) {
                        // 面板脏值跳过
                    }
                }
            }
        }
        try {
            BigDecimal drawdown = new BigDecimal(
                    panel.path("account").path("day_drawdown_pct").asText("0"));
            BigDecimal halt = portfolioService != null
                    ? portfolioService.haltThresholdPct() : BigDecimal.valueOf(5.0);
            if (drawdown.compareTo(halft(halt).negate()) <= 0) {
                triggers.add(String.format("drawdown_half:%.2f%%", drawdown));
            }
        } catch (Exception e) {
            log.debug("drawdown challenge trigger check skipped: {}", e.toString());
        }
        return triggers;
    }

    /**
     * 挑战调用（Tier 2）：失败 = 维持原裁决（度量计数，不影响决策安全）。
     * 返回的 merged 已按"只能更保守"合并；downgrades 计数进度量。
     */
    public JsonNode challengeQuietly(Long userId, ObjectNode payload, JsonNode gateResponse) {
        JsonNode verdicts = gateResponse.path("verdicts");
        if (!verdicts.isArray() || verdicts.isEmpty()) {
            return null;
        }
        var firstVerdicts = new java.util.ArrayList<java.util.Map<String, Object>>();
        for (JsonNode v : verdicts) {
            var row = new java.util.HashMap<String, Object>();
            v.properties().forEach(p -> row.put(p.getKey(),
                    p.getValue().isNumber() ? p.getValue().asDouble() : p.getValue().asText()));
            firstVerdicts.add(row);
        }
        try {
            JsonNode result = strategyClient.accountGateChallenge(payload, firstVerdicts, userId);
            challengeCalls.incrementAndGet();
            if (result == null || result.path("degraded").asBoolean(false)) {
                return null;   // 挑战失败 = 维持原裁决
            }
            challengeTokens.addAndGet(result.path("challenge_tokens").asLong(0));
            if (result.path("overturns").isArray() && !result.path("overturns").isEmpty()) {
                challengeDowngrades.addAndGet(result.path("overturns").size());
            }
            return result;
        } catch (Exception e) {
            log.warn("gate challenge failed for user {}（维持原裁决）: {}", userId, e.getMessage());
            return null;
        }
    }

    /** 守门员度量快照（/health 与周报用）：花没花钱、钱花得干不干净，都要能查。 */
    public java.util.Map<String, Object> stats() {
        var out = new java.util.LinkedHashMap<String, Object>();
        out.put("gate_calls", gateCalls.get());
        out.put("gate_degraded", gateDegraded.get());
        out.put("gate_truncation_suspect", gateTruncationSuspect.get());
        out.put("gate_tokens_total", gateTokens.get());
        out.put("challenge_calls", challengeCalls.get());
        out.put("challenge_downgrades", challengeDowngrades.get());
        out.put("challenge_tokens_total", challengeTokens.get());
        out.put("challenger_enabled", challengerEnabled);
        return out;
    }

    boolean challengerEnabled() {
        return challengerEnabled;
    }

    // ==================== 账户面板（裁决的唯一事实基础） ====================

    /**
     * 组装该用户全部在跑策略的账户面板：持仓明细（含距止损线距离/T+1 锁定/授权边界）、
     * 现金与总敞口、同标的叠加度、组合日回撤。任何一处组装失败都只少一块字段
     * （fail-open）——面板缺失是"少证据"，不该变成"不裁决"。
     */
    public ObjectNode buildAccountPanel(Long userId) {
        ObjectNode panel = mapper.createObjectNode();
        panel.put("note", "账户守门员面板：你管的是整个账户——先仓库后市面再信号。");
        List<Strategy> running = strategyRepository
                .findByUserIdOrderByUpdatedAtDesc(userId).stream()
                .filter(s -> Boolean.TRUE.equals(s.getPaperEnabled()))
                .toList();
        ObjectNode account = panel.putObject("account");
        ArrayNode positions = account.putArray("positions");

        BigDecimal totalCash = BigDecimal.ZERO;
        BigDecimal totalEquity = BigDecimal.ZERO;
        BigDecimal totalPositionValue = BigDecimal.ZERO;
        Map<String, BigDecimal> symbolExposure = new HashMap<>();
        LocalDate today = LocalDate.now(java.time.ZoneId.of("Asia/Shanghai"));

        for (Strategy strategy : running) {
            PaperAccount a = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
            if (a == null) {
                continue;
            }
            totalCash = totalCash.add(nz(a.getCash()));
            if (a.getEquity() != null) {
                totalEquity = totalEquity.add(a.getEquity());
            }
            boolean holding = a.getShares() != null && a.getShares().signum() > 0;
            BigDecimal positionValue = holding && a.getLastPrice() != null
                    ? a.getShares().multiply(a.getLastPrice()) : BigDecimal.ZERO;
            totalPositionValue = totalPositionValue.add(positionValue);
            if (holding) {
                symbolExposure.merge(strategy.getSymbol(), positionValue, BigDecimal::add);
            }

            ObjectNode row = positions.addObject();
            row.put("strategy_id", strategy.getId());
            row.put("name", strategy.getName());
            row.put("symbol", strategy.getSymbol());
            row.put("decision_mode", strategy.getDecisionMode() == null
                    ? "" : strategy.getDecisionMode());
            if (!holding) {
                row.put("shares", 0);
                row.put("cash", a.getCash() == null ? null : a.getCash().toPlainString());
                continue;
            }
            row.put("shares", a.getShares().stripTrailingZeros().toPlainString());
            row.put("avg_cost", a.getAvgCost() == null ? null : a.getAvgCost().toPlainString());
            row.put("last_price", a.getLastPrice() == null ? null : a.getLastPrice().toPlainString());
            row.put("high_watermark", a.getHighWatermark() == null
                    ? null : a.getHighWatermark().toPlainString());
            if (a.getLastPrice() != null && a.getAvgCost() != null
                    && a.getAvgCost().signum() > 0) {
                row.put("unrealized_pnl_pct", a.getLastPrice()
                        .divide(a.getAvgCost(), 6, RoundingMode.HALF_UP)
                        .subtract(BigDecimal.ONE).multiply(BigDecimal.valueOf(100))
                        .setScale(2, RoundingMode.HALF_UP).toPlainString());
            }
            // 持仓天数：最早未平批次的开仓日（无批次账本时退回账户创建日）
            long ageDays = lotRepository
                    .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                            strategy.getId(), BigDecimal.ZERO).stream()
                    .map(l -> l.getOpenedDate()).filter(d -> d != null).min(LocalDate::compareTo)
                    .map(d -> ChronoUnit.DAYS.between(d, today))
                    .orElseGet(() -> a.getCreatedAt() == null ? 0
                            : ChronoUnit.DAYS.between(a.getCreatedAt().toLocalDate(), today));
            row.put("entry_age_days", ageDays);
            // T+1 锁定：今天开的批次今日不可卖
            BigDecimal locked = lotRepository
                    .findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                            strategy.getId(), BigDecimal.ZERO).stream()
                    .filter(l -> today.equals(l.getOpenedDate()))
                    .map(l -> nz(l.getRemainingShares())).reduce(BigDecimal.ZERO, BigDecimal::add);
            row.put("t1_locked_shares", locked.stripTrailingZeros().toPlainString());
            attachStopLines(row, strategy, a);
            attachAuthorization(row, strategy, holding);
        }

        account.put("strategies_running", running.size());
        account.put("cash", totalCash.toPlainString());
        account.put("total_equity", totalEquity.toPlainString());
        account.put("total_position_value", totalPositionValue.toPlainString());
        if (totalEquity.signum() > 0) {
            account.put("position_pct", totalPositionValue
                    .divide(totalEquity, 4, RoundingMode.HALF_UP)
                    .multiply(BigDecimal.valueOf(100)).setScale(1, RoundingMode.HALF_UP)
                    .toPlainString());
            // 每仓位占账户净值的比例（Tier 2 挑战者的触发输入之一）：
            // 总额算完才能回填——第一遍循环时 totalEquity 还没定
            for (JsonNode row : positions) {
                if (row instanceof ObjectNode posNode && posNode.path("shares").asDouble(0) > 0
                        && posNode.hasNonNull("last_price") && posNode.hasNonNull("shares")) {
                    try {
                        BigDecimal value = new BigDecimal(posNode.path("shares").asText())
                                .multiply(new BigDecimal(posNode.path("last_price").asText()));
                        posNode.put("position_share_pct", value
                                .divide(totalEquity, 4, RoundingMode.HALF_UP)
                                .multiply(BigDecimal.valueOf(100)).setScale(1, RoundingMode.HALF_UP)
                                .toPlainString());
                    } catch (NumberFormatException e) {
                        log.debug("position share calc skipped（脏值）: {}", e.toString());
                    }
                }
            }
        }
        ObjectNode exposure = account.putObject("same_symbol_exposure_pct");
        if (totalEquity.signum() > 0) {
            final BigDecimal equityBase = totalEquity;
            symbolExposure.forEach((symbol, value) -> exposure.put(symbol, value
                    .divide(equityBase, 4, RoundingMode.HALF_UP)
                    .multiply(BigDecimal.valueOf(100)).setScale(1, RoundingMode.HALF_UP)
                    .toPlainString()));
        }
        // 组合日回撤（现值 vs 最近收盘基准）：drawdown_warning 的判定口径同款
        try {
            if (portfolioService != null) {
                account.put("day_drawdown_pct", BigDecimal
                        .valueOf(portfolioService.dayDrawdownPct(userId)).toPlainString());
            }
        } catch (Exception e) {
            log.debug("day drawdown unavailable for user {}（面板少一块，不挡裁决）: {}",
                    userId, e.toString());
        }
        attachRecentGateDecisions(account, userId);
        return panel;
    }

    /**
     * 喂回守门员自己的历史裁决（Tier 1b，2026-11-05）：最近 5 条（14 天窗口）。
     * 自我一致性是性价比最高的加价——"上次说等反弹再看，现在条件到了/没到"
     * 这类对照没有它就只能靠模型记忆（而每次唤醒都是无状态的）。
     * fail-open：仓库缺席/查询失败只是面板少一块。
     */
    private void attachRecentGateDecisions(ObjectNode account, Long userId) {
        if (gateEventRepository == null) {
            return;
        }
        try {
            var events = gateEventRepository.findByUserIdAndCreatedAtAfterOrderByCreatedAtDesc(
                    userId, java.time.LocalDateTime.now(java.time.ZoneId.of("Asia/Shanghai"))
                            .minusDays(14));
            if (events.isEmpty()) {
                return;
            }
            ArrayNode recent = account.putArray("recent_gate_decisions");
            recent.addObject().put("note",
                    "你（账户守门员）近期的裁决。新裁决必须与它们自洽：条件变了要说明"
                            + "变在哪；兑现了上次留的 next_review 要点名兑现。");
            events.stream().limit(5).forEach(e -> recent.addObject()
                    .put("date", e.getCreatedAt() == null ? "" : e.getCreatedAt().toString())
                    .put("signature", e.getSignature())
                    .put("action", e.getAction())
                    .put("reason", e.getReason() == null ? "" : e.getReason())
                    .put("executed", Boolean.TRUE.equals(e.getExecuted())));
        } catch (Exception e) {
            log.debug("recent gate decisions unavailable for user {}（面板少一块）: {}",
                    userId, e.toString());
        }
    }

    /** 挂风控线与当前距离：stop_loss_pct（相对成本）/ trailing_stop_pct（距高水位）。 */
    private void attachStopLines(ObjectNode row, Strategy strategy, PaperAccount account) {
        try {
            JsonNode config = new ObjectMapper().readTree(strategy.getConfigJson());
            JsonNode exit = config.path("exit").path("conditions");
            if (!exit.isArray()) {
                return;
            }
            ObjectNode stops = row.putObject("stop_lines");
            for (JsonNode cond : exit) {
                String type = cond.path("type").asText("");
                BigDecimal value = isNumeric(cond.path("value").asText(""))
                        ? new BigDecimal(cond.path("value").asText("")) : null;
                if ("stop_loss_pct".equals(type) && value != null) {
                    stops.put("stop_loss_pct", value.toPlainString());
                    if (account.getAvgCost() != null && account.getAvgCost().signum() > 0
                            && account.getLastPrice() != null) {
                        BigDecimal pnlPct = account.getLastPrice()
                                .divide(account.getAvgCost(), 6, RoundingMode.HALF_UP)
                                .subtract(BigDecimal.ONE).multiply(BigDecimal.valueOf(100));
                        stops.put("distance_to_stop_pct",
                                pnlPct.subtract(value).setScale(2, RoundingMode.HALF_UP)
                                        .toPlainString());
                    }
                } else if ("trailing_stop_pct".equals(type) && value != null) {
                    stops.put("trailing_stop_pct", value.toPlainString());
                    if (account.getHighWatermark() != null
                            && account.getHighWatermark().signum() > 0
                            && account.getLastPrice() != null) {
                        BigDecimal drawdown = account.getHighWatermark().subtract(account.getLastPrice())
                                .divide(account.getHighWatermark(), 6, RoundingMode.HALF_UP)
                                .multiply(BigDecimal.valueOf(100));
                        stops.put("distance_to_trailing_pct",
                                value.subtract(drawdown).setScale(2, RoundingMode.HALF_UP)
                                        .toPlainString());
                    }
                }
            }
        } catch (Exception e) {
            log.debug("stop lines parse failed for strategy {}（面板少一块）: {}",
                    strategy.getId(), e.toString());
        }
    }

    private static boolean isNumeric(String text) {
        if (text == null || text.isBlank()) {
            return false;
        }
        try {
            new BigDecimal(text);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }

    /** 挂授权边界与剩余天数（含 stale 判定，口径同 PaperTradingService.authorizationStale）。 */
    private void attachAuthorization(ObjectNode row, Strategy strategy, boolean holding) {
        try {
            PaperAuthorization auth = paperAuthorizationRepository
                    .findFirstByStrategyIdOrderByIssuedAtDesc(strategy.getId()).orElse(null);
            if (auth == null) {
                row.putNull("authorization");
                return;
            }
            ObjectNode node = row.putObject("authorization");
            node.put("allow_open", Boolean.TRUE.equals(auth.getAllowOpen()));
            node.put("allow_close", Boolean.TRUE.equals(auth.getAllowClose()));
            node.put("max_position_pct", auth.getMaxPositionPct() == null
                    ? null : auth.getMaxPositionPct().toPlainString());
            if (auth.getIssuedAt() != null) {
                int ttl = holding ? AUTH_TTL_DAYS_HOLDING : AUTH_TTL_DAYS_FLAT;
                LocalDate issued = auth.getIssuedAt().toLocalDate();
                node.put("days_since_issued", ChronoUnit.DAYS.between(issued, LocalDate.now()));
                node.put("stale", issued.isBefore(LocalDate.now().minusDays(ttl - 1L)));
            }
            if (auth.getNotes() != null && !auth.getNotes().isBlank()) {
                node.put("notes", auth.getNotes());
            }
        } catch (Exception e) {
            log.debug("authorization attach failed for strategy {}（面板少一块）: {}",
                    strategy.getId(), e.toString());
        }
    }

    // ==================== 唤醒源检测（电平触发，纯代码零 LLM） ====================

    /**
     * 扫描持仓风险阈值（阶段2的周期扫描入口调用）。返回唤醒源列表；
     * 空列表 = 没有值得叫醒守门员的事（零 LLM 成本）。
     *
     * <p>gap_sharp / plan_review 两源在阶段3补（前者要昨收数据源，后者要回看计划表），
     * 当前四源已覆盖"仓位挨着止损线/组合回撤过半/同标的叠加/授权过期"四类账户级风险。
     */
    public List<Map<String, Object>> detectRiskWakes(ObjectNode panel) {
        var wakes = new java.util.ArrayList<Map<String, Object>>();
        JsonNode positions = panel.path("account").path("positions");
        for (JsonNode pos : positions) {
            long strategyId = pos.path("strategy_id").asLong();
            JsonNode stops = pos.path("stop_lines");
            for (String key : List.of("distance_to_stop_pct", "distance_to_trailing_pct")) {
                String distance = stops.path(key).asText(null);
                if (distance != null) {
                    try {
                        if (new BigDecimal(distance).compareTo(nearStopPct) <= 0) {
                            wakes.add(Map.of("source", "near_stop",
                                    "strategy_id", strategyId,
                                    "detail", String.format("距%s %.2f 个百分点",
                                            key.contains("trailing") ? "移动止损线" : "止损线",
                                            new BigDecimal(distance))));
                            break;   // 一个仓位近线只报一次
                        }
                    } catch (NumberFormatException ignored) {
                        // 面板脏值：跳过这一条，不挡其他唤醒
                    }
                }
            }
            if (pos.path("authorization").path("stale").asBoolean(false)) {
                wakes.add(Map.of("source", "auth_expired", "strategy_id", strategyId,
                        "detail", "持仓策略的盘中授权已过 TTL（等 15:30 委员会重划）"));
            }
        }
        // 组合回撤过半（熔断阈值的一半 = 提前商量线）。回撤是负数口径
        // （与 haltsNewOpens 的 drawdown <= -halt 同一约定）：-2.6 <= -2.5 即过半。
        try {
            BigDecimal drawdown = new BigDecimal(
                    panel.path("account").path("day_drawdown_pct").asText("0"));
            BigDecimal haltThreshold = BigDecimal.valueOf(5.0);   // 与 PortfolioService 默认口径
            if (portfolioService != null) {
                haltThreshold = portfolioService.haltThresholdPct();
            }
            if (drawdown.compareTo(halft(haltThreshold).negate()) <= 0) {
                wakes.add(Map.of("source", "drawdown_warning",
                        "detail", String.format("组合日回撤 %.2f%%（熔断线 %.1f%% 的一半）",
                                drawdown, haltThreshold)));
            }
        } catch (Exception e) {
            log.debug("drawdown wake check skipped: {}", e.toString());
        }
        // 同标的叠加敞口
        JsonNode exposure = panel.path("account").path("same_symbol_exposure_pct");
        var exposureFields = exposure.properties();
        for (var entry : exposureFields) {
            try {
                if (new BigDecimal(entry.getValue().asText("0")).compareTo(concentrationPct) >= 0) {
                    wakes.add(Map.of("source", "concentration",
                            "detail", String.format("同标的 %s 跨策略合计敞口 %s%%",
                                    entry.getKey(), entry.getValue().asText())));
                }
            } catch (NumberFormatException ignored) {
                // 脏值跳过
            }
        }
        return wakes;
    }

    private static BigDecimal halft(BigDecimal v) {
        return v.divide(BigDecimal.valueOf(2), 2, RoundingMode.HALF_UP);
    }

    // ==================== 冷却/预算与裁决调用 ====================

    /** 裁决结果：status ∈ ok / cooldown / budget_exceeded / unavailable。 */
    public record GateCall(String status, JsonNode response, String signature) {
        public boolean ok() {
            return "ok".equals(status);
        }
    }

    /**
     * 过冷却/预算后调一次守门员。签名相同的唤醒在冷却窗内只问一次；
     * 预算按"用户×日"计（重启归零无害——预算是止损线不是账单）。
     */
    public GateCall decide(Long userId, ObjectNode payload, ArrayNode wake, String signature) {
        if (!enabled) {
            return new GateCall("unavailable", null, signature);
        }
        long now = System.currentTimeMillis();
        String coolKey = userId + ":" + signature;
        Long last = wakeLastFired.get(coolKey);
        if (last != null && now - last < cooldownMs) {
            return new GateCall("cooldown", null, signature);
        }
        String budgetKey = userId + ":" + LocalDate.now(java.time.ZoneId.of("Asia/Shanghai"));
        if (gateCallCount.getOrDefault(budgetKey, 0) >= dailyBudget) {
            return new GateCall("budget_exceeded", null, signature);
        }
        payload.set("wake", wake);
        try {
            JsonNode response = strategyClient.accountGate(payload, userId);
            if (response == null || response.path("degraded").asBoolean(false)) {
                gateDegraded.incrementAndGet();
                return new GateCall("unavailable", response, signature);
            }
            wakeLastFired.put(coolKey, now);
            gateCallCount.merge(budgetKey, 1, Integer::sum);
            // 度量（2026-11-05）：token 花了多少、有没有疑似截断（verdicts 空 + 警告非空）
            gateCalls.incrementAndGet();
            gateTokens.addAndGet(response.path("gate_tokens").asLong(0));
            if (response.path("verdicts").isEmpty()
                    && response.path("warnings").isArray() && !response.path("warnings").isEmpty()) {
                gateTruncationSuspect.incrementAndGet();
            }
            return new GateCall("ok", response, signature);
        } catch (Exception e) {
            log.warn("account gate call failed for user {}: {}", userId, e.getMessage());
            gateDegraded.incrementAndGet();
            return new GateCall("unavailable", null, signature);
        }
    }

    private static BigDecimal nz(BigDecimal v) {
        return v == null ? BigDecimal.ZERO : v;
    }
}
