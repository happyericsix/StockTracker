package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.JobRun;
import com.happyericsix.stocktracker.repository.JobRunRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Propagation;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 任务心跳：关键定时任务跑完写一行，回答"这个环节今天到底跑没跑、成没成"。
 *
 * <h3>为什么需要它（2026-10-09 事故）</h3>
 * 那天三个容器全程 running、首页全程 200，巡检脚本一路绿着 —— 但日线结算
 * 失败了一整天，直到用户拿错误的日报截图来问才发现。
 * "进程活着"和"任务跑成了"是两件事，后者只能由任务自己申报。
 *
 * <h3>纪律：心跳是旁路，绝不反噬任务本身</h3>
 * {@link #record} 内部吞掉一切异常（写库失败只记日志）—— 心跳挂了不该把
 * 结算/抓取拖死，这与报告推送（MessageService.saveReport 的 REQUIRES_NEW）
 * 是同一条纪律：增强件的失败由增强件自己承担。
 * REQUIRES_NEW 的理由也一样：调用方（job）不在事务里，但万一哪天有人在
 * 事务内调它，一行心跳也不该被别人的回滚带走。
 *
 * <h3>豁免名单（明规则）</h3>
 * 心跳点名的是"一天跑几轮"的任务；秒级常驻轮询不在此列：
 * StockPriceRefreshJob（交易时段 15 秒）与 NewsBodyPrefetchJob（30 秒）
 * 明确豁免 —— 逐轮写心跳一天几千行，除了刷爆 job_runs 没有别的作用；
 * 它们"跑没跑"由下游可观察（SSE 推价 / 预警评估 / 正文回填进度）。
 *
 * <h3>消费方</h3>
 * 心跳行写给 job_runs 表；**本地监控（server-monitor）直接读表做红黄灯**。
 * 写入侧刻意不依赖任何展示逻辑 —— 展示形态变了，点名的纪律不变。
 */
@Service
public class JobRunService {

    private static final Logger log = LoggerFactory.getLogger(JobRunService.class);

    public static final String STATUS_OK = "ok";
    public static final String STATUS_FAILED = "failed";

    // ==================== 任务键（封闭集） ====================

    /** 日线结算（15:30）：最关键的一格 —— 委员会、净值、授权都挂在它后面。 */
    public static final String JOB_PAPER_DAILY = "paper-daily";
    /** 结算重试（16:10 / 17:10）：17:10 那轮是当天最后一次机会。 */
    public static final String JOB_PAPER_RETRY = "paper-retry";
    /** 周复盘（周五 16:30）：样本外验证 + 空转摘要。 */
    public static final String JOB_PAPER_WEEKLY = "paper-weekly";
    /** 事件线专题（交易时段每小时 :20）。 */
    public static final String JOB_NEWS_TOPICS = "news-topics";
    /** 新闻抓取（事件驱动入口保留）：资讯页手动刷新 + 定时全量趟（交易日 15:40/19:00/21:00 + 每日 08:00）。 */
    public static final String JOB_NEWS_REFRESH = "news-refresh";
    /** 快讯采集（7×24：交易时段 5 分钟、休市 30 分钟）：休市期间资讯也在进来的那条管道。 */
    public static final String JOB_NEWS_WIRE = "news-wire";
    /** 情绪快照（交易日 16:10）。 */
    public static final String JOB_SENTIMENT = "sentiment-snapshot";
    /** 资讯保留清理（每日 03:40）—— 它同时负责 90 天前心跳行的瘦身。 */
    public static final String JOB_NEWS_RETENTION = "news-retention";

    private final JobRunRepository repository;

    public JobRunService(JobRunRepository repository) {
        this.repository = repository;
    }

    /**
     * 写一行心跳（旁路安全）。同名任务一天多行是正常的（重试两次、专题 8 次）——
     * 心跳是点名历史不是状态机，消费侧永远取最新一行。
     */
    @Transactional(propagation = Propagation.REQUIRES_NEW)
    public void record(String jobName, String status, String detail) {
        try {
            repository.save(JobRun.builder()
                    .jobName(jobName)
                    .runDate(LocalDate.now(CnTime.ZONE))
                    .status(STATUS_OK.equals(status) ? STATUS_OK : STATUS_FAILED)
                    .detail(clip(detail))
                    .finishedAt(LocalDateTime.now(CnTime.ZONE))
                    .build());
        } catch (Exception e) {
            log.warn("job 心跳写入失败（不影响任务本身）：{} {}", jobName, e.getMessage());
        }
    }

    /** 带心跳的任务体：返回一句话摘要（null 也行）。 */
    @FunctionalInterface
    public interface Heartbeated {

        String run() throws Exception;
    }

    /**
     * 统一的任务包装：成功落 ok 心跳，异常落 failed 心跳并**在这里终结** ——
     * 定时任务绝不让异常冒到调度线程（冒出去也只是被调度器吞掉），
     * 这层 try/catch 同时承担了各 Job 原有的兜底职责，Job 体里不用再写一遍。
     */
    public void runWithHeartbeat(String jobName, Heartbeated action) {
        try {
            record(jobName, STATUS_OK, action.run());
        } catch (Exception e) {
            log.error("job {} 失败：{}", jobName, e.getMessage(), e);
            record(jobName, STATUS_FAILED, e.toString());
        }
    }

    private static String clip(String detail) {
        if (detail == null) {
            return null;
        }
        return detail.length() <= 500 ? detail : detail.substring(0, 497) + "...";
    }
}
