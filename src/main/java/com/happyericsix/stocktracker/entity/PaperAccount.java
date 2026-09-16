package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

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

    @Column
    private Double initialCapital;

    @Column
    private Double cash;

    @Column
    private Double shares;

    @Column
    private Double avgCost;

    @Column
    private Double equity;

    @Column
    private Double highWatermark;

    @Column
    private Double lastPrice;

    @Column
    private String lastSignal;

    @Column
    private LocalDateTime lastEvalAt;

    @Column
    private String lastBarTime;

    /** T+1 守卫：最近一次买入对应的 bar 时间（realtime 路径），卖出遇同一自然日被阻止 */
    @Column(name = "last_buy_bar")
    private String lastBuyBar;

    @Column(nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column
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
