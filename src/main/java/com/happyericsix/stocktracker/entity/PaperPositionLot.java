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
 * 持仓批次账本（2026-10-15）：每一笔买入都是一个批次，卖出按【先进先出】消耗批次。
 *
 * <h3>为什么必须有它</h3>
 * 真实券商的持仓就是按批次记账的（APP 里的"持仓批次/成本明细"），A 股的三条真实规则
 * 全部建立在批次之上：
 * <ul>
 *   <li>T+1 是批次级的——当日买入的批次锁定，老批次当日可卖；</li>
 *   <li>卖出遵循先进先出（FIFO）；</li>
 *   <li>红利税按卖出批次的持股期限分档补扣（证监会深圳监管局有专门投教案例）。</li>
 * </ul>
 * 对 agent 它还有更直接的意义：**卖出决策第一次能对着具体批次说话**——"卖掉 10-14
 * 那批成本 7.1271 的仓位（持有 1 天、浮亏 2.3%）"而不是"把持仓全抛了"。
 *
 * <h3>口径</h3>
 * {@code remainingShares} 递减到 0 即批次平掉；成本口径与账户的移动加权 avg_cost 并行
 * （avg_cost 用于展示与市值，批次用于决策/计税/T+1/已实现盈亏）。送转股按比例并入
 * 原批次（股数 ×(1+ratio)、成本 ÷(1+ratio)），与除权参考价公式一致。
 */
@Entity
@Table(name = "paper_position_lots", indexes = {
        @Index(name = "idx_lots_strategy", columnList = "strategy_id"),
        @Index(name = "idx_lots_strategy_open", columnList = "strategy_id,remaining_shares")
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class PaperPositionLot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false)
    private Strategy strategy;

    /** 开仓日（T+1 判定与持股期限的基准）。 */
    @Column(name = "opened_date", nullable = false)
    private LocalDate openedDate;

    /** 开仓时刻（盘中 bar 时间或日线结算日），给 agent 看的"哪一笔"。 */
    @Column(name = "opened_bar", length = 64)
    private String openedBar;

    /** 来源买入成交 id（审计可反查）。 */
    @Column(name = "trade_id")
    private Long tradeId;

    /** 本批成交价（含滑点，0.01 网格后的真实成交价）。 */
    @Column(name = "price", nullable = false, precision = 18, scale = 4)
    private BigDecimal price;

    /** 本批买入费用（佣金+过户费）——摊进成本口径。 */
    @Column(name = "fee", precision = 18, scale = 2)
    private BigDecimal fee;

    /** 原始股数。 */
    @Column(name = "shares", nullable = false, precision = 18, scale = 4)
    private BigDecimal shares;

    /** 剩余股数（卖出递减；0 = 批次已平）。 */
    @Column(name = "remaining_shares", nullable = false, precision = 18, scale = 4)
    private BigDecimal remainingShares;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at", nullable = false)
    private LocalDateTime updatedAt;
}
