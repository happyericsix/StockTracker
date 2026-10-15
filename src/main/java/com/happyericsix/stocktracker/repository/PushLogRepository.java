package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PushLog;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;

@Repository
public interface PushLogRepository extends JpaRepository<PushLog, Long> {

    /** Profile 页"最近推送状态"：按用户取最近几条（索引 user_id, created_at 直接命中）。 */
    List<PushLog> findTop10ByUserIdOrderByCreatedAtDesc(Long userId);

    /** 保留期清理（NewsRetentionJob 顺带做）：与 job_runs 同一条"点名册只留近期"纪律。 */
    long deleteByCreatedAtBefore(LocalDateTime cutoff);
}
