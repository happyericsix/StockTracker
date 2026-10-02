package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 资讯/公告事件（spec §7 的事件主表，Python 与 Java 共享的事实层）。
 *
 * <h3>⚠️ 这张表是**全局事实表，没有也不该有 user_id**</h3>
 * 同一条公告对所有用户是**同一份事实**，per-user 只体现在"看哪些"——
 * 由 {@code NewsService} 在 {@code scope=mine} 时用 {@code favorite_stocks} 过滤。
 * 一旦加上 {@code user_id}，同一条公告会被 N 个用户存 N 份，
 * {@code url} 去重键立刻失效（唯一索引会因为 user_id 不同而允许重复）。
 *
 * <p>{@code symbol} 存 Python 侧归一化后的形态（大写带前缀，如 {@code SH600519}）；
 * 宏观资讯（财新要闻）**没有标的**，存 {@code null}（不是空串：空串会混进
 * "按标的查"的结果里，而 {@code null} 与任何 IN 查询都不匹配，语义更准）。
 */
@Entity
@Table(name = "news_events",
        uniqueConstraints = @UniqueConstraint(name = "uk_news_events_url", columnNames = {"url"}),
        indexes = {
                @Index(name = "idx_news_events_published_at", columnList = "published_at"),
                @Index(name = "idx_news_events_symbol_published", columnList = "symbol, published_at")
        })
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class NewsEvent {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 归一化代码（如 SH600519）；宏观资讯为 null */
    @Column(name = "symbol", length = 32)
    private String symbol;

    @Column(name = "name", length = 64)
    private String name;

    @Column(name = "title", nullable = false, length = 255)
    private String title;

    /** 摘要/正文（公告没有正文时等于标题）；研报与补抓的公告正文远超 VARCHAR(255) */
    @Column(name = "content", columnDefinition = "TEXT")
    private String content;

    /**
     * 去重主键（spec §7）。公告可能没有 url，那种情况下用
     * {@code (symbol, title, published_at)} 三元组兜底（见 {@code NewsService.upsert}）。
     *
     * <p>长度 512 是唯一索引与 MySQL 索引前缀上限（3072 字节 / utf8mb4）之间的折中：
     * 东财与财新的 URL 都在 200 字符以内，512 足够且不会让建表失败。
     */
    @Column(name = "url", length = 512)
    private String url;

    /** 1公告 2媒体 3研报 4舆情（与 Python 的 LEVEL_* 常量一致） */
    @Column(name = "source_level", nullable = false)
    private Integer sourceLevel;

    @Column(name = "source_name", length = 64)
    private String sourceName;

    /** 源站自带的分类（公告类型 / 财新 tag）：未做 AI 分析前，它是唯一的分类线索 */
    @Column(name = "event_type_raw", length = 64)
    private String eventTypeRaw;

    /**
     * 资讯雷达的"文件夹"分类（封闭枚举，见 {@code NewsCategory}）。
     * 落库时由关键词规则打初值，AI 解读返回的 category 优先覆写 ——
     * 规则兜底保证"没解读的条目也有文件夹可归"，模型修正错分。
     */
    @Column(name = "category", length = 16)
    private String category;

    /**
     * 原文正文（"去链接化"）：首次被阅读时懒抓取（article_fetcher 白名单管线），
     * 之后一直用库里的。{@code bodyFetchedAt} 防重复抓取 —— 抓取失败也记录时间，
     * 避免一条永远抓不到的链接被反复请求。
     */
    @Lob
    @Column(name = "body", columnDefinition = "TEXT")
    private String body;

    @Column(name = "body_fetched_at")
    private LocalDateTime bodyFetchedAt;

    /** spec §6 的 event_type，AI 分析后回填 */
    @Column(name = "event_type", length = 32)
    private String eventType;

    /** 利好/利空/中性；**null = 信息不足，不判断方向**（不是"中性"） */
    @Column(name = "direction", length = 8)
    private String direction;

    @Column(name = "confidence")
    private Double confidence;

    /** high / medium / low */
    @Column(name = "impact_level", length = 16)
    private String impactLevel;

    /** 大白话一句话：发生了什么 + 对持有者意味着什么 */
    @Column(name = "plain_summary", columnDefinition = "TEXT")
    private String plainSummary;

    /** 风险点（红标），JSON 字符串数组；读的时候反序列化 */
    @Column(name = "risks", columnDefinition = "TEXT")
    private String risks;

    /** 机会点（绿标），JSON 字符串数组 */
    @Column(name = "opportunities", columnDefinition = "TEXT")
    private String opportunities;

    /** spec §6 的 related_symbols，JSON 字符串数组（一条宏观资讯可关联多只票） */
    @Column(name = "related_symbols", columnDefinition = "TEXT")
    private String relatedSymbols;

    /**
     * 信息可信度 0-100（Python news_credibility 规则引擎产出；null = 未评估）。
     *
     * <p>判的是**传播链路可信度**（信源级别 + 措辞信号 + 交叉印证），
     * 不是事实真伪——后者需要对照官方登记/裁判文书，超出资讯管道的能力边界。
     */
    @Column(name = "credibility")
    private Integer credibility;

    /** 高 / 较高 / 中 / 较低 / 低；与 credibility 同批写入 */
    @Column(name = "credibility_grade", length = 8)
    private String credibilityGrade;

    /**
     * 可信度明细 JSON：components{source,content,corroboration} + reasons[] +
     * rumor_flag / sensational_flag / corroborated。
     * 分数给快读，明细给"为什么"——用户必须能核对理由，而不是盲信一个数字。
     */
    @Column(name = "credibility_detail", columnDefinition = "TEXT")
    private String credibilityDetail;

    /**
     * 源站给出的发布时间。
     *
     * <p><b>允许为 null</b>：源站偶尔没有时间（Python 侧 {@code published_at} 为空串）。
     * 那条资讯照样要入库、照样要能被看见，所以时间窗口查询统一写成
     * {@code published_at IS NULL OR published_at >= :since} —— 与 Python 的
     * {@code _in_window}（"没有时间的条目返回 True"）保持同一套语义。
     */
    @Column(name = "published_at")
    private LocalDateTime publishedAt;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    /**
     * 兜底默认值。
     *
     * <p>刻意**不在字段上写初始值**：{@code @Builder} 不会带上字段初始值
     * （除非标 {@code @Builder.Default}），{@code builder().build()} 会得到 null 并撞
     * {@code nullable = false}。放在这里则"无论谁来构造实体"都成立。
     */
    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now();
        }
        if (sourceLevel == null) {
            // 级别未知时按"媒体"处理（Python 的 _event 默认值也是 2），不假装它是公告
            sourceLevel = 2;
        }
    }
}
