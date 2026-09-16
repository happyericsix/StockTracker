package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 记忆账本（L0）—— agent 视角的原始事件流。
 *
 * <h3>为什么单独一张表，而不复用 {@link Message}</h3>
 * {@code messages} 是"面向用户的消息中心"（可读、可标记已读），而账本要记录 agent 看到的
 * <b>全部</b>事件：用户消息、助手回复、策略生成、以及将来要加的"工具调用与结果"。
 * 两者是投影关系不是同一张表，用 {@code eventId} 关联即可，别双写两份内容。
 *
 * <h3>只是一条硬约束：只 INSERT，永不 UPDATE/DELETE</h3>
 * 记忆的价值来自"可回溯"，一旦允许就地改写，"用户上周说过什么"就再也答不出来了。
 * 因此：
 * <ul>
 *   <li>纠正/改口 = 追加一条新记录并指向旧记录（M1 的 supersede 链），不是覆盖；</li>
 *   <li>事实失效 = 标记有效期结束，不是删行；</li>
 *   <li>唯一的例外是用户行使删除权——那走单独的硬删除通道并留审计，不在这条常规路径上。</li>
 * </ul>
 * 这条约束靠<b>代码纪律 + 数据库权限</b>双重保证：写入方（含 Python agent）只有 INSERT 权限，
 * 没有 UPDATE/DELETE 权限（见 MemoryController 的说明）。
 *
 * <h3>时间字段为什么要三个</h3>
 * 金融场景里"什么时候问的"和"问的是什么时候"完全是两回事，混在一起就会出现
 * "把三个月前的判断当作此刻事实"的错误：
 * <ul>
 *   <li>{@code occurredAt} 事情实际发生 / 用户提问的时刻</li>
 *   <li>{@code eventTime} 内容所<b>指</b>的时间（"去年" → 2025 年），可空</li>
 *   <li>{@code ingestedAt} 系统写入时刻</li>
 * </ul>
 * 相对时间必须在<b>写入时</b>解析成绝对区间（那时才"现在"是什么时候），
 * 但原始措辞 {@code rawTimePhrase} 必须一起留下，否则将来无法回答"我什么时候说过"。
 */
@Entity
@Table(name = "memory_events", indexes = {
        @Index(name = "idx_memory_events_user_session", columnList = "user_id,session_key"),
        @Index(name = "idx_memory_events_user_occurred", columnList = "user_id,occurred_at")
})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class MemoryEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 用裸 userId 而不是 @ManyToOne：账本是事件流，不该被用户实体的生命周期牵连 */
    @Column(name = "user_id", nullable = false)
    private Long userId;

    /**
     * 会话键：{@code "{userId}:{yyyy-MM-dd}"}（Asia/Shanghai）。
     * M0 有意用"一天 = 一个会话"这种轻量口径——前情提要天然按天切分，
     * 不需要额外引入会话生命周期管理。将来要精细到"一次连续对话"时改这里即可。
     */
    @Column(name = "session_key", nullable = false, length = 64)
    private String sessionKey;

    /** chat_user / chat_bot / strategy / system */
    @Column(nullable = false, length = 32)
    private String kind;

    /** user / assistant / system（喂给模型时的角色语义） */
    @Column(length = 16)
    private String role;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(length = 32)
    private String symbol;

    /** 内容所指时间，M0 基本为空；M1 由相对时间解析器填充 */
    @Column(name = "event_time")
    private LocalDateTime eventTime;

    /** 原始时间措辞（"去年""上周三"），与 eventTime 配套保留 */
    @Column(name = "raw_time_phrase", length = 64)
    private String rawTimePhrase;

    @Column(name = "occurred_at", nullable = false)
    private LocalDateTime occurredAt;

    @Column(name = "ingested_at", nullable = false)
    private LocalDateTime ingestedAt;

    @Column(name = "time_zone", length = 64)
    private String timeZone;

    /** user / model / tool / external —— 谁产生了这条内容，注入时要按信任度区别对待 */
    @Column(length = 16)
    private String provenance;

    /** high / medium / low —— external 内容一律 low，只能"参考"不能"授权动作" */
    @Column(length = 16)
    private String trust;

    /** 关联信息（如 strategyId），JSON 文本 */
    @Column(columnDefinition = "TEXT")
    private String meta;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        LocalDateTime now = LocalDateTime.now();
        if (createdAt == null) {
            createdAt = now;
        }
        if (ingestedAt == null) {
            ingestedAt = now;
        }
        if (occurredAt == null) {
            occurredAt = now;
        }
        if (timeZone == null) {
            timeZone = "Asia/Shanghai";
        }
    }
}
