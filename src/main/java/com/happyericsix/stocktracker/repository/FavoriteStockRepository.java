package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.FavoriteStock;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

@Repository
public interface FavoriteStockRepository extends JpaRepository<FavoriteStock, Long> {

    // 已删除未按用户隔离的旧版本 deleteByStockSymbol(String) / existsByStockSymbol(String)。
    // 它们只看股票代码、不看 userId，任何调用都会跨用户命中别人的自选股 ——
    // 已被下面的 ...AndUserId 版本取代，勿再加回。

    List<FavoriteStock> findByUserId(Long userId);
    void deleteByStockSymbolAndUserId(String stockSymbol, Long userId);
    boolean existsByStockSymbolAndUserId(String stockSymbol, Long userId);

    /** 用于 PnlPercentEvaluator 取某用户对某股票的买入价 */
    List<FavoriteStock> findByUserIdAndStockSymbol(Long userId, String stockSymbol);

}
