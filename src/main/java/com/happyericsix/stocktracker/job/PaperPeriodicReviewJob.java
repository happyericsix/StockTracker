package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.PaperPeriodicReviewService;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 月度/季度复盘（2026-10-15）：月度小结 + 季度策略信的定时入口。
 *
 * <h3>为什么排在周五 16:40（周复盘 16:30 之后）</h3>
 * 季度信引用"规则证据"那一行（样本外验证结论），先让周验证跑完再取数，
 * 读到的就是当天的新鲜结论。隔 10 分钟也避免两个任务在同一分钟抢线程池。
 *
 * <h3>为什么每周五都跑、而不是"每月第一个周五"</h3>
 * 服务层按幂等键（periodic:monthly:{sid}:2026-09）判断"上个月发过没有"：
 * 发过就静默返回。把"第一个"交给幂等而不是日历 —— 国庆那种"月首周五是假日"
 * 的场景不需要任何补跑逻辑，假日跳过后下一个周五自然补上。
 */
@Component
public class PaperPeriodicReviewJob {

    private static final Logger log = LoggerFactory.getLogger(PaperPeriodicReviewJob.class);

    private final PaperPeriodicReviewService paperPeriodicReviewService;
    private final MarketStatusService marketStatusService;
    private final JobRunService jobRunService;

    public PaperPeriodicReviewJob(PaperPeriodicReviewService paperPeriodicReviewService,
                                  MarketStatusService marketStatusService,
                                  JobRunService jobRunService) {
        this.paperPeriodicReviewService = paperPeriodicReviewService;
        this.marketStatusService = marketStatusService;
        this.jobRunService = jobRunService;
    }

    @Scheduled(cron = "0 40 16 * * FRI", zone = "Asia/Shanghai")
    public void runPeriodicReports() {
        // 与 PaperReviewJob 同一条纪律：只有日历明确说非交易日才跳过（unknown 照跑），
        // 让幂等键承担"哪个月该发"的判定。
        jobRunService.runWithHeartbeat(JobRunService.JOB_PAPER_PERIODIC, () -> {
            try {
                if (Boolean.FALSE.equals(marketStatusService.current().tradingDay())) {
                    log.info("Skip periodic review: not a trading day");
                    return "非交易日，跳过";
                }
            } catch (Exception e) {
                log.warn("Market status unavailable, run periodic review anyway: {}", e.toString());
            }
            paperPeriodicReviewService.runPeriodicReports(CnTime.today());
            return null;
        });
    }
}
