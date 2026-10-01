package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MarketSentimentSnapshot;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

@Repository
public interface MarketSentimentSnapshotRepository extends JpaRepository<MarketSentimentSnapshot, Long> {

    Optional<MarketSentimentSnapshot> findBySymbolAndTradeDate(String symbol, LocalDate tradeDate);

    List<MarketSentimentSnapshot> findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
            String symbol, LocalDate since);
}
