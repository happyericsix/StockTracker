package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.NewsTopic;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;

@Repository
public interface NewsTopicRepository extends JpaRepository<NewsTopic, Long> {

    /** 雷达页列表：近 N 天有活动的事件线，最新活动在前。symbols 过滤在 Java 侧做（JSON 数组包含）。 */
    @Query("""
            SELECT t FROM NewsTopic t
             WHERE t.lastEventAt IS NOT NULL AND t.lastEventAt >= :since
             ORDER BY t.lastEventAt DESC
            """)
    List<NewsTopic> findActiveSince(@Param("since") LocalDateTime since, Pageable pageable);

    /**
     * 每日撰稿预算的已用量：当天撰稿成功的专题数（synthesized_at 落库即记账，
     * 跨轮次/跨重启都不丢 —— 预算纪律与模拟盘 metering 同一哲学）。
     */
    long countBySynthesizedAtGreaterThanEqual(LocalDateTime since);

    /** 静默超时关线：一次性把超过静默窗口没动静的 open 线置为 closed。 */
    @Modifying
    @Query("UPDATE NewsTopic t SET t.status = :closed, t.updatedAt = CURRENT_TIMESTAMP"
            + " WHERE t.status = :open AND t.lastEventAt < :idleBefore")
    int closeIdleTopics(@Param("closed") String closed, @Param("open") String open,
                        @Param("idleBefore") LocalDateTime idleBefore);
}
