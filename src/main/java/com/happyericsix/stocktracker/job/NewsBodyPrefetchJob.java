package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.NewsService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 资讯正文后台预抓：把"近 14 天、有链接、还没正文"的条目慢慢补齐。
 *
 * <h3>为什么单独立一个 Job（而不是塞进资讯刷新）</h3>
 * 刷新链路负责"入库 + 解读"，走 LLM 是慢操作；正文抓取是纯 HTTP，节奏完全不同。
 * 分开后各自可调：抓取按 fixedDelay 节流（对信源友好），刷新仍按事件驱动。
 *
 * <h3>节流账</h3>
 * 每 30s 一次 × 每次 4 条 = 上限 8 篇/分钟。看似慢——500 条积压要一个多小时才能
 * 补完——但这是一次性的追赶，之后每天的新增只有几十条，预抓永远跑在前面。
 *
 * <h3>独立线程 + 重入保护（2026-10-13）</h3>
 * 本 Job 此前跑在 Spring 的**单线程调度器**上：委员会一次几分钟的日线结算
 * 占住调度器时，预抓被实测饿到 0.6 条/分钟（当日回放验证时暴露）。现在
 * @Async 到专用单线程池（newsPrefetchExecutor），长任务再占调度器也不影响
 * 30 秒一班照常发车。@Async 会让 fixedDelay 的"上一班跑完才计时"语义失效
 * （方法立即返回），所以用 AtomicBoolean 保证上一班没跑完时这一班直接放弃
 * —— 节流语义不因线程挪移而丢失（线程池的 DiscardPolicy 是同一层意思的双保险）。
 *
 * <h3>明确豁免 job_runs 心跳</h3>
 * 心跳纪律点名的是日级任务（见 JobRunService 的豁免名单）；本任务 30 秒一轮，
 * 逐轮写心跳一天近三千行纯属刷表。积压有没有在消化看日志 debug 行即可。
 */
@Component
public class NewsBodyPrefetchJob {

    private static final Logger log = LoggerFactory.getLogger(NewsBodyPrefetchJob.class);

    private final NewsService newsService;
    private final AtomicBoolean running = new AtomicBoolean(false);

    public NewsBodyPrefetchJob(NewsService newsService) {
        this.newsService = newsService;
    }

    @Async("newsPrefetchExecutor")
    @Scheduled(fixedDelay = 30_000, initialDelay = 60_000)
    public void prefetch() {
        if (!running.compareAndSet(false, true)) {
            return;   // 上一班还在跑：这一班放弃，不排队堆任务
        }
        try {
            int fetched = newsService.prefetchBodies();
            if (fetched > 0) {
                log.debug("正文预抓：本批 {} 条", fetched);
            }
        } catch (Exception e) {
            // 预抓是增强功能：任何失败只记日志，绝不影响其他链路
            log.warn("正文预抓任务异常：{}", e.getMessage());
        } finally {
            running.set(false);
        }
    }
}
