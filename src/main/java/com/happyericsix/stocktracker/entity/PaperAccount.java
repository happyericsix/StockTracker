package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 模拟盘账户（每策略一个）。
 *
 * <h3>为什么金额是 BigDecimal / DECIMAL</h3>
 * 净值恒等式 {@code equity == cash + shares × price} 要能**零容差**成立。
 * 用 {@code double} 时它结构上做不到（{@code 0.1 + 0.2 != 0.3}）：
 * 一次买入就能让"现金 + 持仓市值"与记录的净值差出 1e-13。单次看不出，
 * 但净值曲线、回撤、对账、"净值什么时候真的变了"全都建在这条等式上。
 *
 * <p>口径由 {@link com.happyericsix.stocktracker.service.Money} 单处定义
 * （价格 4 位、金额 2 位、净值 2 位、收益率 4 位，HALF_UP），
 * 与 Python 侧 {@code MoneyPolicy} 逐字一致（有跨语言测试盯着）。
 */
@Entity
@Table(name = "paper_accounts")
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperAccount {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @OneToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false, unique = true)
    private Strategy strategy;

    @Column(precision = 18, scale = 2)
    private BigDecimal initialCapital;

    @Column(precision = 18, scale = 2)
    private BigDecimal cash;

    @Column(precision = 18, scale = 4)
    private BigDecimal shares;

    @Column(name = "avg_cost", precision = 18, scale = 4)
    private BigDecimal avgCost;

    @Column(precision = 18, scale = 2)
    private BigDecimal equity;

    @Column(name = "high_watermark", precision = 18, scale = 4)
    private BigDecimal highWatermark;

    @Column(name = "last_price", precision = 18, scale = 4)
    private BigDecimal lastPrice;

    @Column(name = "last_signal")
    private String lastSignal;

    @Column(name = "last_eval_at")
    private LocalDateTime lastEvalAt;

    @Column(name = "last_bar_time")
    private String lastBarTime;

    /** T+1 守卫：最近一次买入对应的 bar 时间（realtime 路径），卖出遇同一自然日被阻止 */
    @Column(name = "last_buy_bar")
    private String lastBuyBar;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
        updatedAt = createdAt;
    }

    @PreUpdate
    protected void onUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
