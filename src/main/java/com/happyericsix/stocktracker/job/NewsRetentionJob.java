package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.service.NewsRetention;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 资讯时效清理：每晚按保留期物理删除过期行（"踢掉"的那道闸）。
 *
 * <h3>为什么物理删而不是只做查询过滤</h3>
 * 过滤救得了一屏救不了库：news_events 会随全市场抓取无限增长，
 * 没有清理任务的库只会越来越大、越来越慢。展示窗口（大盘 3 天 /
 * 个股 30 天）是**读侧**的第一道闸，这里是**存储侧**的第二道。
 *
 * <h3>删除顺序</h3>
 * ① 披露类（公告 1 / 研报 3）按 disclosureDays 删 —— 信源级别优先；
 * ② 其余按分类各自的天数删（分类删除排除 1/3，避免把 180 天的公告
 *   被"公司动态 30 天"的规则误删 —— 顺序换成先分类后信源就会发生）。
 */
@Component
public class NewsRetentionJob {

    private static final Logger log = LoggerFactory.getLogger(NewsRetentionJob.class);
    /** 公告 / 研报的信源级别（与 news_client 的 LEVEL_* 一致） */
    private static final List<Integer> DISCLOSURE_LEVELS = List.of(1, 3);

    private final NewsEventRepository newsEventRepository;
    private final NewsRetention retention;

    public NewsRetentionJob(NewsEventRepository newsEventRepository, NewsRetention retention) {
        this.newsEventRepository = newsEventRepository;
        this.retention = retention;
    }

    /** 03:40：避开整点（那是各种日报任务的高峰），且远离开收盘。 */
    @Scheduled(cron = "0 40 3 * * *", zone = "Asia/Shanghai")
    public void purgeExpired() {
        try {
            LocalDateTime now = LocalDateTime.now();
            long removed = newsEventRepository
                    .deleteBySourceLevelInAndPublishedAtBefore(DISCLOSURE_LEVELS,
                            now.minusDays(retention.disclosureDays()));
            for (var entry : retention.categoryDays().entrySet()) {
                removed += newsEventRepository
                        .deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
                                entry.getKey(), DISCLOSURE_LEVELS,
                                now.minusDays(entry.getValue()));
            }
            // 未分类的存量行（回填失败/表外值）：按默认期删，同样不碰披露类
            removed += newsEventRepository
                    .deleteByCategoryIsNullAndSourceLevelNotInAndPublishedAtBefore(
                            DISCLOSURE_LEVELS, now.minusDays(retention.defaultDays()));
            log.info("资讯时效清理完成：删除过期条目 {} 条", removed);
        } catch (Exception e) {
            // 清理失败只是这一天库没瘦，绝不能因此中断调度线程
            log.error("资讯时效清理失败：{}", e.getMessage(), e);
        }
    }
}
