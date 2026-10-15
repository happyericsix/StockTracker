package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;
import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.TradeJournalResponse;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.StrategyRevision;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.StrategyRevisionRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 策略修订闭环（P2，2026-10-15）：提案 → 门禁证据 → 人审批 → 走既有更新路径落地。
 *
 * <p>与季度信（文本提议、永不落地）的分工：本服务产出的是**结构化待审批记录**，
 * 批准动作复用 {@link StrategyService#updateStrategy}（schema 校验 fail-closed +
 * configUpdatedAt 口径打点 + 净值图换线），服务自身绝不直接改 config。
 *
 * <p>健康信号全部是确定性代码算的（命中率/预期判定/净值回撤），生成提案时原样
 * 快照进 healthJson —— 审批的人能看到"当初为什么觉得该改"。
 */
@Service
public class StrategyRevisionService {

    private static final Logger log = LoggerFactory.getLogger(StrategyRevisionService.class);

    /** 命中率预警线：判过 ≥5 次且低于 40% 才算信号（样本太小不叫问题）。 */
    private static final int JOURNAL_MIN_EVALUATED = 5;
    private static final BigDecimal JOURNAL_HIT_RATE_WARN = new BigDecimal("40");

    private final StrategyRepository strategyRepository;
    private final StrategyRevisionRepository revisionRepository;
    private final UserRepository userRepository;
    private final StrategyClient strategyClient;
    private final StrategyService strategyService;
    private final TradeJournalService tradeJournalService;
    private final ExpectationService expectationService;
    private final PaperEquitySnapshotRepository equitySnapshotRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final ObjectMapper mapper;

    public StrategyRevisionService(StrategyRepository strategyRepository,
                                   StrategyRevisionRepository revisionRepository,
                                   UserRepository userRepository,
                                   StrategyClient strategyClient,
                                   StrategyService strategyService,
                                   TradeJournalService tradeJournalService,
                                   ExpectationService expectationService,
                                   PaperEquitySnapshotRepository equitySnapshotRepository,
                                   PaperTradeRepository paperTradeRepository,
                                   ObjectMapper mapper) {
        this.strategyRepository = strategyRepository;
        this.revisionRepository = revisionRepository;
        this.userRepository = userRepository;
        this.strategyClient = strategyClient;
        this.strategyService = strategyService;
        this.tradeJournalService = tradeJournalService;
        this.expectationService = expectationService;
        this.equitySnapshotRepository = equitySnapshotRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.mapper = mapper;
    }

    // ---------------- 健康信号（确定性，无 LLM） ----------------

    /** 信号：{key, label, detail, level: ok|warn|info} —— 提案触发理由的可读版。 */
    public List<ObjectNode> healthSignals(String username, Long strategyId) {
        Strategy strategy = ownedStrategy(username, strategyId);
        List<ObjectNode> signals = new ArrayList<>();

        try {
            TradeJournalResponse journal = tradeJournalService.journal(username, strategyId, 90);
            if (journal.evaluated() >= JOURNAL_MIN_EVALUATED && journal.hitRate() != null
                    && journal.hitRate().compareTo(JOURNAL_HIT_RATE_WARN) < 0) {
                signals.add(signal("journal_hit_rate", "warn", String.format(
                        "决策日志命中率 %s%%（%d 判 %d 中，近 90 天）—— 方向判断持续跑输掷硬币",
                        journal.hitRate(), journal.evaluated(), journal.hits())));
            } else if (journal.evaluated() > 0) {
                signals.add(signal("journal_hit_rate", "ok", String.format(
                        "决策日志命中率 %s%%（%d 判 %d 中）",
                        journal.hitRate() == null ? "—" : journal.hitRate(),
                        journal.evaluated(), journal.hits())));
            }
        } catch (Exception e) {
            log.debug("journal signal unavailable: {}", e.getMessage());
        }

        try {
            User user = getUser(username);
            var expectation = expectationService.latest(user.getId(), strategyId);
            if (expectation != null && ExpectationService.STATUS_UNMET.equals(expectation.status())) {
                signals.add(signal("expectation_unmet", "warn", String.format(
                        "可验证预期未达成（%s，实际 %s）—— 这条策略没兑现对自己的承诺",
                        ExpectationService.describe(expectation),
                        expectation.outcome() == null ? "—" : expectation.outcome())));
            }
        } catch (Exception e) {
            log.debug("expectation signal unavailable: {}", e.getMessage());
        }

        try {
            var snapshots = equitySnapshotRepository
                    .findByStrategyIdOrderByTradeDateAsc(strategy.getId());
            var summary = PaperEquitySeries.summarize(snapshots);
            if (summary.hasPeriod()) {
                if (summary.returnPct().signum() < 0
                        && summary.maxDrawdownPct().abs().compareTo(new BigDecimal("10")) >= 0) {
                    signals.add(signal("equity_drawdown", "warn", String.format(
                            "净值 %s%%、最大回撤 %s%%（%s ~ %s）",
                            summary.returnPct(), summary.maxDrawdownPct(),
                            summary.firstDate(), summary.lastDate())));
                } else {
                    signals.add(signal("equity", "ok", String.format(
                            "净值 %s%%、最大回撤 %s%%、对买入持有超额 %s%%",
                            summary.returnPct(), summary.maxDrawdownPct(),
                            summary.excessVsBuyAndHoldPct())));
                }
            }
        } catch (Exception e) {
            log.debug("equity signal unavailable: {}", e.getMessage());
        }

        if (signals.isEmpty()) {
            signals.add(signal("none", "info",
                    "暂无明确的不健康信号（样本可能还少）。仍可让提案师基于近期行情表现给参数建议。"));
        }
        return signals;
    }

    private ObjectNode signal(String key, String level, String detail) {
        ObjectNode node = mapper.createObjectNode();
        node.put("key", key);
        node.put("level", level);
        node.put("detail", detail);
        return node;
    }

    /** 控制器出口：ObjectNode → 简单 Map（JSON 序列化友好）。 */
    public List<java.util.Map<String, Object>> healthSignalsRaw(String username, Long strategyId) {
        List<java.util.Map<String, Object>> out = new ArrayList<>();
        for (ObjectNode node : healthSignals(username, strategyId)) {
            java.util.Map<String, Object> m = new java.util.LinkedHashMap<>();
            for (var entry : node.properties()) {
                m.put(entry.getKey(), entry.getValue().asText());
            }
            out.add(m);
        }
        return out;
    }

    // ---------------- 生成提案（1 次 LLM + 门禁，Python 侧） ----------------

    @Transactional
    public StrategyRevision generate(String username, Long strategyId) {
        Strategy strategy = ownedStrategy(username, strategyId);

        revisionRepository.findByStrategyIdAndStatus(strategyId, StrategyRevision.STATUS_PENDING)
                .ifPresent(r -> { throw new IllegalArgumentException(
                        "已有一条待审批提案：先处理它（批准/拒绝），再生成新的"); });

        ObjectNode payload = mapper.createObjectNode();
        payload.put("strategy_id", strategyId);
        payload.set("config", readTree(strategy.getConfigJson()));
        ObjectNode meta = payload.putObject("strategy");
        meta.put("id", strategyId);
        meta.put("name", strategy.getName());
        meta.put("symbol", strategy.getSymbol());

        ArrayNode signals = payload.putArray("health_signals");
        healthSignals(username, strategyId).forEach(signals::add);
        attachContext(payload, strategyId);

        JsonNode result = strategyClient.strategyRevision(payload);
        if (result == null || !result.path("ok").asBoolean(false)) {
            String error = result == null ? "提案服务无响应"
                    : result.path("error").asText("提案生成失败");
            throw new IllegalArgumentException(error);
        }
        JsonNode proposal = result.path("proposal");
        JsonNode evidence = result.path("evidence");

        StrategyRevision revision = revisionRepository.save(StrategyRevision.builder()
                .strategy(strategy)
                .status(StrategyRevision.STATUS_PENDING)
                .gatePassed(evidence.path("passed").asBoolean(false))
                .proposalJson(proposal.toString())
                .evidenceJson(evidence.toString())
                .healthJson(signals.toString())
                .configBefore(strategy.getConfigJson())
                .createdAt(LocalDateTime.now())
                .build());
        log.info("User {} generated revision proposal for strategy {} (gatePassed={}, diffCount={})",
                username, strategyId, revision.getGatePassed(),
                proposal.path("diff").size());
        return revision;
    }

    /** 给提案师的上下文：近期成交 + 净值摘要（全部确定性数字，fail-open）。 */
    private void attachContext(ObjectNode payload, Long strategyId) {
        try {
            var trades = paperTradeRepository
                    .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategyId);
            ArrayNode arr = payload.putArray("recent_trades");
            trades.stream().limit(10).forEach(t -> {
                ObjectNode n = arr.addObject();
                n.put("date", t.getTradeDate() == null ? null : t.getTradeDate().toString());
                n.put("side", t.getSide());
                n.put("price", t.getPrice() == null ? null : t.getPrice().toPlainString());
                n.put("shares", t.getShares() == null ? null : t.getShares().toPlainString());
                n.put("reason", t.getReason());
            });
        } catch (Exception e) {
            log.debug("recent trades context unavailable: {}", e.getMessage());
        }
        try {
            var snapshots = equitySnapshotRepository
                    .findByStrategyIdOrderByTradeDateAsc(strategyId);
            var summary = PaperEquitySeries.summarize(snapshots);
            if (summary.hasPeriod()) {
                ObjectNode eq = payload.putObject("equity_summary");
                eq.put("days", summary.days());
                eq.put("return_pct", summary.returnPct().toPlainString());
                eq.put("max_drawdown_pct", summary.maxDrawdownPct().toPlainString());
                eq.put("excess_vs_buy_and_hold_pct",
                        summary.excessVsBuyAndHoldPct() == null
                                ? null : summary.excessVsBuyAndHoldPct().toPlainString());
                eq.put("flat_ratio_pct", summary.flatRatioPct().toPlainString());
            }
        } catch (Exception e) {
            log.debug("equity context unavailable: {}", e.getMessage());
        }
    }

    // ---------------- 审批（人签字；落地走既有更新路径） ----------------

    /** 批准：复用 updateStrategy（schema fail-closed 校验 + configUpdatedAt 打点）。 */
    @Transactional
    public StrategyRevision approve(String username, Long strategyId, Long revisionId, String note) {
        StrategyRevision revision = ownedPendingRevision(username, strategyId, revisionId);
        if (!Boolean.TRUE.equals(revision.getGatePassed())) {
            throw new IllegalArgumentException(
                    "该提案未通过回测门禁（近期加权口径下没跑赢现版），不予批准 —— 可拒绝后重新生成");
        }
        JsonNode proposal = readTree(revision.getProposalJson());
        String newConfig = proposal.path("config").toString();
        if (newConfig.isBlank() || proposal.path("config").isMissingNode()) {
            throw new IllegalArgumentException("提案记录缺少新配置，无法应用");
        }
        // 走唯一更新路径：校验、configUpdatedAt、口径分界线全部由它负责
        strategyService.updateStrategy(username, strategyId,
                new StrategyRequest(currentName(username, strategyId),
                        currentSymbol(username, strategyId), newConfig));

        revision.setStatus(StrategyRevision.STATUS_APPROVED);
        revision.setNote(blankToNull(note));
        revision.setDecidedAt(LocalDateTime.now());
        supersedeOthers(strategyId, revisionId);
        log.info("User {} approved revision {} for strategy {} (config applied)",
                username, revisionId, strategyId);
        return revisionRepository.save(revision);
    }

    @Transactional
    public StrategyRevision reject(String username, Long strategyId, Long revisionId, String note) {
        StrategyRevision revision = ownedPendingRevision(username, strategyId, revisionId);
        revision.setStatus(StrategyRevision.STATUS_REJECTED);
        revision.setNote(blankToNull(note));
        revision.setDecidedAt(LocalDateTime.now());
        log.info("User {} rejected revision {} for strategy {}", username, revisionId, strategyId);
        return revisionRepository.save(revision);
    }

    public List<StrategyRevision> list(String username, Long strategyId) {
        Strategy strategy = ownedStrategy(username, strategyId);
        return revisionRepository.findByStrategyIdOrderByCreatedAtDesc(strategy.getId());
    }

    // ---------------- 内部 ----------------

    private void supersedeOthers(Long strategyId, Long keepId) {
        revisionRepository.findByStrategyIdAndStatusIn(strategyId,
                        List.of(StrategyRevision.STATUS_PENDING))
                .forEach(r -> {
                    if (!r.getId().equals(keepId)) {
                        r.setStatus(StrategyRevision.STATUS_SUPERSEDED);
                        r.setDecidedAt(LocalDateTime.now());
                        revisionRepository.save(r);
                    }
                });
    }

    private StrategyRevision ownedPendingRevision(String username, Long strategyId, Long revisionId) {
        Strategy strategy = ownedStrategy(username, strategyId);
        StrategyRevision revision = revisionRepository.findById(revisionId)
                .orElseThrow(() -> new IllegalArgumentException("提案不存在"));
        if (!revision.getStrategy().getId().equals(strategy.getId())) {
            throw new IllegalArgumentException("提案不属于这条策略");
        }
        if (!StrategyRevision.STATUS_PENDING.equals(revision.getStatus())) {
            throw new IllegalArgumentException("该提案已处理过（" + revision.getStatus() + "）");
        }
        return revision;
    }

    private Strategy ownedStrategy(String username, Long strategyId) {
        User user = getUser(username);
        return strategyRepository.findByIdAndUserId(strategyId, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
    }

    private String currentName(String username, Long strategyId) {
        return ownedStrategy(username, strategyId).getName();
    }

    private String currentSymbol(String username, Long strategyId) {
        return ownedStrategy(username, strategyId).getSymbol();
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }

    private static String blankToNull(String s) {
        return s == null || s.isBlank() ? null : s;
    }

    private JsonNode readTree(String json) {
        try {
            return mapper.readTree(json == null ? "{}" : json);
        } catch (Exception e) {
            return mapper.createObjectNode();
        }
    }
}
