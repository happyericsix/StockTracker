package com.happyericsix.stocktracker.event;

import com.happyericsix.stocktracker.dto.RefreshedPrice;

import java.time.Instant;
import java.util.List;

/**
 * 价格刷新完成事件：
 *  - StockPriceRefreshJob 每 5 分钟拉完价后发布
 *  - AlertEvaluationListener 监听并对所有 enabled Alert 评估
 *
 * 为什么不直接耦合：Job 不该知道有 Alert 存在，加新消费者（metrics / webhook）只加 Listener 即可
 */
public record PricesRefreshedEvent(
        List<RefreshedPrice> prices,
        Instant refreshedAt
) {}
