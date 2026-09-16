package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 语义事实（L2）—— "用户是谁、偏好什么、持有什么、做了什么决定"。
 *
 * <h3>它和摘要（L1）的区别</h3>
 * 摘要是"这段时间聊了什么"（叙述性、有损），事实是"某一件事是什么"（原子、可被精确引用）。
 * "把止损改成 5%"这种请求必须靠事实层回答——摘要里那句"用户调整了止损"没法直接用来改策略。
 *
 * <h3>核心机制：改口 = 追加一条新事实，指向旧的（supersede 链）</h3>
 * 用户说"我改主意了，止损改成 5%"，我们<b>不修改</b>旧事实，而是：
 * <pre>
 *   fact#41  止损=8%   supersedesId=null      ← 已失效（被 #57 取代）
 *   fact#57  止损=5%   supersedesId=41        ← 当前有效
 * </pre>
 * 于是：
 * <ul>
 *   <li>"当前有效的事实" = <b>没有任何行指向它</b>的那些行（一次 NOT EXISTS 查询即可，不需要 UPDATE）；</li>
 *   <li>任意历史时刻的事实都能还原："2026-09-16 之前，止损是多少？"</li>
 *   <li>用户看到的解释是"止损 5%（2026-09-16 更新，此前为 8%）"，而不是一句没来由的 5%。</li>
 * </ul>
 * 这是把"只追加"落到时间有效性上的关键设计：失效是<b>被人取代</b>，不是被就地改写。
 *
 * <h3>三个时间（金融场景必须分开）</h3>
 * <ul>
 *   <li>{@code eventTime} 这句话指向的时间（"去年买的时候"）——回答"什么时候的事"；</li>
 *   <li>{@code validFrom} 该事实开始成立的时间（默认取 eventTime 或记录时间）；</li>
 *   <li>{@code recordedAt} 我们记下它的时间——回答"你什么时候告诉我的"。</li>
 * </ul>
 * 有效期的<b>结束</b>时间不落库，由取代它的那条事实推导（见上）。行情/财报这类会变的
 * 数据另外靠 {@code dataAsOf} 标注数据口径，避免把旧结论当此刻的事实。
 */
@Entity
@Table(name = "memory_facts", indexes = {
        @Index(name = "idx_memory_facts_user_key", columnList = "user_id,subject,predicate"),
        @Index(name = "idx_memory_facts_user_type", columnList = "user_id,fact_type"),
        @Index(name = "idx_memory_facts_supersedes", columnList = "supersedes_id")
})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class MemoryFact {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    /** 来源会话（便于回溯"这件事是哪天说的"） */
    @Column(name = "session_key", length = 64)
    private String sessionKey;

    /** 主体：user / 600519 / strategy:MA-cross */
    @Column(nullable = false, length = 128)
    private String subject;

    /**
     * 谓词：risk_preference / stop_loss_pct / holding_cost / watch_reason ...
     * (subject, predicate) 构成事实的"键"——同一个键上的新事实会取代旧的。
     */
    @Column(nullable = false, length = 128)
    private String predicate;

    /** 值（文本：稳健 / 5 / 1500 / 关注分红） */
    @Column(name = "fact_value", nullable = false, length = 512)
    private String factValue;

    /** preference / constraint / goal / holding / decision / observation */
    @Column(name = "fact_type", nullable = false, length = 32)
    private String factType;

    /** 0~1，抽取时的自信度；低置信度的事实可以只做参考而不进权威注入 */
    private Double confidence;

    /** 被取代的那条事实 id（形成链；null 表示它本身没有取代谁） */
    @Column(name = "supersedes_id")
    private Long supersedesId;

    /** 内容所指时间（"去年买的时候" → 2025-03） */
    @Column(name = "event_time")
    private LocalDateTime eventTime;

    /** 原始时间措辞，必须保留：将来要回答"我什么时候说的"就得靠它 */
    @Column(name = "raw_time_phrase", length = 64)
    private String rawTimePhrase;

    /** 事实开始成立的时间 */
    @Column(name = "valid_from")
    private LocalDateTime validFrom;

    /** 我们记下它的时间 */
    @Column(name = "recorded_at", nullable = false)
    private LocalDateTime recordedAt;

    /** 数据口径时间：行情/财报类事实必须标，否则旧结论会被当成此刻的事实 */
    @Column(name = "data_as_of")
    private LocalDateTime dataAsOf;

    @Column(name = "time_zone", length = 64)
    private String timeZone;

    /** user / model / tool / external */
    @Column(length = 16)
    private String provenance;

    /** high / medium / low */
    @Column(length = 16)
    private String trust;

    /**
     * 是否已被用户确认。
     * 模型推断出来但用户没确认过的东西（比如"用户可能偏好低波动"）一律 false，
     * 注入时要标注清楚，绝不能当成用户说过的话。
     */
    @Column(nullable = false)
    private Boolean confirmed;

    /** 来源账本事件 id，JSON 数组文本 */
    @Column(name = "source_event_ids", columnDefinition = "TEXT")
    private String sourceEventIds;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        LocalDateTime now = LocalDateTime.now();
        if (createdAt == null) {
            createdAt = now;
        }
        if (recordedAt == null) {
            recordedAt = now;
        }
        if (confirmed == null) {
            confirmed = false;
        }
        if (timeZone == null) {
            timeZone = "Asia/Shanghai";
        }
        if (validFrom == null) {
            validFrom = eventTime != null ? eventTime : recordedAt;
        }
    }

    /** 事实键：取代判定与去重都按它来 */
    @Transient
    public String factKey() {
        return (subject == null ? "" : subject.trim().toLowerCase()) + "|"
                + (predicate == null ? "" : predicate.trim().toLowerCase());
    }
}
