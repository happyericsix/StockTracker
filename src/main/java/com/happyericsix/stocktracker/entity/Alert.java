package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

@Entity
@Table(name = "alerts")
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class Alert {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "stock_symbol", nullable = false)
    private String stockSymbol;

    @Column(name = "condition_type", nullable = false)
    private String conditionType;
//预警阈值
    @Column(nullable = false)
    private Double threshold;

    @Column(nullable = false)
    private Boolean enabled;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Builder.Default
    @Column(nullable = false)
    private Boolean triggered = false;       // 默认未触发

    @Column
    private LocalDateTime lastTriggeredAt;   // 默认 null

    // ===== 边沿触发 + 时间衰减 v1 改造 =====

    // ===== v3 跟踪止盈(Trailing Take-Profit)=====

    /**
     * 跟踪期间最高价：仅 trailing_take_profit 使用。
     * - 每次评估都更新为 max(highWatermark, currentPrice)
     * - 触发条件：当前价从 highWatermark 回撤 ≥ threshold%
     * - 重新武装：当前价 > highWatermark（创新高）
     */
    @Column
    private Double highWatermark;

    // ===== 边沿触发 + 时间衰减 v1 改造 =====

    /** 是否可触发（边沿触发核心字段） */
    @Builder.Default
    @Column(nullable = false)
    private Boolean armed = true;

    /** 上次评估时间（用于时间衰减重置） */
    @Column
    private LocalDateTime lastEvaluatedAt;

    /** 价格回落重置比率：armed=false 后，价格回落到 threshold*resetRatio 即重置 */
    @Builder.Default
    @Column(nullable = false)
    private Double resetRatio = 0.5;

    /** 时间衰减重置小时数：armed=false 后，经过 reArmHours 小时自动重置 */
    @Builder.Default
    @Column(nullable = false)
    private Integer reArmHours = 2;

    /** 冷却分钟数（覆盖硬编码 30）；0 = 不冷却 */
    @Builder.Default
    @Column(nullable = false)
    private Integer cooldownMinutes = 5;
}
