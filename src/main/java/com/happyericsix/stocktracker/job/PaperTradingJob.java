package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class PaperTradingJob {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingJob.class);
    private final PaperTradingService paperTradingService;
    private final MarketStatusService marketStatusService;
    private final JobRunService jobRunService;

    public PaperTradingJob(PaperTradingService paperTradingService,
                           MarketStatusService marketStatusService,
                           JobRunService jobRunService) {
        this.paperTradingService = paperTradingService;
        this.marketStatusService = marketStatusService;
        this.jobRunService = jobRunService;
    }

    @Scheduled(cron = "0 30 15 * * MON-FRI", zone = "Asia/Shanghai")
    public void runDailyPaper() {
        // 法定节假日落在周一到周五时 cron 照样触发：没有 K 线，结算全是 no_bar，
        // 还会发一条"今天什么都没发生"的复盘消息（国庆实测：休市 7 天发了 2 条纯噪音）。
        // 只有日历**明确**说非交易日（tradingDay=false）才跳过；unknown/null 照跑 ——
        // 宁可空跑一次，不可漏一次真结算。跳过也要留心跳（状态页据此不误报黄灯）。
        jobRunService.runWithHeartbeat(JobRunService.JOB_PAPER_DAILY, () -> {
            if (Boolean.FALSE.equals(marketStatusService.current().tradingDay())) {
                log.info("Skip daily paper settlement: not a trading day");
                return "非交易日，跳过";
            }
            paperTradingService.evaluateDaily();
            return null;
        });
    }

    /**
     * 结算重试（2026-10-08）：日线源在 15:30 当口常还没出当天的 bar
     * （实测腾讯源 10-08 的日线 17:05 才有），当天结算会以 no_bar 失败 ——
     * 不补跑的话这一天就永远缺失（曲线少一格、委员会没开会、授权没落库）。
     * 16:10 与 17:10 各补一次，覆盖慢发布的日子；只碰当天失败过的策略，
     * 成功过的绝不重跑（不花第二次委员会成本）。日历明确说非交易日时跳过，
     * 与主结算同一道闸。
     *
     * <p>16:10 是中间轮（bar 可能只是还没发布，报告继续等）；17:10 是终轮 ——
     * 到这一刻还是 no_bar 就是当天定论，推系统告警而不是复盘（同件事只说一遍）。
     * 2026-10-09 事故：15:30 首轮就把中间态 no_bar 推成了"这一天没有 K 线"，
     * 而当天 bar 17:10 后才发布、重试已停，日报成了永久假话。
     */
    @Scheduled(cron = "0 10 16 * * MON-FRI", zone = "Asia/Shanghai")
    public void retryDailyPaperMid() {
        runRetry(false);
    }

    @Scheduled(cron = "0 10 17 * * MON-FRI", zone = "Asia/Shanghai")
    public void retryDailyPaperFinal() {
        runRetry(true);
    }

    private void runRetry(boolean finalAttemptOfDay) {
        jobRunService.runWithHeartbeat(JobRunService.JOB_PAPER_RETRY, () -> {
            if (Boolean.FALSE.equals(marketStatusService.current().tradingDay())) {
                log.info("Skip settlement retry: not a trading day");
                return "非交易日，跳过";
            }
            return paperTradingService.retryFailedDailySettlements(finalAttemptOfDay)
                    + (finalAttemptOfDay ? "（终轮）" : "（中间轮）");
        });
    }
}
