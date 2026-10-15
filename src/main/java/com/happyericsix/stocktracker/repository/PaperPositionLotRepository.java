package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperPositionLot;
import org.springframework.data.jpa.repository.JpaRepository;

import java.math.BigDecimal;
import java.util.List;

public interface PaperPositionLotRepository extends JpaRepository<PaperPositionLot, Long> {

    /** 某策略全部未平批次，按开仓先后（先进先出的消耗顺序；T+1 解锁按 openedDate 在内存过滤）。 */
    List<PaperPositionLot> findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
            Long strategyId, BigDecimal zero);
}
