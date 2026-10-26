package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.StrategyRevision;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface StrategyRevisionRepository extends JpaRepository<StrategyRevision, Long> {

    List<StrategyRevision> findByStrategyIdOrderByCreatedAtDesc(Long strategyId);

    Optional<StrategyRevision> findByStrategyIdAndStatus(Long strategyId, String status);

    /** 同一策略同时最多一条待审批：审批动作把其余 pending 顶替掉。 */
    List<StrategyRevision> findByStrategyIdAndStatusIn(Long strategyId, List<String> statuses);

    /**
     * 多重检验预算：滚动窗口内已生成的提案数（被顶替的 superseded 不算——它从未
     * 进入过门禁，重生成顶掉待审批是合理操作，不该烧预算）。
     */
    long countByStrategyIdAndStatusNotAndCreatedAtAfter(
            Long strategyId, String status, LocalDateTime since);
}
