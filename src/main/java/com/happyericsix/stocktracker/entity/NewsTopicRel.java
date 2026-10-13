package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 事件线成员关联：news_topics ↔ news_events 的多对多。
 *
 * <p>与 {@link NewsStockRel} 同一模式：裸 Long 外键（不加 JPA 关联 ——
 * 关联对象会把"读一条专题"变成"联表拉全部成员正文"，这里只需要 id 对账）。
 * {@code eventId} 上有索引：聚类匹配按成员 id 反查已有专题，走的就是它。
 */
@Entity
@Table(name = "news_topic_rel",
        uniqueConstraints = @UniqueConstraint(name = "uk_news_topic_rel",
                columnNames = {"topic_id", "event_id"}),
        indexes = {
                @Index(name = "idx_news_topic_rel_event", columnList = "event_id"),
                @Index(name = "idx_news_topic_rel_topic", columnList = "topic_id")
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class NewsTopicRel {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "topic_id", nullable = false)
    private Long topicId;

    @Column(name = "event_id", nullable = false)
    private Long eventId;

    /** 落库时间（对账用：这条成员关系是哪一轮聚类加进来的） */
    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @jakarta.persistence.PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
    }
}
