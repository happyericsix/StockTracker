package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.Strategy;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface StrategyRepository extends JpaRepository<Strategy, Long> {
    List<Strategy> findByUserIdOrderByUpdatedAtDesc(Long userId);
    Optional<Strategy> findByIdAndUserId(Long id, Long userId);
    List<Strategy> findByPaperEnabledTrue();

    /** 某个用户启用模拟盘的策略（页面按用户组装报告用）。 */
    List<Strategy> findByUserIdAndPaperEnabledTrue(Long userId);
    /** 只读分享视图（token 是 32 位随机串，本身即凭证）。 */
    Optional<Strategy> findByShareToken(String shareToken);
}
