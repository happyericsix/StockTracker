package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTrade;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface PaperTradeRepository extends JpaRepository<PaperTrade, Long> {
    List<PaperTrade> findByStrategyIdOrderByTradeDateDescCreatedAtDesc(Long strategyId);
    boolean existsByStrategyIdAndTradeDate(Long strategyId, LocalDate tradeDate);

    /** 盘中语境（2026-10-14）：当日已成交清单——盯盘交易员的"今天动过手"记忆。 */
    List<PaperTrade> findByStrategyIdAndTradeDateOrderByIdAsc(Long strategyId, LocalDate tradeDate);

    /** 决策质量对账用：某用户一段时间内的全部成交（跨策略，按日期倒序）。 */
    List<PaperTrade> findByUserIdAndTradeDateGreaterThanEqualOrderByTradeDateDescCreatedAtDesc(
            Long userId, LocalDate since);
}
