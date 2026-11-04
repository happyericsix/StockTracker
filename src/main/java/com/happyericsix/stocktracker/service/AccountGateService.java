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
                              StrategyClient strategyClient) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperAuthorizationRepository = paperAuthorizationRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.lotRepository = lotRepository;
        this.portfolioService = portfolioService;
        this.strategyClient = strategyClient;
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
        return panel;
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
                return new GateCall("unavailable", response, signature);
            }
            wakeLastFired.put(coolKey, now);
            gateCallCount.merge(budgetKey, 1, Integer::sum);
            return new GateCall("ok", response, signature);
        } catch (Exception e) {
            log.warn("account gate call failed for user {}: {}", userId, e.getMessage());
            return new GateCall("unavailable", null, signature);
        }
    }

    private static BigDecimal nz(BigDecimal v) {
        return v == null ? BigDecimal.ZERO : v;
    }
}
