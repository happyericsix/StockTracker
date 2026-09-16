package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 经验记忆（L3 procedural）—— "这类问题上次是怎么解决的"。
 *
 * <h3>为什么要有这一层</h3>
 * 事实层记的是"用户是什么样的人"，经验层记的是"系统怎么做成的事"。两者不能混：
 * "回测数据不足时改用更长周期的标的" 不是用户偏好，但它能避免下一次重复踩坑。
 * 这也是用户明确提过的需求：agent 遇到问题时要记住是怎么解决的。
 *
 * <h3>内容只追加，"审核状态"是例外</h3>
 * 每遇到一次同类问题就追加一行（带这次的证据事件），所以：
 * <ul>
 *   <li>"当前经验" = 同一个 (taskType, symptom) 下最新的那条；</li>
 *   <li>"出现过几次" = 该键的行数（不需要额外计数器，也不会因为并发写丢计数）。</li>
 * </ul>
 * 唯一的就地更新是 {@code status}（pending → active → retired）：那是<b>审核元数据</b>，
 * 记录"人有没有确认过这条经验"，不是记忆内容本身，与"内容只追加"不冲突
 * （和 Message.read 是同一类字段）。
 *
 * <h3>为什么默认 pending、且必须人工确认才能 active</h3>
 * 经验会被注入到以后的对话里，一旦被污染（外部内容诱导出的"解法"）就会持续生效。
 * 2026 年已有针对"自演化 agent 技能轨迹投毒"的系统性研究，所以：
 * <b>经验永远只是参考，绝不自动升级成可执行规则或 skill</b>，升格必须过人这一关。
 */
@Entity
@Table(name = "memory_lessons", indexes = {
        @Index(name = "idx_memory_lessons_user_key", columnList = "user_id,task_type,symptom"),
        @Index(name = "idx_memory_lessons_user_status", columnList = "user_id,status")
})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class MemoryLesson {

    public static final String STATUS_PENDING = "pending";
    public static final String STATUS_ACTIVE = "active";
    public static final String STATUS_RETIRED = "retired";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    @Column(name = "session_key", length = 64)
    private String sessionKey;

    /** 任务类型：generate_strategy / backtest / quote_lookup ... */
    @Column(name = "task_type", nullable = false, length = 64)
    private String taskType;

    /** 症状：可复现的失败现象（"回测数据不足"）——注入时按它匹配 */
    @Column(nullable = false, length = 512)
    private String symptom;

    /** 上下文（股票、参数等），JSON 文本 */
    @Column(columnDefinition = "TEXT")
    private String context;

    /** 试过什么、为什么不行，JSON 数组文本 */
    @Column(columnDefinition = "TEXT")
    private String attempts;

    /** 最后是怎么解决的 */
    @Column(columnDefinition = "TEXT")
    private String resolution;

    /** 可复用做法：一句可执行、可验证的话（不是投资结论） */
    @Column(name = "reusable_rule", columnDefinition = "TEXT")
    private String reusableRule;

    /** 来源账本事件 id，JSON 数组文本 */
    @Column(name = "evidence_event_ids", columnDefinition = "TEXT")
    private String evidenceEventIds;

    private Double confidence;

    /** pending / active / retired —— 唯一允许就地更新的字段（审核元数据） */
    @Column(nullable = false, length = 16)
    private String status;

    @Column(length = 16)
    private String provenance;

    @Column(length = 16)
    private String trust;

    @Column(name = "recorded_at", nullable = false)
    private LocalDateTime recordedAt;

    @Column(name = "last_used_at")
    private LocalDateTime lastUsedAt;

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
        if (status == null) {
            status = STATUS_PENDING;
        }
    }

    /** 经验键：出现次数与"当前经验"都按它统计 */
    @Transient
    public String lessonKey() {
        return (taskType == null ? "" : taskType.trim().toLowerCase()) + "|"
                + (symptom == null ? "" : symptom.trim().toLowerCase());
    }
}
