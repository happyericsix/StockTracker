package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.StockService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.ApplicationEventPublisher;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.Instant;
import java.util.List;
import java.util.Set;

/**
 * 价格刷新定时任务（快车道，2026-10-08 起交易时段默认 15 秒一次，可配）
 *
 * 流程：
 *  1. 拉所有自选股的价 + 指标 + 买入价 → List<RefreshedPrice>
 *  2. 发 PricesRefreshedEvent
 *  3. 两个订阅者各自消化：AlertEvaluationListener 评估预警（每次都跑，预警自带冷却）；
 *     PaperTradingListener 模拟盘盘中结算（自带 45s 节流 —— 它吃 1 分钟 K 线，
 *     15s 一评没有意义，见其注释）；QuoteStreamListener 把最新价 SSE 推给前端。
 *
 * 这个 Job 只管"刷新数据并广播"，不直接知道有谁在听。
 * 异常隔离：拉价失败 / 单只股票挂掉由 StockService 内部处理，整体 publish 失败由外层 try 兜底
 *
 * <h3>只在有行情的时段刷新（2026-10-07）</h3>
 * 收盘后/休市日价格不动，继续刷新只会把同一份旧价反复推给下游 ——
 * 国庆实测：休市日照样每 5 分钟空转，模拟盘盘中评估一天留下 186 条
 * "等待授权"痕迹；预警更会拿旧收盘价按冷却节奏反复触发。
 * 这里拦一道，下游几个事件订阅者同时安静。
 *
 * <h3>明确豁免 job_runs 心跳</h3>
 * 心跳纪律点名的是日级任务（见 JobRunService 的豁免名单）；本任务交易时段
 * 15 秒一轮，逐轮写心跳一天几千行纯属刷表。跑没跑由下游可观察：
 * SSE 有没有推价、预警有没有评估。
 */
@Component
public class StockPriceRefreshJob {

    private static final Logger log = LoggerFactory.getLogger(StockPriceRefreshJob.class);

    /** 有行情的阶段：集合竞价 + 上午 + 午间（午间价格不动，但保留刷新以便跨段衔接）。 */
    private static final Set<String> ACTIVE_PHASES = Set.of("auction", "morning", "noon_break", "afternoon");

    private final StockService stockService;
    private final ApplicationEventPublisher eventPublisher;
    private final MarketStatusService marketStatusService;

    public StockPriceRefreshJob(StockService stockService, ApplicationEventPublisher eventPublisher,
                                MarketStatusService marketStatusService) {
        this.stockService = stockService;
        this.eventPublisher = eventPublisher;
        this.marketStatusService = marketStatusService;
    }

    /**
     * 快车道：交易时段 15 秒一次（原来 5 分钟）。免费快照行情本身约 3 秒一跳，
     * 15 秒的轮询间隔已经贴近快照节奏；再快只是把同一份价多拉几遍。
     * 间隔走配置（stock.refresh-interval-ms）：自选股多了觉得压力大就调大，不用改代码。
     */
    @Scheduled(fixedRateString = "${stock.refresh-interval-ms:15000}", initialDelay = 30_000)
    public void refreshFavoritePrices() {
        // 日历**明确**说不在有行情的阶段（收盘后/休市日/未开盘）就不刷。
        // unknown（日历不可用）时照旧刷：宁可对旧价空转，也不能因为拿不到日历
        // 就把交易时段的刷新一起停了 —— 那是把"不确定"当"休市"，方向反了。
        String phase;
        try {
            phase = marketStatusService.current().phase();
        } catch (Exception e) {
            phase = "unknown";
        }
        if (!"unknown".equals(phase) && !ACTIVE_PHASES.contains(phase)) {
            log.info("Skip price refresh: market phase={}", phase);
            return;
        }
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
