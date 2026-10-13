package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * 委员会的**盘中授权**（authorization 模式）：下一次开会之前，规则引擎可以
 * 在什么边界内自动执行买卖信号。
 *
 * <h3>为什么需要这一层（2026-10-06）</h3>
 * agent 模式此前盘中完全短路（一天只剩 15:30 一次决策），理由是防止
 * "委员会说不动、盘中 DSL 信号照样建仓"的**决定冲突**。授权把委员会的权威
 * 从"逐单审批"前置成"划定边界"：边界内的成交是**委员会授权的执行**，
 * 不是对它的覆盖——决定权仍然只有一个持有者。
 *
 * <h3>生命周期</h3>
 * 只追加：委员会每次开会（带了 authorization 输出）插一行，盘中永远读
 * {@code findFirstByStrategyIdOrderByIssuedAtDesc}——**最新一行生效**，
 * 直到下一次会议更新。门控没开会（{@code agent_no_new_information}）的日子
 * 不产生新行，上次的授权继续有效：授权是"自上次开会起"的持续状态，
 * 不是"仅当日"的有效期——这样假期与不开会的日子都不会造成授权空窗。
 *
 * <h3>边界语义（与 Python 侧 trading_decision._clean_authorization 同源）</h3>
 * <ul>
 *   <li>{@code intradayExecution=false}：委员会明确关掉盘中执行（回到"只等日线"）；</li>
 *   <li>{@code allowOpen/allowClose}：方向边界。allow_close 的缺省比 allow_open 宽——
 *       风控语义上"允许退出"应先于"允许进场"；</li>
 *   <li>{@code maxPositionPct}（0~1）：盘中开仓的仓位上限。它是委员会的显式边界，
 *       结算侧还有策略 size_pct 与 agent size_fraction 两层夹取，三层取最小。</li>
 * </ul>
 */
@Entity
@Table(name = "paper_authorizations",
        indexes = {
                @Index(name = "idx_paper_auth_strategy", columnList = "strategy_id,issued_at"),
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PaperAuthorization {

    private static final ZoneId ZONE_CN = ZoneId.of("Asia/Shanghai");

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "strategy_id", nullable = false)
    private Strategy strategy;

    /** 委员会是否授权盘中自动执行（授权模式的总开关） */
    @Column(name = "intraday_execution", nullable = false)
    private Boolean intradayExecution;

    /** 授权开仓（买入信号可执行） */
    @Column(name = "allow_open", nullable = false)
    private Boolean allowOpen;

    /** 授权平仓（卖出信号可执行）——缺省语义比 allow_open 宽 */
    @Column(name = "allow_close", nullable = false)
    private Boolean allowClose;

    /** 盘中开仓仓位上限（0~1 fraction；三层夹取之一） */
    @Column(name = "max_position_pct", nullable = false, precision = 6, scale = 4)
    private BigDecimal maxPositionPct;

    /** 委员会给边界的理由（给 trace 与复盘看） */
    @Column(length = 500)
    private String notes;

    /** 授权签发时间（= 产生它的那次委员会决策时间） */
    @Column(name = "issued_at", nullable = false, updatable = false)
    private LocalDateTime issuedAt;

    @PrePersist
    void prePersist() {
        if (issuedAt == null) {
            issuedAt = LocalDateTime.now(ZONE_CN);
        }
    }
}
