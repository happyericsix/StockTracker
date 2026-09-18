package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

@Repository
public interface PaperTradeTraceRepository extends JpaRepository<PaperTradeTrace, Long> {

    /** 去重键是唯一真相源：结算重跑与每 5 分钟的实时路径都靠它避免重复写行。 */
    Optional<PaperTradeTrace> findByDedupeKey(String dedupeKey);

    List<PaperTradeTrace> findByStrategyIdOrderByCreatedAtDesc(Long strategyId, Pageable pageable);

    List<PaperTradeTrace> findByStrategyIdAndTradeDateOrderByCreatedAtAsc(Long strategyId, LocalDate tradeDate);

    List<PaperTradeTrace> findByStrategyIdAndSkipReasonOrderByCreatedAtDesc(Long strategyId, String skipReason);

    long countByStrategyId(Long strategyId);
}
