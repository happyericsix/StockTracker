package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.StockService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;

/**
 * 价格刷新定时任务：每 5 分钟一次
 *
 * 流程：
 *  1. 拉所有自选股的价 + 指标 + 买入价 → List<RefreshedPrice>
 *  2. 发 PricesRefreshedEvent
 *  3. AlertEvaluationListener 监听事件，自动对所有 enabled 预警跑评估
 *
 * 这个 Job 只管"刷新数据并广播"，不直接知道有 Alert 存在
 * 异常隔离：拉价失败 / 单只股票挂掉由 StockService 内部处理，整体 publish 失败由外层 try 兜底
 */
@Component
public class StockPriceRefreshJob {

    private static final Logger log = LoggerFactory.getLogger(StockPriceRefreshJob.class);

    private final StockService stockService;
    private final ApplicationEventPublisher eventPublisher;

    public StockPriceRefreshJob(StockService stockService, ApplicationEventPublisher eventPublisher) {
        this.stockService = stockService;
        this.eventPublisher = eventPublisher;
    }

    @Scheduled(fixedRate = 300_000, initialDelay = 30_000)
    public void refreshFavoritePrices() {
        long t0 = System.currentTimeMillis();
        try {
            List<RefreshedPrice> prices = stockService.refreshFavorites();
            eventPublisher.publishEvent(new PricesRefreshedEvent(prices, Instant.now()));
            long cost = System.currentTimeMillis() - t0;
            log.info("StockPriceRefreshJob: refreshed {} symbols, cost={}ms", prices.size(), cost);
        } catch (Exception e) {
            // 整体兜底：单次刷新全挂不能让定时任务挂掉，否则后续刷新全部停摆
            log.error("StockPriceRefreshJob failed: {}", e.getMessage(), e);
        }
    }
}
