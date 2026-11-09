package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * 委员会的**前日预案**（T+1 条件化交易预案）：15:30 开会用当日完整数据，
 * 产出**下一交易日**的条件化 if-then 计划，次日盘中由确定性规则引擎
 * （Python 侧 {@code agent/trade_plan.py}，零 LLM）判定触发并在授权边界内执行。
 *
 * <h3>为什么需要这一层（前日预案制）</h3>
 * 根治"同 bar 决策与成交"偏差：决策（T 日 15:30）严格先于成交（T+1 盘中实时价），
 * 无未来函数。授权（{@link PaperAuthorization}）是**边界**（允许多大仓位），
 * 预案是边界内的**执行时机**（什么条件下动手）——预案不越过授权，
 * 执行时仍被当日授权的 allow_open/allow_close/max_position_pct 夹取。
 *
 * <h3>生命周期</h3>
 * 只追加：委员会每次**真实开会**（非 shadow、agent_llm_calls&gt;0）且输出了 plan
 * 就插一行。{@code planDate} 是预案生效的首个交易日（T+1，Python 按交易日历算好），
 * {@code validUntilDate} 是最后一个有效日（planDate + valid_days - 1 个交易日）。
 * 盘中读"当日仍在有效期的最新一行"；过期/被失效条款打死 → 痕迹留
 * plan_invalidated / plan_trigger_not_met，行本身不删不改（审计链）。
 *
 * <h3>planJson 的形状（与 Python 侧 trade_plan.clean_plan 同源）</h3>
 * {@code {valid_days, summary, entries[{side,trigger{type,value,window,
 * require_volume_ratio},size_fraction,valid_price_range}], exits[...],
 * invalidation[{type,value}|{type:news_high_impact}]}}
 */
@Entity
@Table(name = "paper_trade_plans",
        indexes = {
                @Index(name = "idx_paper_plan_strategy", columnList = "strategy_id,plan_date"),
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperTradePlan {

    private static final ZoneId ZONE_CN = ZoneId.of("Asia/Shanghai");

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false)
    private Strategy strategy;

    /** 预案生效首个交易日（= 签发它的结算日的下一交易日） */
    @Column(name = "plan_date", nullable = false)
    private LocalDate planDate;

    /** 预案最后一个有效日（plan_date + valid_days - 1 个交易日，Python 侧算好写入） */
    @Column(name = "valid_until_date", nullable = false)
    private LocalDate validUntilDate;

    /** 预案全文（clean_plan 归一后的 JSON，含 summary/entries/exits/invalidation） */
    @Column(name = "plan_json", nullable = false, columnDefinition = "LONGTEXT")
    private String planJson;

    /** 签发它的委员会决策痕迹（审计链：预案→会议→证据一串可回溯） */
    @Column(name = "source_trace_id")
    private Long sourceTraceId;

    /** 签发时间（= 产生它的那次委员会结算时间） */
    @Column(name = "issued_at", nullable = false, updatable = false)
    private LocalDateTime issuedAt;

    @PrePersist
    void prePersist() {
        if (issuedAt == null) {
            issuedAt = LocalDateTime.now(ZONE_CN);
        }
    }
}
