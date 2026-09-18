package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDate;
import java.time.LocalDateTime;

@Entity
@Table(name = "strategies")
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class Strategy {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(nullable = false)
    private String name;

    @Column(nullable = false)
    private String symbol;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String configJson;

    @Builder.Default
    @Column(nullable = false)
    private Boolean paperEnabled = false;

    @Column(nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(nullable = false)
    private LocalDateTime updatedAt;

    @Column
    private LocalDateTime lastBacktestAt;

    @Column
    private LocalDateTime lastPaperEvalAt;

    /**
     * 决策来源：{@code rule}（策略 DSL）或 {@code agent}（多角色委员会）。
     *
     * <p><b>为什么是策略上的字段而不是一个新的策略类型</b>：同一张策略记录、同一套参数，
     * 换的是**谁做决定**。把它做成新类型会让"这条策略的历史"断成两半；
     * 做成字段 + 生效日期，历史是连续的，而 {@code fingerprint.decision_mode} 保证
     * 两段曲线的数字**永远不会被当成可比**。
     *
     * <p>{@code null}（存量数据）一律按 {@code rule} 处理 —— 不写默认值、不做数据回填，
     * 因为"没切换过"本来就是事实。
     */
    @Column(name = "decision_mode", length = 16)
    private String decisionMode;

    /** 当前决策方式的**生效日**：曲线上的分界点，报告必须写明。 */
    @Column(name = "decision_mode_since")
    private LocalDate decisionModeSince;

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
