package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTradePlan;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.Optional;

/**
 * 前日预案（paper_trade_plans，只追加）的读取口径。
 *
 * <p>"当日生效预案" = plan_date ≤ today ≤ valid_until_date 的**最新**一行：
 * 同一天可能有多行（早盘结算重跑重签），最新一行覆盖旧预案（旧的不删，审计链保留）。
 */
public interface PaperTradePlanRepository extends JpaRepository<PaperTradePlan, Long> {

    @Query("select p from PaperTradePlan p where p.strategy.id = :strategyId"
            + " and p.planDate <= :today and p.validUntilDate >= :today"
            + " order by p.planDate desc, p.id desc")
    Optional<PaperTradePlan> findActiveForStrategy(@Param("strategyId") Long strategyId,
                                                   @Param("today") LocalDate today);

    /** 最近一行预案（不过滤生效期——给 /paper/overview 展示"当前预案"用）。 */
    Optional<PaperTradePlan> findFirstByStrategyIdOrderByIdDesc(Long strategyId);
}
