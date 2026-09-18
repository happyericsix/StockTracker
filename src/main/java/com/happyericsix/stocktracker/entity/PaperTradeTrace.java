package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 一次**值得留痕**的模拟盘结算决策（一行 = 一根 bar 上的一个结论）。
 *
 * <h3>它解决什么问题</h3>
 * 在此之前，"今天为什么没成交"的答案只存在于日志里，而且只在少数分支上打印。
 * 用户看到的是一张没变的账户：没有成交、没有解释、也没有任何可查的东西。
 * 痕迹把每一根 bar 的结论连同**当时的证据**一起落库：
 * 那根 bar 的 OHLC、当时算出的指标值、当时用的参数、账户结算前后的钱与股数、
 * 以及**为什么**是这个结论（{@code skip_reason}）。
 *
 * <h3>它承诺什么、不承诺什么</h3>
 * <b>承诺</b>：快照与可审计 —— "当时那根 bar、当时那些指标值就是证据"，支持对账与抽查。
 * <b>不承诺可复算</b>：行情按前复权取，除权后历史 bar 会变；且引擎版本会演进。
 * 所以 {@code traceHash} 的用途是**防篡改 / 对账**，不是"用同样输入再算一遍应当得到它"。
 *
 * <h3>为什么钱是 BigDecimal / DECIMAL</h3>
 * 痕迹是钱的证据。用 double 存，等于让证据本身带着二进制误差，
 * 而"净值 = 现金 + 股数 × 价格"这条恒等式就永远只能"看起来差不多"（见 {@code Money}）。
 *
 * <h3>去重</h3>
 * {@code dedupeKey} 是唯一键：日线结算每个交易日一行、成交按 bar 与方向一行、
 * 阻塞型跳过按"策略+日期+原因"一行（重复发生时累加 {@code repeatCount} 而不是再写一行）。
 * 有了它，"结算重跑"和"实时路径每 5 分钟一次"都不会把痕迹冲成噪声。
 */
@Entity
@Table(name = "paper_trade_traces",
        uniqueConstraints = @UniqueConstraint(name = "uk_paper_trace_dedupe", columnNames = "dedupe_key"),
        indexes = {
                @Index(name = "idx_paper_trace_strategy", columnList = "strategy_id,trade_date"),
                @Index(name = "idx_paper_trace_skip", columnList = "strategy_id,skip_reason"),
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperTradeTrace {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false)
    private Strategy strategy;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "account_id")
    private PaperAccount account;

    /** 去重键（唯一）。形状见 {@code PaperTraceService.dedupeKey}。 */
    @Column(name = "dedupe_key", nullable = false, length = 190)
    private String dedupeKey;

    // ---------- identity ----------

    /** daily | realtime（决定成交价口径，见 {@code ExecutionContract}）。 */
    @Column(name = "settlement_kind", nullable = false, length = 32)
    private String settlementKind;

    /**
     * 这一条决策是**谁做的**：{@code rule}（策略 DSL）或 {@code agent}（多角色委员会）。
     *
     * <p>做成独立列而不是只放在快照 JSON 里：报告与统计要能按它**分组查询**
     * （"agent 段和规则段各自表现如何"），而 JSON 里的字段查不动。
     */
    @Column(name = "decision_mode", length = 16)
    private String decisionMode;

    /** agent 这一轮的 LLM 调用次数与 token 总量（规则路径为空）。成本必须可查，不能只存在日志里。 */
    @Column(name = "agent_llm_calls")
    private Integer agentLlmCalls;

    @Column(name = "agent_tokens")
    private Integer agentTokens;

    /** cron | event | manual：这一行是谁触发的（运营口径，不参与"能不能对比"的判定）。 */
    @Column(nullable = false, length = 32)
    private String trigger;

    @Column(name = "trade_date", nullable = false)
    private LocalDate tradeDate;

    /** 实时路径的 bar 时间（`yyyy-MM-dd HH:mm:ss`）；日线为空。 */
    @Column(name = "bar_time", length = 64)
    private String barTime;

    /** 证据那根 bar 的日期 —— 由 Python 快照回传，可能**早于** tradeDate（数据未出时不允许冒充当日）。 */
    @Column(name = "bar_date", length = 32)
    private String barDate;

    @Column(name = "symbol", length = 32)
    private String symbol;

    // ---------- decision ----------

    /** buy | sell | skip（封闭集）。 */
    @Column(nullable = false, length = 32)
    private String decision;

    /** 仅 decision=skip 时有值（封闭集；未知值归一成 unknown_*）。 */
    @Column(name = "skip_reason", length = 32)
    private String skipReason;

    /** 引擎判定的信号（buy/sell/hold）—— 与 decision 的区别：hold 但被 T+1 挡住时 decision 仍是 skip。 */
    @Column(name = "signal", length = 16)
    private String signal;

    @Column(name = "matched_conditions", length = 512)
    private String matchedConditions;

    /** 成交价口径（close / realtime_last / next_open）。 */
    @Column(name = "fill_basis", length = 32)
    private String fillBasis;

    /** 这一根 bar 上真正用于结算的价格（按成交价口径取的候选价）。 */
    @Column(name = "bar_close", precision = 18, scale = 4)
    private BigDecimal barClose;

    // ---------- 账户前后 ----------

    @Column(name = "cash_before", precision = 18, scale = 2)
    private BigDecimal cashBefore;

    @Column(name = "shares_before", precision = 18, scale = 4)
    private BigDecimal sharesBefore;

    @Column(name = "avg_cost_before", precision = 18, scale = 4)
    private BigDecimal avgCostBefore;

    @Column(name = "equity_before", precision = 18, scale = 2)
    private BigDecimal equityBefore;

    @Column(name = "cash_after", precision = 18, scale = 2)
    private BigDecimal cashAfter;

    @Column(name = "shares_after", precision = 18, scale = 4)
    private BigDecimal sharesAfter;

    @Column(name = "avg_cost_after", precision = 18, scale = 4)
    private BigDecimal avgCostAfter;

    @Column(name = "equity_after", precision = 18, scale = 2)
    private BigDecimal equityAfter;

    // ---------- provenance + evidence ----------

    @Column(name = "engine_version", length = 32)
    private String engineVersion;

    @Column(name = "adjust_mode", length = 16)
    private String adjustMode;

    @Column(name = "money_policy_version")
    private Integer moneyPolicyVersion;

    /** Python 回传的完整快照 JSON（bar / indicators / params / fingerprint / extra）。 */
    @Column(name = "snapshot_json", columnDefinition = "LONGTEXT")
    private String snapshotJson;

    @Column(name = "snapshot_schema_version")
    private Integer snapshotSchemaVersion;

    /** 防篡改 / 对账用（**不是**"可复算"的承诺）。 */
    @Column(name = "trace_hash", length = 64)
    private String traceHash;

    // ---------- 重复（降噪） ----------

    /** 同一个去重键出现了几次。阻塞型跳过在实时路径上会反复发生，只累加、不再写行。 */
    @Column(name = "repeat_count", nullable = false)
    private Integer repeatCount;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "last_seen_at", nullable = false)
    private LocalDateTime lastSeenAt;

    @PrePersist
    protected void onCreate() {
        LocalDateTime now = LocalDateTime.now();
        createdAt = now;
        lastSeenAt = now;
        if (repeatCount == null) {
            repeatCount = 1;
        }
    }
}
