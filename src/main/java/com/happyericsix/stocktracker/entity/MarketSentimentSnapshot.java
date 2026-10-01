package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDate;

/**
 * 个股散户情绪的<b>每日快照</b>（东财千股千评聚合指数）。
 *
 * <h3>为什么必须有这张表（Python 接口给不了）</h3>
 * 参与意愿接口<b>只返回最近 5 天</b>（实地探针 2026-09-30），它的长序列只能靠
 * 每日落一次快照自己累积——攒满 20 个交易日后才能做"情绪 vs 次日涨跌"的对照
 * 验证。关注指数/评分接口自带 30 天历史，不需要快照也能验证，但顺手一起存：
 * 一致的数据形状 + 上游接口改版时的自保护。
 *
 * <p>幂等键：{@code (symbol, tradeDate)} 唯一——任务重跑/手工补跑只覆盖不新增。
 */
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
@Entity(name = "market_sentiment_snapshot")
@Table(name = "market_sentiment_snapshots",
        uniqueConstraints = @UniqueConstraint(columnNames = {"symbol", "trade_date"}))
public class MarketSentimentSnapshot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "symbol", nullable = false, length = 32)
    private String symbol;

    /** 快照对应的交易日（按东八区口径取） */
    @Column(name = "trade_date", nullable = false)
    private LocalDate tradeDate;

    /** 市场参与意愿（0-100，股吧评论行为聚合） */
    @Column(name = "desire")
    private Double desire;

    @Column(name = "desire_avg5")
    private Double desireAvg5;

    /** 用户关注指数（0-100） */
    @Column(name = "focus")
    private Double focus;

    /** 千股千评综合评分 */
    @Column(name = "score")
    private Double score;

    @Column(name = "created_at", nullable = false, updatable = false)
    private java.time.LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = com.happyericsix.stocktracker.util.CnTime.now();
        }
    }
}
