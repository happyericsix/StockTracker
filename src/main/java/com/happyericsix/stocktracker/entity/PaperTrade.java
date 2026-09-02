package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "paper_trades")
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperTrade {

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
    @JoinColumn(name = "account_id", nullable = false)
    private PaperAccount account;

    @Column(nullable = false)
    private LocalDate tradeDate;

    @Column(nullable = false)
    private String symbol;

    @Column(nullable = false)
    private String side;

    @Column(nullable = false)
    private Double price;

    @Column(nullable = false)
    private Double shares;

    @Column(nullable = false)
    private Double amount;

    @Column
    private String reason;

    @Column(nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
    }
}
