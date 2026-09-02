package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.FavoriteStock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface FavoriteStockRepository extends JpaRepository<FavoriteStock, Long> {

    void deleteByStockSymbol(String stockSymbol);

    boolean existsByStockSymbol(String stockSymbol);
    List<FavoriteStock> findByUserId(Long userId);
    void deleteByStockSymbolAndUserId(String stockSymbol, Long userId);
    boolean existsByStockSymbolAndUserId(String stockSymbol, Long userId);

    /** 用于 PnlPercentEvaluator 取某用户对某股票的买入价 */
    List<FavoriteStock> findByUserIdAndStockSymbol(Long userId, String stockSymbol);

}
