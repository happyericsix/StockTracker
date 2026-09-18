package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
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

    /** 成交价（价格口径 4 位小数）。 */
    @Column(nullable = false, precision = 18, scale = 4)
    private BigDecimal price;

    @Column(nullable = false, precision = 18, scale = 4)
    private BigDecimal shares;

    /** 成交额 = 价格 × 股数（金额口径 2 位小数）。 */
    @Column(nullable = false, precision = 18, scale = 2)
    private BigDecimal amount;

    @Column
    private String reason;

    /**
     * 这一笔对应的痕迹行 id（{@code paper_trade_traces.id}）。
     *
     * <p><b>为 null 有两种含义，界面必须区分</b>：痕迹功能上线**之前**的成交（历史行不可能回填），
     * 或者痕迹写入失败（fail-open：成交照常，记录少了 —— 这种情况由
     * {@code PaperTraceService.writeFailures()} 计数暴露）。
     * 用普通列而不是 {@code @ManyToOne}：痕迹可能不存在，而这里只需要一个可跳转的 id。
     */
    @Column(name = "trace_id")
    private Long traceId;

    @Column(nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    protected void onCreate() {
        createdAt = LocalDateTime.now();
    }
}
