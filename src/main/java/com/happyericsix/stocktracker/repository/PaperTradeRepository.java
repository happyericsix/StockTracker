package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTrade;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;

@Repository
public interface PaperTradeRepository extends JpaRepository<PaperTrade, Long> {
    List<PaperTrade> findByStrategyIdOrderByTradeDateDesc(Long strategyId);
    boolean existsByStrategyIdAndTradeDate(Long strategyId, LocalDate tradeDate);
}
