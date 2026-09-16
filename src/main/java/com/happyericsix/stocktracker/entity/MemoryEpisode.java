package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 情节记忆（L1）—— 一个会话的"前情提要"。
 *
 * <h3>版本化，不覆盖</h3>
 * 重新总结<b>不修改</b>旧摘要，而是追加一条 {@code version + 1} 的新行，
 * "当前摘要" = 同一 (userId, sessionKey) 下 version 最大的那条。
 * 好处是摘要写坏了可以随时回退，而且摘要本身也是"当时怎么理解的"的证据。
 *
 * <h3>它是派生数据</h3>
 * 摘要完全由 {@link MemoryEvent} 账本推导出来，因此<b>可以随时重放重建</b>。
 * 这意味着摘要质量不是关键路径风险：写差了就重新生成，原始事实永远在账本里。
 * 也正因为是派生数据，它承担不了"事实来源"的角色——需要精确事实时回账本取。
 */
@Entity
@Table(name = "memory_episodes", indexes = {
        @Index(name = "idx_memory_episodes_user_session", columnList = "user_id,session_key,version"),
        @Index(name = "idx_memory_episodes_user_created", columnList = "user_id,created_at")
})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class MemoryEpisode {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(name = "session_key", nullable = false, length = 64)
    private String sessionKey;

    /** 同一会话的摘要版本，从 1 开始 */
    @Column(nullable = false)
    private Integer version;

    @Column(name = "started_at")
    private LocalDateTime startedAt;

    @Column(name = "ended_at")
    private LocalDateTime endedAt;

    /** 给人和给模型看的前情提要正文 */
    @Column(nullable = false, columnDefinition = "TEXT")
    private String summary;

    /** 关键要点，JSON 数组文本 */
    @Column(name = "key_points", columnDefinition = "TEXT")
    private String keyPoints;

    /**
     * 未决问题，JSON 数组文本。
     * 存在的意义是防幻觉：摘要里<b>不确定</b>的东西写成疑问句放这里，
     * 而不是当成事实写进 summary —— 一次把"用户可能想换策略"写成"用户要换策略"，
     * 后面每个会话都会被这个错误污染。
     */
    @Column(name = "open_questions", columnDefinition = "TEXT")
    private String openQuestions;

    /** 提到的标的/策略等实体，JSON 文本（M2 做检索时用） */
    @Column(columnDefinition = "TEXT")
    private String entities;

    /** 来源事件 id 列表，JSON 数组文本 —— 摘要必须可追溯到账本 */
    @Column(name = "source_event_ids", columnDefinition = "TEXT")
    private String sourceEventIds;

    /** 生成摘要的模型名，便于评估不同模型/提示词的效果 */
    @Column(length = 64)
    private String model;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
        if (version == null) {
            version = 1;
        }
    }
}
