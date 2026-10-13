package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryFact;
import jakarta.persistence.LockModeType;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
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

    /**
     * 某个键（subject + predicate）上当前有效的事实，最多一条是正常状态。
     * <p>排序必须钉死（id 倒序 = 最新在前）：历史脏数据或曾经的并发写可能留下
     * 多条 active，调用方取 {@code get(0)} 时不能靠数据库心情决定取代谁。
     */
    @Query("select f from MemoryFact f where f.userId = :userId "
            + "and lower(f.subject) = lower(:subject) and lower(f.predicate) = lower(:predicate) "
            + "and not exists (select 1 from MemoryFact g where g.supersedesId = f.id) "
            + "order by f.id desc")
    List<MemoryFact> findActiveByKey(@Param("userId") Long userId,
                                     @Param("subject") String subject,
                                     @Param("predicate") String predicate);

    /**
     * 同 {@link #findActiveByKey}，但带行锁——**只给写入路径用**（saveFacts 的事务里）。
     *
     * <p>为什么需要：会话中段巩固与跨天补巩固可能并发对同一把键写入，两个事务
     * 都读到"该键无 active 行"再各自 INSERT，键上就会出现两条 active——之后
     * 新事实只取代其中一条，另一条继续"当前有效"，系统里并存两个矛盾的止损值。
     * {@code FOR UPDATE} 在键索引上加间隙锁，把"读→判→写"串行化。
     * 读路径（检索/报告）不要用这个方法：悲观锁要求事务，会把无事务的只读调用方搞挂。
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select f from MemoryFact f where f.userId = :userId "
            + "and lower(f.subject) = lower(:subject) and lower(f.predicate) = lower(:predicate) "
            + "and not exists (select 1 from MemoryFact g where g.supersedesId = f.id) "
            + "order by f.id desc")
    List<MemoryFact> findActiveByKeyForUpdate(@Param("userId") Long userId,
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

    long countByUserId(Long userId);

    /**
     * 双层记忆回填（一次性）：把系统遥测谓词的客观事实标记为不可见。
     * 只摸仍标记可见的行 —— 回填完成后就是零命中，幂等且零成本。
     */
    @org.springframework.data.jpa.repository.Modifying
    @Query("update MemoryFact f set f.userVisible = false "
            + "where f.factType = :observation and f.predicate in :predicates and f.userVisible = true")
    int markTelemetryInvisible(@Param("observation") String observation,
                               @Param("predicates") java.util.Collection<String> predicates);

    /** 有客观事实的策略主体（userId + subject 去重）—— 聚合叙事回填的遍历范围。 */
    @Query("select distinct f.userId, f.subject from MemoryFact f where f.subject like 'strategy:%'")
    List<Object[]> findDistinctStrategySubjects();
}
