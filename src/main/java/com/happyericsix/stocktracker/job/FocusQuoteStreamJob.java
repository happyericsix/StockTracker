package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.listener.QuoteStreamListener;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.SseEmitterService;
import com.happyericsix.stocktracker.service.StockService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Set;

/**
 * 焦点票快车道（2026-10-15）：图表页正在看的票，交易时段 3 秒一发直连源新价。
 *
 * <h3>为什么与 {@link StockPriceRefreshJob} 分开</h3>
 * 那个是"所有自选 + 焦点并批，15 秒一轮，走缓存"；这个只刷 SSE 订阅者正在盯的
 * 个位数票，@CachePut 直通 Python/腾讯源。混在同一个 Job 里，要么把自选批也拖成
 * 3 秒一轮（免费源扛不住 24 票 × 20 次/分），要么让快车道跟着 15 秒的批走（那就不快了）。
 *
 * <h3>为什么直推 SSE 而不发 PricesRefreshedEvent</h3>
 * 事件还有另外两个订阅者：预警评估（自带冷却，但 3 秒一评纯属空转）和模拟盘盘中
 * 结算（45s 节流）。行情快照流只需要"推给前端"这一个动作，直接走
 * {@link SseEmitterService#pushQuotes}，不惊动任何人。
 *
 * <h3>节奏与源的物理极限对齐</h3>
 * 免费快照（腾讯 qt.gtimg.cn）本身约 3 秒一跳，间隔 configurable
 * （stock.focus-refresh-interval-ms）——想要 1 秒请先换付费逐笔源，否则只是重复拉同一份价。
 */
@Component
public class FocusQuoteStreamJob {

    private static final Logger log = LoggerFactory.getLogger(FocusQuoteStreamJob.class);

    /** 与 StockPriceRefreshJob 同一张相位表：有行情的时段才跑（午间价格不动但保留，代价可忽略）。 */
    private static final Set<String> ACTIVE_PHASES = Set.of("auction", "morning", "noon_break", "afternoon");

    private final StockService stockService;
    private final SseEmitterService sseEmitterService;
    private final MarketStatusService marketStatusService;

    public FocusQuoteStreamJob(StockService stockService,
                               SseEmitterService sseEmitterService,
                               MarketStatusService marketStatusService) {
        this.stockService = stockService;
        this.sseEmitterService = sseEmitterService;
        this.marketStatusService = marketStatusService;
    }

    @Scheduled(fixedRateString = "${stock.focus-refresh-interval-ms:3000}", initialDelay = 45_000)
    public void pushFocusQuotes() {
        // 相位门与 StockPriceRefreshJob 同一条纪律：日历明确说没行情就不刷；
        // unknown（日历不可用）照跑——不能把"不确定"当"休市"。
        String phase;
        try {
            phase = marketStatusService.current().phase();
        } catch (Exception e) {
            phase = "unknown";
        }
        if (!"unknown".equals(phase) && !ACTIVE_PHASES.contains(phase)) {
            return;   // 盘后/休市：静默跳过（15s 那个 Job 会打日志，这里每 3 秒一条会刷屏）
        }
        try {
            List<RefreshedPrice> prices = stockService.refreshFocusQuotes();
            if (prices.isEmpty()) return;
            sseEmitterService.pushQuotes(QuoteStreamListener.toJson(prices));
            log.debug("FocusQuoteStreamJob: pushed {} focus quotes", prices.size());
        } catch (Exception e) {
            // 整体兜底：快车道挂了不影响任何主流程，下一轮 3 秒后再来
            log.warn("FocusQuoteStreamJob failed: {}", e.getMessage());
        }
    }
}
