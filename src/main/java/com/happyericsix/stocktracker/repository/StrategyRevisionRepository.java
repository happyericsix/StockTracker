package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.StrategyRevision;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface StrategyRevisionRepository extends JpaRepository<StrategyRevision, Long> {

    List<StrategyRevision> findByStrategyIdOrderByCreatedAtDesc(Long strategyId);

    Optional<StrategyRevision> findByStrategyIdAndStatus(Long strategyId, String status);

    /** 同一策略同时最多一条待审批：审批动作把其余 pending 顶替掉。 */
    List<StrategyRevision> findByStrategyIdAndStatusIn(Long strategyId, List<String> statuses);
}
