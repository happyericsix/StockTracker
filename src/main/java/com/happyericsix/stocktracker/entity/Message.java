package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "messages")
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class Message {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    /** CHAT_USER / CHAT_BOT / ALERT / SYSTEM */
    @Column(nullable = false, length = 32)
    private String type;

    @Column(nullable = false, columnDefinition = "TEXT")
    private String content;

    @Column(name = "related_symbol", length = 32)
    private String relatedSymbol;

    /**
     * 关联的预警 ID：仅当 type=ALERT 时使用，无 FK（Alert 删除不影响历史）
     * 用于冷却判断："该预警 N 分钟内是否已触发过"
     */
    @Column(name = "alert_id")
    private Long alertId;

    /**
     * 结构化元数据（JSON 格式）：仅 type=ALERT 时使用
     * 典型内容：{"triggerPrice":195.32,"triggerValue":72.5,"threshold":70,"conditionType":"rsi_overbought"}
     */
    @Column(name = "metadata", columnDefinition = "TEXT")
    private String metadata;

    /** read 是 MySQL 保留字，列名映射为 is_read */
    @Column(name = "is_read", nullable = false)
    private Boolean read = false;//是否已读

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
        if (read == null) {
            read = false;
        }
    }
}
