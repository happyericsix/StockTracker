package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.StrategyHealthEvent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDateTime;
import java.util.List;

public interface StrategyHealthEventRepository extends JpaRepository<StrategyHealthEvent, Long> {

    /** 冷却判据：该策略该类型最近的若干条事件（含未推送的——冷却按"触发"算，不按"推送"算）。 */
    /** 模拟盘页"最近哨兵观察"：每策略取最近几条（页面展示用，与冷却判定无关）。 */
    List<StrategyHealthEvent> findTop5ByStrategyIdOrderByCreatedAtDesc(Long strategyId);

    List<StrategyHealthEvent> findTop3ByStrategyIdAndEventTypeOrderByCreatedAtDesc(
            Long strategyId, String eventType);
}
