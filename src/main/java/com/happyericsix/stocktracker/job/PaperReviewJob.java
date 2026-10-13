package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 周频复盘：**样本外验证 + 空转期摘要**。
 *
 * <h3>为什么与 {@link PaperTradingJob} 分开</h3>
 * 那个是"每天结算"（15:30，与收盘对齐），这个是"每周看一次规则还成不成立"。
 * 两者的失败后果也不同：结算失败＝丢一天的数据，复盘失败＝少一条消息。
 * 放在一个类里，读的人会以为它们是同一件事。
 *
 * <h3>为什么排在周五 16:30</h3>
 * ① 在当日结算（15:30）之后：这周最后一天的结果已经落库，摘要不会漏掉它；
 * ② 收盘后：验证要取行情，盘中取到的是"今天的半根 K 线"；
 * ③ 周五而不是周末：周一开盘前用户有整块时间看它。
 */
@Component
public class PaperReviewJob {

    private static final Logger log = LoggerFactory.getLogger(PaperReviewJob.class);
    private final PaperTradingService paperTradingService;
    private final MarketStatusService marketStatusService;
    private final JobRunService jobRunService;

    public PaperReviewJob(PaperTradingService paperTradingService,
                          MarketStatusService marketStatusService,
                          JobRunService jobRunService) {
        this.paperTradingService = paperTradingService;
        this.marketStatusService = marketStatusService;
        this.jobRunService = jobRunService;
    }

    @Scheduled(cron = "0 30 16 * * FRI", zone = "Asia/Shanghai")
    public void runWeeklyReview() {
        // 节假日周五（如国庆 10-02）没有新结算，验证与摘要都是对旧数据复读。
        // 与 PaperTradingJob 同一条纪律：只有日历明确说非交易日才跳过。
        jobRunService.runWithHeartbeat(JobRunService.JOB_PAPER_WEEKLY, () -> {
            try {
                if (Boolean.FALSE.equals(marketStatusService.current().tradingDay())) {
                    log.info("Skip weekly review: not a trading day");
                    return "非交易日，跳过";
                }
            } catch (Exception e) {
                log.warn("Market status unavailable, run review anyway: {}", e.toString());
            }
            paperTradingService.runWeeklyReview();
            return null;
        });
    }
}
