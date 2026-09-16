package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryFact;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 语义事实仓储。
 *
 * <h3>「当前有效」是怎么算出来的</h3>
 * 一条事实之所以失效，是因为<b>有另一条事实指向了它</b>（{@code supersedesId}）。
 * 所以"当前有效 = 没有任何行指向它"，用 {@code NOT EXISTS} 子查询表达。
 * 这样失效不需要 UPDATE 旧行，账本保持只追加（见 MemoryFact 的说明）。
 *
 * <p>同样地：这里<b>不提供</b>修改事实内容的派生方法。用户改口 = 追加新事实 + 指向旧的。
 */
@Repository
public interface MemoryFactRepository extends JpaRepository<MemoryFact, Long> {

    /** 某个用户当前有效的事实 */
    @Query("select f from MemoryFact f where f.userId = :userId "
            + "and not exists (select 1 from MemoryFact g where g.supersedesId = f.id)")
    List<MemoryFact> findActiveByUserId(@Param("userId") Long userId);

    /** 某个键（subject + predicate）上当前有效的事实，最多一条是正常状态 */
    @Query("select f from MemoryFact f where f.userId = :userId "
            + "and lower(f.subject) = lower(:subject) and lower(f.predicate) = lower(:predicate) "
            + "and not exists (select 1 from MemoryFact g where g.supersedesId = f.id)")
    List<MemoryFact> findActiveByKey(@Param("userId") Long userId,
                                     @Param("subject") String subject,
                                     @Param("predicate") String predicate);

    /** 某个键的全部历史（含已被取代的），按时间倒序 */
    @Query("select f from MemoryFact f where f.userId = :userId "
            + "and lower(f.subject) = lower(:subject) and lower(f.predicate) = lower(:predicate) "
            + "order by f.id desc")
    List<MemoryFact> findHistoryByKey(@Param("userId") Long userId,
                                      @Param("subject") String subject,
                                      @Param("predicate") String predicate,
                                      Pageable pageable);

    /** 谁取代了这条事实（用于展示"此前为 X"） */
    List<MemoryFact> findBySupersedesId(Long supersedesId);

    long countByUserId(Long userId);
}
