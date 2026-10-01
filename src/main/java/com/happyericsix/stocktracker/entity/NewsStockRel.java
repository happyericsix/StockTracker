package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 事件 ↔ 代码（spec §7）。建这张表的**唯一理由**是：一条资讯可以关联多只标的。
 *
 * <p>个股公告天然一对一（{@code news_events.symbol} 就是答案），但两类条目不是：
 * <ul>
 *   <li>宏观/行业资讯（财新要闻）根本没有标的，只有 AI 分析出的
 *       {@code related_symbols} 才知道它跟谁有关；</li>
 *   <li>一条"行业政策"会同时关联板块里好几只票。</li>
 * </ul>
 * 没有这张表，个股时间轴就只能看到"标题里正好写着这只票"的条目 ——
 * 而那恰恰是最不重要的一半（真正影响股价的政策与行业新闻都不写代码）。
 *
 * <p>{@code event_id} 刻意用**裸 Long 而不是 {@code @ManyToOne}**：与
 * {@code Message.alertId} 同一套理由 —— 事件是只增不改的事实层，
 * 关联行不需要（也不该）通过实体图反向加载事件，那只会把一次时间轴查询
 * 变成 N+1 次查询。
 */
@Entity
@Table(name = "news_stock_rel",
        indexes = @Index(name = "idx_news_stock_rel_symbol_published", columnList = "symbol, published_at"))
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class NewsStockRel {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 关联的事件 id（无 FK：关联行的生死不改变事件本身） */
    @Column(name = "event_id", nullable = false)
    private Long eventId;

    /** 归一化代码（如 SH600519） */
    @Column(name = "symbol", nullable = false, length = 32)
    private String symbol;

    /** 冗余一份事件时间：时间轴按 {@code (symbol, published_at)} 走索引直接命中 */
    @Column(name = "published_at")
    private LocalDateTime publishedAt;
}
