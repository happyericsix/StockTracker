package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * 账户守门员的**裁决事件**（ACCOUNT-GATE 阶段3，2026-11-04）：记忆回路的原子单位。
 *
 * <h3>它回答什么问题</h3>
 * "同类情况出现了多少次"——守门员每次裁决（含 veto/hold）落一行。同一
 * {@code signature} 在窗口内反复出现（默认 14 天 ≥3 次）即触发
 * {@code pattern_escalation}：下一次 15:30 委员会**必须开会**，且证据包带上
 * 这 N 次的完整记录（当时的信号、裁决、理由）。
 *
 * <h3>签名口径（与 docs/ACCOUNT-GATE.md §B3.2 对齐）</h3>
 * 签名 = 守门员唤醒轮次的签名（{@code signal:buy:price_cross_ma} /
 * {@code risk:near_stop+...}），**天然带方向维度**：买入信号被否决与止损位
 * 风险唤醒是两个签名、分别计数——防升级回路把"止损反复触发"误读成
 * "卖出有错"而优化成扛单机器。
 *
 * <h3>生命周期</h3>
 * 只追加、不更新（历史事实）。计数查询走
 * {@code findByUserIdAndSignatureAndCreatedAtAfter}。
 */
@Entity
@Table(name = "paper_gate_events",
        indexes = {
                @Index(name = "idx_gate_events_pattern", columnList = "user_id,signature,created_at"),
                @Index(name = "idx_gate_events_strategy", columnList = "strategy_id,created_at"),
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperGateEvent {

    private static final ZoneId ZONE_CN = ZoneId.of("Asia/Shanghai");

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id")
    private Strategy strategy;

    /** 唤醒轮次签名（带方向维度，见类注释）。 */
    @Column(nullable = false, length = 190)
    private String signature;

    /** 守门员的裁决动作（approve/veto/downsize/sell/partial_sell/hold/flag）。 */
    @Column(nullable = false, length = 32)
    private String action;

    /** 一句话理由（守门员自己的话，复盘原样可读）。 */
    @Column(length = 500)
    private String reason;

    /** 裁决是否被执行（影子模式=false：只记录未执行）。 */
    @Column(name = "executed", nullable = false)
    private Boolean executed;

    /** 触发来源（signal / risk-scan）。 */
    @Column(name = "wake_source", length = 32)
    private String wakeSource;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now(ZONE_CN);
        }
    }
}
