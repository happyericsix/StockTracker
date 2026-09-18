package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

@Repository
public interface PaperEquitySnapshotRepository extends JpaRepository<PaperEquitySnapshot, Long> {

    /** 幂等的依据：每个交易日一行（结算重跑不会多一行）。 */
    Optional<PaperEquitySnapshot> findByStrategyIdAndTradeDate(Long strategyId, LocalDate tradeDate);

    /** 画曲线与算回撤：按时间正序。 */
    List<PaperEquitySnapshot> findByStrategyIdOrderByTradeDateAsc(Long strategyId);

    /** 取最近 N 个交易日（倒序取，调用方自行反转）。 */
    List<PaperEquitySnapshot> findByStrategyIdOrderByTradeDateDesc(Long strategyId, Pageable pageable);

    long countByStrategyId(Long strategyId);
}
