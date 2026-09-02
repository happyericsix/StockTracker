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
}
