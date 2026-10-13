package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.NewsEvent;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;
import java.util.Optional;

@Repository
public interface NewsEventRepository extends JpaRepository<NewsEvent, Long> {

    /** 去重主键：url 唯一（spec §7）。 */
    Optional<NewsEvent> findByUrl(String url);

    /** 文件夹分类回填：只摸还没打标的存量行（回填完成后即空查询） */
    List<NewsEvent> findByCategoryIsNull();

    /** 时效清理：披露类（公告/研报）按信源级别统一保留期 */
    long deleteBySourceLevelInAndPublishedAtBefore(Collection<Integer> levels, LocalDateTime cutoff);

    /** 时效清理：普通分类各自的保留期（排除披露类，它们按上一条的长周期走） */
    long deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
            String category, Collection<Integer> levels, LocalDateTime cutoff);

    /** 时效清理：未分类存量行的兜底 */
    long deleteByCategoryIsNullAndSourceLevelNotInAndPublishedAtBefore(
            Collection<Integer> levels, LocalDateTime cutoff);

    /**
     * 公告没有 url 时的兜底去重键 {@code (symbol, title, published_at)}。
     *
     * <p>为什么只按 title 取候选、再到 Java 里比 symbol 与时间：JPQL 里
     * {@code published_at = NULL} 永远不成立，而"源站没给时间"（以及宏观资讯没有代码）
     * 恰恰是最需要兜底键的情况 —— 用 null 参数去查 null 列会静默查不到，
     * 于是同一条公告每次刷新都重新入库。null 与 null 相等这件事只能在 Java 里判。
     */
    List<NewsEvent> findByTitle(String title);

    /** 时间轴（个股）：库内该标的自己的事件。 */
    @Query("""
            SELECT e FROM NewsEvent e
             WHERE e.symbol IN :symbols
               AND (e.publishedAt IS NULL OR e.publishedAt >= :since)
             ORDER BY e.publishedAt DESC, e.id DESC
            """)
    List<NewsEvent> findTimeline(@Param("symbols") Collection<String> symbols,
                                 @Param("since") LocalDateTime since,
                                 Pageable pageable);

    /**
     * 正文预抓候选：近 {@code since} 内、有链接、还没抓到正文、且不在重试冷却内的条目。
     *
     * <p>背景（2026-10-05 实测）：527 条事件只有 4 条有正文——抓取只在用户点开时
     * 懒触发，绝大多数条目从未被尝试。预抓把它们在后台补齐，解读才有"原文"可读。
     * 公告优先（sourceLevel=1 排最前）：公告走 JSON API 近乎必成，
     * 媒体页成败参半——先收割确定性高的。公告同时**豁免时间窗**（{@code sourceLevel=1}
     * 直接入选）：媒体的时效短（保留期 3~30 天），公告按披露类保留 180 天，
     * 用媒体的窗口会把历史公告全部挡在外面（实测 158 条公告全在 14 天外）。
     * 失败重试靠 {@code bodyFetchedAt}：抓失败也会盖上时间戳，冷却窗口
     * （Java 侧传 {@code retryBoundary}）过后允许再试一次；窗口外的过期条目
     * 自然不再入选，不会无限重试。
     */
    @Query("""
            SELECT e FROM NewsEvent e
             WHERE e.url IS NOT NULL AND e.url <> ''
               AND (e.body IS NULL OR e.body = '')
               AND (e.bodyFetchedAt IS NULL OR e.bodyFetchedAt < :retryBoundary)
               AND (e.sourceLevel = 1
                    OR e.publishedAt IS NULL OR e.publishedAt >= :since)
             ORDER BY CASE WHEN e.sourceLevel = 1 THEN 0 ELSE 1 END, e.publishedAt DESC, e.id DESC
            """)
    List<NewsEvent> findBodyPrefetchCandidates(@Param("since") LocalDateTime since,
                                               @Param("retryBoundary") LocalDateTime retryBoundary,
                                               Pageable pageable);

    /** 近 48h 全市场事件（市场综合的输入，时间倒序）。 */
    List<NewsEvent> findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
            LocalDateTime since, Pageable pageable);

    /** 某时刻之后发布的条数（资讯页"新消息"红点，按发布时间）。 */
    long countByPublishedAtGreaterThanEqual(LocalDateTime since);


    /**
     * 库优先搜索：时间窗口 + 级别 + 关键词（全部命中标题或正文）。
     *
     * <p>时间条件写成 {@code IS NULL OR >=}：没有发布时间的条目照样要能被搜到，
     * 与 Python 的 {@code _in_window} 同一套语义（"看不见的错误比看得见的多余更危险"）。
     */
    @Query("""
            SELECT e FROM NewsEvent e
             WHERE (e.publishedAt IS NULL OR e.publishedAt >= :since)
               AND e.sourceLevel IN :levels
               AND (LOWER(e.title) LIKE :keyword OR LOWER(e.content) LIKE :keyword)
            """)
    Page<NewsEvent> searchAll(@Param("since") LocalDateTime since,
                              @Param("levels") Collection<Integer> levels,
                              @Param("keyword") String keyword,
                              Pageable pageable);

    /** 同上，但限定标的（个股页/个股搜索用）。 */
    @Query("""
            SELECT e FROM NewsEvent e
             WHERE (e.publishedAt IS NULL OR e.publishedAt >= :since)
               AND e.sourceLevel IN :levels
               AND (LOWER(e.title) LIKE :keyword OR LOWER(e.content) LIKE :keyword)
               AND e.symbol IN :symbols
            """)
    Page<NewsEvent> searchBySymbols(@Param("since") LocalDateTime since,
                                    @Param("levels") Collection<Integer> levels,
                                    @Param("keyword") String keyword,
                                    @Param("symbols") Collection<String> symbols,
                                    Pageable pageable);

    /**
     * 窗口内该标的的**最新**一条事件时间；库内没有该标的时返回 null。
     *
     * <p>"库优先 + 实时兜底"的判据就是它：结果为空 → 兜底；
     * 有结果但最新一条早于今天 → 也要兜底（否则用户看不到今天的新公告）。
     */
    @Query("SELECT MAX(e.publishedAt) FROM NewsEvent e WHERE e.symbol IN :symbols")
    LocalDateTime latestPublishedAt(@Param("symbols") Collection<String> symbols);

    /**
     * 微调语料导出（内部接口用）：窗口内**已解读**的条目，按发布时间倒序。
     *
     * <p>只取 plainSummary 非空的行——蒸馏的监督信号来自通过 schema 规整的
     * 分析结论，降级行（"信息不足，不判断方向"）当标签只会教会模型敷衍。
     */
    List<NewsEvent> findByPlainSummaryNotNullAndPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
            LocalDateTime since, Pageable pageable);
}
