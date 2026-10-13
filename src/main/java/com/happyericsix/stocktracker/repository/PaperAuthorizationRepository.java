package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperAuthorization;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

/**
 * 盘中授权仓储。只读入口唯一：{@code findLatest}——
 * 授权是"自上次开会起"的持续状态，最新一行生效（见实体的生命周期说明）。
 */
@Repository
public interface PaperAuthorizationRepository extends JpaRepository<PaperAuthorization, Long> {

    Optional<PaperAuthorization> findFirstByStrategyIdOrderByIssuedAtDesc(Long strategyId);

    default Optional<PaperAuthorization> findLatest(Long strategyId) {
        if (strategyId == null) {
            return Optional.empty();
        }
        return findFirstByStrategyIdOrderByIssuedAtDesc(strategyId);
    }
}
