package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.StrategyRevision;

/**
 * 修订提案的对外视图：proposal/evidence/health 都是**生成时刻的原文 JSON**，
 * 前端原样解析展示 —— 审批的人看到的证据与落档的证据是同一份，不存在"两套口径"。
 */
public record StrategyRevisionResponse(
        Long id,
        Long strategyId,
        String status,
        Boolean gatePassed,
        String proposalJson,
        String evidenceJson,
        String healthJson,
        String createdAt,
        String decidedAt,
        String note) {

    public static StrategyRevisionResponse from(StrategyRevision r) {
        return new StrategyRevisionResponse(
                r.getId(),
                r.getStrategy() == null ? null : r.getStrategy().getId(),
                r.getStatus(),
                r.getGatePassed(),
                r.getProposalJson(),
                r.getEvidenceJson(),
                r.getHealthJson(),
                r.getCreatedAt() == null ? null : r.getCreatedAt().toString(),
                r.getDecidedAt() == null ? null : r.getDecidedAt().toString(),
                r.getNote());
    }
}
