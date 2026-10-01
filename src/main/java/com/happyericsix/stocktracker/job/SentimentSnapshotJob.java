package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.MarketSentimentService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 散户情绪每日快照：收盘后（16:10）把"自选 ∪ 策略"标的的当日情绪落一行。
 *
 * <p>为什么必须每天存（而不是要用时现拉）：参与意愿接口只回最近 5 天，
 * 它的对照验证需要 20+ 个交易日的序列——序列只能自己攒。
 * 节假日跑也只是用同一交易日覆盖同一行（幂等键 = 上游给的最后交易日），
 * 所以 cron 不做交易日判断，靠幂等兜底，少一个对日历的依赖。
 */
@Component
public class SentimentSnapshotJob {

    private static final Logger log = LoggerFactory.getLogger(SentimentSnapshotJob.class);

    private final MarketSentimentService marketSentimentService;

    public SentimentSnapshotJob(MarketSentimentService marketSentimentService) {
        this.marketSentimentService = marketSentimentService;
    }

    @Scheduled(cron = "0 10 16 * * MON-FRI", zone = "Asia/Shanghai")
    public void snapshotDaily() {
        try {
            int saved = marketSentimentService.snapshotAll();
            log.info("SentimentSnapshotJob done: {} snapshots", saved);
        } catch (Exception e) {
            log.error("SentimentSnapshotJob failed", e);
        }
    }
}
