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
