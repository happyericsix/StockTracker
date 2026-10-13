package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.NewsService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.Set;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 资讯采集定时任务（仿新闻 App 的后台采集器）：抓取与"用户是否打开页面"解耦 ——
 * 此前快讯只在有人逛雷达页时才进来，休市没人访问，库就是静止的。
 *
 * <h3>两条节拍，两种数据形态</h3>
 * <ul>
 *   <li><b>快讯（wire）</b>：7×24 滚动。交易时段 5 分钟一趟，休市/周末/夜间 30 分钟一趟 ——
 *       这就是"休市的时候资讯也在进来"的那条管道：宏观、海外、突发事件不挑交易日。</li>
 *   <li><b>全量趟（公告 + 舆情）</b>：交易日 15:40 / 19:00 / 21:00 + 每日 08:00，
 *       复用手动刷新的 {@link NewsService#refresh} 链路。公告上游只存当日（news_client
 *       的实测结论），盘后多跑几趟才接得住晚间披露；08:00 那趟兜住隔夜与清晨
 *       （周六跑是为周五深夜披露，周日上游必然为空、不跑）。研报没有全市场接口
 *       （按股查询），维持个股页 lazy enrich，不硬做。</li>
 * </ul>
 *
 * <h3>为什么快讯用 fixedDelay + 相位门控，而不是两段 cron</h3>
 * 节假日落在周一到周五时 cron 照样触发，而 {@link MarketStatusService} 的交易日历
 * 知道今天是不是真休市 —— 与 StockPriceRefreshJob 同一套哲学：日历说了算；
 * 日历拿不到（unknown）时按快节拍跑（宁可多拉：去重兜底，多一趟无害；少一趟漏新闻）。
 *
 * <h3>口径</h3>
 * 心跳走 job_runs（news-wire / news-refresh），server-monitor 红黄灯直接可见；
 * 静默跳过不是执行、不写心跳。上游失败由 refreshWire/refresh 内部降级成中文
 * 摘要返回，心跳层是双保险 —— 失败趟在监控里是红的，用户侧仍是"看库里旧的"。
 */
@Component
public class NewsIngestJob {

    private static final Logger log = LoggerFactory.getLogger(NewsIngestJob.class);

    /** 与 StockPriceRefreshJob 一致的有行情阶段；午间不休 —— 快讯中午照样发。 */
    private static final Set<String> FAST_PHASES = Set.of("auction", "morning", "noon_break", "afternoon");

    /** 休市节拍间隔：周末/夜间 30 分钟一趟。 */
    private static final long SLOW_INTERVAL_MS = 30 * 60_000L;

    /** 上一趟快讯的时间戳（毫秒）；0 = 进程内还没跑过（首趟必跑）。 */
    private final AtomicLong lastWireRunMs = new AtomicLong();

    private final NewsService newsService;
    private final MarketStatusService marketStatusService;
    private final JobRunService jobRunService;

    public NewsIngestJob(NewsService newsService, MarketStatusService marketStatusService,
                         JobRunService jobRunService) {
        this.newsService = newsService;
        this.marketStatusService = marketStatusService;
        this.jobRunService = jobRunService;
    }

    @Scheduled(fixedDelay = 5 * 60_000, initialDelay = 90_000)
    public void pullWire() {
        if (!wireDue()) {
            return;
        }
        lastWireRunMs.set(System.currentTimeMillis());
        jobRunService.runWithHeartbeat(JobRunService.JOB_NEWS_WIRE,
                () -> newsService.refreshWire());
    }

    /**
     * 交易时段每 5 分钟都到点；休市由"距上一趟的时长"节流成 30 分钟一趟。
     * 包级可见是给单测钉节拍语义（NewsIngestJobTest）。
     */
    boolean wireDue() {
        String phase;
        try {
            phase = marketStatusService.current().phase();
        } catch (Exception e) {
            phase = "unknown";
        }
        if ("unknown".equals(phase) || FAST_PHASES.contains(phase)) {
            return true;
        }
        return System.currentTimeMillis() - lastWireRunMs.get() >= SLOW_INTERVAL_MS;
    }

    @Scheduled(cron = "0 40 15 * * MON-FRI", zone = "Asia/Shanghai")
    public void pullAfterClose() {
        ingestFull();
    }

    @Scheduled(cron = "0 0 19,21 * * MON-FRI", zone = "Asia/Shanghai")
    public void pullEvening() {
        ingestFull();
    }

    @Scheduled(cron = "0 0 8 * * MON-SAT", zone = "Asia/Shanghai")
    public void pullMorning() {
        ingestFull();
    }

    private void ingestFull() {
        log.debug("资讯全量趟开始（公告 + 舆情，复用手动刷新链路）");
        jobRunService.runWithHeartbeat(JobRunService.JOB_NEWS_REFRESH,
                () -> newsService.refresh(null));
    }
}
