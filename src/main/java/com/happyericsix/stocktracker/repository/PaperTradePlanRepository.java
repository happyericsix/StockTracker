package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTradePlan;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

/**
 * 前日预案（paper_trade_plans，只追加）的读取口径。
 *
 * <p>"当日生效预案" = plan_date ≤ today ≤ valid_until_date 的**最新**一行：
 * 同一天可能有多行，且是<b>常态而非异常</b>——委员会每天开会在尾日预案仍有效时
 * 就签发次日预案（valid_days=2 时新旧两份在交界日重叠一天）。2026-11-18 线上事故：
 * 查询曾用 Optional 直收，重叠日两行使每个盘中评估都抛 IncorrectResultSize（一天
 * 246 次），预案的盘中确定性执行臂全天瘫痪。所以这里必须 List 取首行，"最新覆盖
 * 旧"是读取语义，不是数据库约束。
 */
public interface PaperTradePlanRepository extends JpaRepository<PaperTradePlan, Long> {

    /** 生效中的预案候选（按签发新旧倒序）。可能多行：见类注释的重叠语义。 */
    @Query("select p from PaperTradePlan p where p.strategy.id = :strategyId"
            + " and p.planDate <= :today and p.validUntilDate >= :today"
            + " order by p.planDate desc, p.id desc")
    List<PaperTradePlan> findActiveCandidatesForStrategy(@Param("strategyId") Long strategyId,
                                                         @Param("today") LocalDate today);

    /** 当日生效预案 = 候选中最新一行；没有则空。调用方照旧用 Optional。 */
    default Optional<PaperTradePlan> findActiveForStrategy(Long strategyId, LocalDate today) {
        return findActiveCandidatesForStrategy(strategyId, today).stream().findFirst();
    }

    /** 最近一行预案（不过滤生效期——给 /paper/overview 展示"当前预案"用）。 */
    Optional<PaperTradePlan> findFirstByStrategyIdOrderByIdDesc(Long strategyId);
}
