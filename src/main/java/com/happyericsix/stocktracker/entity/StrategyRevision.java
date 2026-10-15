package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 策略修订提案（P2 修订闭环，2026-10-15）。
 *
 * <p>生命周期：pending（已生成待审批）→ approved（人签字，配置已应用）/
 * rejected（人否决）/ superseded（审批另一条时被顶替）。
 *
 * <p>口径纪律：proposalJson 存的是 Python {@code agent/revision.py} 的提案
 * （新 config + 参数 diff + 理由 + 预期），evidenceJson 存的是**当时的**回测门禁
 * 证据（数据窗口、加权口径、新旧指标）——审批时人看的就是这份证据，批准动作
 * 不重新生成提案，只做 schema 复验后走既有 updateStrategy 路径（configUpdatedAt
 * 打点、净值图换线都由那条路径负责）。
 */
@Entity
@Table(name = "strategy_revisions",
        indexes = {@Index(name = "idx_revision_strategy", columnList = "strategy_id")})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class StrategyRevision {

    public static final String STATUS_PENDING = "pending";
    public static final String STATUS_APPROVED = "approved";
    public static final String STATUS_REJECTED = "rejected";
    public static final String STATUS_SUPERSEDED = "superseded";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false)
    private Strategy strategy;

    @Column(nullable = false)
    private String status;

    /** 门禁结论（生成时刻的回测证据判定）。批准接口会再次校验，不信旧值。 */
    @Column(nullable = false)
    private Boolean gatePassed;

    /** 提案原文：{config, diff, rationale, expected_effect}（Python 产出，原样留档）。 */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String proposalJson;

    /** 证据原文：{passed, reason, old, new, window}（时间衰减加权口径）。 */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String evidenceJson;

    /** 生成提案时的健康信号快照（触发理由的可追溯性）。 */
    @Column(columnDefinition = "TEXT")
    private String healthJson;

    /** 提案基于的旧配置快照：批准后 diff 仍可对账。 */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String configBefore;

    @Column(nullable = false)
    private LocalDateTime createdAt;

    private LocalDateTime decidedAt;

    /** 人的批注（拒绝理由 / 批准备注），可空。 */
    @Column(columnDefinition = "TEXT")
    private String note;
}
