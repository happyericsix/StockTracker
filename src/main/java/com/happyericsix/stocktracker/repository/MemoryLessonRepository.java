package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryLesson;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 经验记忆仓储。
 *
 * <p>"当前经验" = 同一 (taskType, symptom) 下 id 最大的那条（内容只追加）；
 * "出现过几次" = 该键的行数。两者都是查询推导出来的，不维护计数器。
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

    List<MemoryLesson> findByUserIdAndStatusOrderByIdDesc(Long userId, String status);

    long countByUserId(Long userId);
}
