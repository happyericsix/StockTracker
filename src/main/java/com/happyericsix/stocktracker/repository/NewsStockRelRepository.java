package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.NewsStockRel;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;

@Repository
public interface NewsStockRelRepository extends JpaRepository<NewsStockRel, Long> {

    /** 幂等插入的判据：同一条资讯与同一只票只该有一行关联。 */
    boolean existsByEventIdAndSymbol(Long eventId, String symbol);

    /**
     * 时间轴（个股）里"不是它自己的"那部分事件 id —— 宏观/行业资讯经 AI 的
     * {@code related_symbols} 关联到这只票。刻意只取 id：时间轴最终要的是事件本体，
     * 先取 id 再一次 {@code findAllById} 比逐行加载关联对象少一半查询。
     */
    @Query("""
            SELECT r.eventId FROM NewsStockRel r
             WHERE r.symbol IN :symbols
               AND (r.publishedAt IS NULL OR r.publishedAt >= :since)
            """)
    List<Long> findRelatedEventIds(@Param("symbols") Collection<String> symbols,
                                   @Param("since") LocalDateTime since);
}
