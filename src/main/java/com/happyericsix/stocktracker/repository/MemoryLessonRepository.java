package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryLesson;
import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 经验记忆仓储。
 *
 * <p>"当前经验" = 同一 (taskType, symptom) 下未被取代的最新行（内容只追加，修订走取代链）；
 * "出现过几次" = 该键下 supersedesId 为空的行数（修订不灌水复发计数）。
 * 两者都是查询推导出来的，不维护计数器。
 */
@Repository
public interface MemoryLessonRepository extends JpaRepository<MemoryLesson, Long> {

    List<MemoryLesson> findByUserIdOrderByIdDesc(Long userId);

    /** 某个键的全部记录（按时间倒序），用于展示"遇到过几次、每次怎么处理的" */
    @Query("select l from MemoryLesson l where l.userId = :userId "
            + "and lower(l.taskType) = lower(:taskType) and lower(l.symptom) = lower(:symptom) "
            + "order by l.id desc")
    List<MemoryLesson> findByKey(@Param("userId") Long userId,
                                 @Param("taskType") String taskType,
                                 @Param("symptom") String symptom);

    long countByUserIdAndTaskTypeAndSymptom(Long userId, String taskType, String symptom);

    /**
     * 复发计数只数"独立出生"的行（supersedesId 为空）——修订是对既有经验换版本，
     * 不是同类问题再现，不该推高"值不值得升格 skill"的复发信号。
     */
    long countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
            Long userId, String taskType, String symptom);

    List<MemoryLesson> findByUserIdAndStatusOrderByIdDesc(Long userId, String status);

    long countByUserId(Long userId);

    /**
     * 按 id 加行锁取教训（写路径专用，须在事务内）：周反思 job 与用户手工编辑
     * 可能同时修订同一条，不锁会出现两条新版本各自指向旧行的分叉链。
     */
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select l from MemoryLesson l where l.id = :id")
    MemoryLesson findByIdForUpdate(@Param("id") Long id);

    /** 该行是否已被新版本取代（有行的 supersedesId 指向它即是）——修订前的分叉防护。 */
    boolean existsBySupersedesId(Long id);
}
