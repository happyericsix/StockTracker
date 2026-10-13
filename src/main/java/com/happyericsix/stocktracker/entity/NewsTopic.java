package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.PrePersist;
import jakarta.persistence.PreUpdate;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 新闻事件线（2026-10-09 新闻雷达升级）：同一事件的多条报道聚成的一条"故事"。
 *
 * <h3>与 news_events 的关系</h3>
 * news_events 仍是全局事实表；本表是它的**组织层**（一个专题引用多条事件，
 * 经 {@link NewsTopicRel}）。聚类由 Python 的 N4 纯代码完成（确定性），
 * 撰稿由 N5 的总编/撰稿 LLM 完成 —— LLM 挂了专题照样存在（代码标题 + 成员列表），
 * 这是 fail-open：事件线本身没有 AI 也成立。
 *
 * <h3>标题的两种来源</h3>
 * {@code titleOrigin}: {@code code}（聚类代表条目的标题，公告腔）或
 * {@code editor}（总编 LLM 的读者视角标题）。upsert 只覆写 code 标题，
 * editor 标题由总编显式更新 —— 否则下一轮聚类会把总编起的名字冲掉。
 */
@Entity
@Table(name = "news_topics",
        indexes = {
                @Index(name = "idx_news_topics_last_event", columnList = "last_event_at"),
                @Index(name = "idx_news_topics_status_last", columnList = "status, last_event_at")
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class NewsTopic {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 事件线标题：聚类代表条目标题（code）或总编标题（editor） */
    @Column(name = "title", nullable = false, length = 255)
    private String title;

    /**
     * 一句话结论（≤40 字）：雷达页事件线卡片**不展开也显示的那一行**。
     * 2026-10-09 用户实测反馈"整段 200 字叙事没人看"后加 —— 卡片默认两行
     * （标题 + digest），全文点开才有。早期已撰稿的行没有它，前端用 story 首句兜底。
     */
    @Column(name = "digest", length = 128)
    private String digest;

    /** 撰稿产出的"来龙去脉"（150~250字叙事）；null = 还没撰稿 */
    @Column(name = "summary", columnDefinition = "TEXT")
    private String summary;

    /** 撰稿产出的"后续关注"；null = 还没撰稿 */
    @Column(name = "outlook", columnDefinition = "TEXT")
    private String outlook;

    /** 利好/利空/中性；**null = 信息不足不判方向**（与 news_events 同一语义） */
    @Column(name = "direction", length = 8)
    private String direction;

    @Column(name = "confidence")
    private Double confidence;

    /** 涉及标的，JSON 字符串数组（含撰稿补充的 affected_symbols） */
    @Column(name = "symbols", columnDefinition = "TEXT")
    private String symbols;

    /** 参与信源名，JSON 字符串数组（展示"N 条·M 源"用） */
    @Column(name = "sources", columnDefinition = "TEXT")
    private String sources;

    @Column(name = "first_event_at")
    private LocalDateTime firstEventAt;

    @Column(name = "last_event_at")
    private LocalDateTime lastEventAt;

    @Column(name = "event_count", nullable = false)
    private Integer eventCount;

    @Column(name = "source_count", nullable = false)
    private Integer sourceCount;

    /** open=仍有新报道进来；closed=超过静默窗口（见 NewsTopicService.TOPIC_IDLE_HOURS） */
    @Column(name = "status", nullable = false, length = 16)
    private String status;

    /** code | editor（见类注释的标题覆写规则） */
    @Column(name = "title_origin", nullable = false, length = 8)
    private String titleOrigin;

    /**
     * 未撰稿原因（封闭枚举，见 {@code NewsTopicContract.SKIP_REASONS}）：
     * null = 已撰稿或从未进入候选。每次"该写而没写"都要留痕且原因专属 ——
     * 与模拟盘 skip_reason 同一条纪律：混用一个"没写"会让运维无从排查。
     */
    @Column(name = "skip_reason", length = 32)
    private String skipReason;

    /** 总编对这条线的判断理由（"孤本报道"/"多源印证"），展示给用户看 */
    @Column(name = "editor_reason", length = 200)
    private String editorReason;

    /** 最近一次撰稿成功时间（预算口径：当日已撰稿数按它统计；冷却按它算） */
    @Column(name = "synthesized_at")
    private LocalDateTime synthesizedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @Column(name = "updated_at")
    private LocalDateTime updatedAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
        if (status == null) {
            status = "open";
        }
        if (titleOrigin == null) {
            titleOrigin = "code";
        }
        if (eventCount == null) {
            eventCount = 0;
        }
        if (sourceCount == null) {
            sourceCount = 0;
        }
    }

    @PreUpdate
    void preUpdate() {
        updatedAt = LocalDateTime.now();
    }
}
