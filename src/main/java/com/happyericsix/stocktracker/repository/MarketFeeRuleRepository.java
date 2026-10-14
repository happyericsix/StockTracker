package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MarketFeeRule;
import org.springframework.data.jpa.repository.JpaRepository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

public interface MarketFeeRuleRepository extends JpaRepository<MarketFeeRule, Long> {

    List<MarketFeeRule> findByRuleKeyOrderByEffectiveFromAsc(String ruleKey);

    /** 结算日适用的规则：effectiveFrom ≤ 结算日里最新的一条。 */
    Optional<MarketFeeRule> findFirstByRuleKeyAndEffectiveFromLessThanEqualOrderByEffectiveFromDesc(
            String ruleKey, LocalDate effectiveFrom);
}
