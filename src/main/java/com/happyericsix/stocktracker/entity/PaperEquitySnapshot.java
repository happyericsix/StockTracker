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
 * 每日模拟盘净值快照（一行 = 一个交易日收盘后的账户状态）。
 *
 * <h3>为什么必须有它</h3>
 * {@code PaperAccount} 只存**当前值**，于是任何"随时间"的问题都算不出来：
 * 最大回撤、空仓了多少个交易日、这段时间跑赢还是跑输了买入持有。
 * 客观事实白名单里 {@code paper_max_drawdown_pct} 一直被刻意留空，原因就是它（见
 * {@code agent/objective.py} 的"刻意还没有的键"）。
 *
 * <h3>它同时是"机会成本记账"的唯一依据</h3>
 * 空仓在账面上是 0 收益，看起来没有代价 —— 这正是"不动不会被惩罚"的根源。
 * 有了同期的收盘价序列，报告才能写出：<b>这段时间买入持有是 +X%，空仓的代价是 -X%</b>。
 * 所以这张表要存 {@code closePrice}，而不只是净值。
 *
 * <h3>为什么金额是 DECIMAL</h3>
 * 与痕迹同一条纪律：净值曲线的每一格都要能与"现金 + 股数 × 价格"逐笔对上账，
 * 用 double 存就永远只能"差不多"。
 *
 * <h3>只记日线结算</h3>
 * 实时路径一天会跑几十上百次，那不是"日度净值"，是分时抖动。
 * 缺口（某天没结算）不补：曲线上的洞是**事实**，补齐才是编造。
 */
@Entity
@Table(name = "paper_equity_snapshots",
        uniqueConstraints = @UniqueConstraint(name = "uk_paper_equity_day",
                columnNames = {"strategy_id", "trade_date"}),
        indexes = @Index(name = "idx_paper_equity_strategy", columnList = "strategy_id,trade_date"))
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperEquitySnapshot {

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

    @Column(name = "trade_date", nullable = false)
    private LocalDate tradeDate;

    /** 收盘后净值（现金 + 持仓市值）。 */
    @Column(nullable = false, precision = 18, scale = 2)
    private BigDecimal equity;

    @Column(nullable = false, precision = 18, scale = 2)
    private BigDecimal cash;

    @Column(nullable = false, precision = 18, scale = 4)
    private BigDecimal shares;

    /** 当日收盘价：既用于核对净值恒等式，也是"同期买入持有"的基准。 */
    @Column(name = "close_price", precision = 18, scale = 4)
    private BigDecimal closePrice;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    protected void onCreate() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
    }
}
