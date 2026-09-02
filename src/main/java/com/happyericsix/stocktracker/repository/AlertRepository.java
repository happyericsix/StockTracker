package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.Alert;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;
import java.util.Optional;

@Repository
public interface AlertRepository extends JpaRepository<Alert, Long> {
    List<Alert> findByUserId(Long userId);
    Optional<Alert> findByIdAndUserId(Long id, Long userId);
    List<Alert> findByUserIdAndEnabledTrue(Long userId);
    List<Alert> findByUserIdAndStockSymbol(Long userId, String stockSymbol);
    /** Job 用：拉所有启用的预警 */
    List<Alert> findByEnabledTrue();
}
