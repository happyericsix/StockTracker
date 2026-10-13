package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.JobRun;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;

@Repository
public interface JobRunRepository extends JpaRepository<JobRun, Long> {

    /** 每个任务键的最新一行（状态页用）：比逐键 findFirst 快，也只扫一遍。 */
    @Query("""
            SELECT j FROM JobRun j
             WHERE j.finishedAt = (
                    SELECT MAX(k.finishedAt) FROM JobRun k WHERE k.jobName = j.jobName)
             ORDER BY j.jobName
            """)
    List<JobRun> findLatestPerJob();

    /** 心跳自身的保留清理（NewsRetentionJob 顺带做）：只留最近 N 天的点名册。 */
    long deleteByFinishedAtBefore(LocalDateTime cutoff);
}
