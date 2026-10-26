package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import jakarta.persistence.Temporal;
import jakarta.persistence.TemporalType;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 策略健康事件（策略哨兵 Phase 1）。
 *
 * <p>记录"某个确定性判据在某天对某条策略成立"这一事实，作用有两个：
 * <ol>
 *   <li><b>冷却判据</b>：同一策略同一事件类型在冷却窗口内只推一条消息
 *       （防骚扰是硬规则，写在这里而不是提示词里）；</li>
 *   <li><b>审计留痕</b>：事件本身就算消息没推出去（冷却/异常）也留档，
 *       Phase 2 的 LLM 分诊和决策日志都从这里取历史。</li>
 * </ol>
 *
 * <p>证据字段存**已算好的数字摘要**（"连续 12 个交易日无成交；期间信号均为 hold"），
 * 不存原始查询——回查原始数据用痕迹/净值/事实表，这里只回答"当时因为什么触发"。
 */
@Data
@Entity
@NoArgsConstructor
@Table(name = "strategy_health_events", indexes = {
        @Index(name = "idx_health_strategy_type", columnList = "strategyId,eventType"),
        @Index(name = "idx_health_created", columnList = "createdAt")
})
public class StrategyHealthEvent {

    public static final String TYPE_IDLE_STREAK = "idle_streak";
    public static final String TYPE_DRAWDOWN_BREACH = "drawdown_breach";
    public static final String TYPE_VERIFICATION_FAILED = "verification_failed";
    public static final String TYPE_NEWS_MATERIAL = "news_material";

    /** 情绪哨兵（2026-10-16 ③步）：标的登上龙虎榜——关注度异动提醒，只告知不提案。 */
    public static final String TYPE_LHB_ATTENTION = "lhb_attention";
    /** 满仓单票（持仓市值占总净值 ≥ 阈值）：集中度观察，只报事实不建议。 */
    public static final String TYPE_CONCENTRATION_FULL = "concentration_full";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false)
    private Long strategyId;

    @Column(nullable = false, length = 40)
    private String eventType;

    /** 触发时的证据摘要（人话 + 关键数字），推送文案的原料。 */
    @Column(length = 1000)
    private String evidence;

    /** 是否已推送消息（冷却期内重复触发 = 记录但不推）。 */
    @Column(nullable = false)
    private boolean notified;

    @Temporal(TemporalType.TIMESTAMP)
    @Column(nullable = false)
    private LocalDateTime createdAt;

    @Builder
    public StrategyHealthEvent(Long id, Long strategyId, String eventType, String evidence,
                               boolean notified, LocalDateTime createdAt) {
        this.id = id;
        this.strategyId = strategyId;
        this.eventType = eventType;
        this.evidence = evidence;
        this.notified = notified;
        this.createdAt = createdAt;
    }
}
