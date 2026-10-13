package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.NewsTopicService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 事件线（新闻专题）定时任务：交易日 9:20~16:20 每小时一轮
 * "聚类 → 落库 → 总编 → 撰稿"。
 *
 * <h3>为什么这个节奏</h3>
 * 新闻综合不需要分钟级响应（与盯盘 agent 的事件驱动是两种形态），
 * 定时批量更稳也更省：每轮 1 次总编调用 + 少量撰稿调用，
 * 全天 ≤ {@code news.editor.daily-budget} 次 LLM。16:20 那轮是"收盘复盘"，
 * 把盘后的公告与收盘报道收进当日事件线。
 *
 * <h3>为什么不在周末跑</h3>
 * 周末没有新资讯，聚类是确定性纯函数 —— 上一轮的结果不会变，
 * 跑了也只是白付一次聚类请求（撰稿有冷却与"无新成员不写"的守卫）。
 */
@Component
public class NewsTopicJob {

    private static final Logger log = LoggerFactory.getLogger(NewsTopicJob.class);

    private final NewsTopicService newsTopicService;
    private final JobRunService jobRunService;

    public NewsTopicJob(NewsTopicService newsTopicService, JobRunService jobRunService) {
        this.newsTopicService = newsTopicService;
        this.jobRunService = jobRunService;
    }

    @Scheduled(cron = "0 20 9-16 * * MON-FRI", zone = "Asia/Shanghai")
    public void buildTopics() {
        // runOnce 内部已兜底所有异常（软失败以摘要文本返回），心跳层是双保险
        jobRunService.runWithHeartbeat(JobRunService.JOB_NEWS_TOPICS,
                () -> newsTopicService.runOnce());
    }
}
