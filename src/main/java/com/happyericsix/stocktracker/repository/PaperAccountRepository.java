package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperAccount;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface PaperAccountRepository extends JpaRepository<PaperAccount, Long> {
    Optional<PaperAccount> findByStrategyId(Long strategyId);
}
