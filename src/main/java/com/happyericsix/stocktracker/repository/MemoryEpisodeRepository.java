package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryEpisode;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

/**
 * 情节记忆（前情提要）仓储。
 *
 * "当前摘要"永远是同一 (userId, sessionKey) 下 version 最大的那条 ——
 * 重新总结只追加新版本，不覆盖旧版本（见 MemoryEpisode 的说明）。
 */
@Repository
public interface MemoryEpisodeRepository extends JpaRepository<MemoryEpisode, Long> {

    Optional<MemoryEpisode> findFirstByUserIdAndSessionKeyOrderByVersionDesc(Long userId, String sessionKey);

    boolean existsByUserIdAndSessionKey(Long userId, String sessionKey);

    /** 最近若干条摘要（跨会话），用于组装"前情提要" */
    List<MemoryEpisode> findByUserIdOrderByCreatedAtDesc(Long userId, org.springframework.data.domain.Pageable pageable);

    long countByUserId(Long userId);
}
