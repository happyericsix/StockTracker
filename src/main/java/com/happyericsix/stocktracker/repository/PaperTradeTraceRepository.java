package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

@Repository
public interface PaperTradeTraceRepository extends JpaRepository<PaperTradeTrace, Long> {

    /** 去重键是唯一真相源：结算重跑与每 5 分钟的实时路径都靠它避免重复写行。 */
    Optional<PaperTradeTrace> findByDedupeKey(String dedupeKey);

    List<PaperTradeTrace> findByStrategyIdOrderByCreatedAtDesc(Long strategyId, Pageable pageable);

    List<PaperTradeTrace> findByStrategyIdAndTradeDateOrderByCreatedAtAsc(Long strategyId, LocalDate tradeDate);

    List<PaperTradeTrace> findByStrategyIdAndSkipReasonOrderByCreatedAtDesc(Long strategyId, String skipReason);

    long countByStrategyId(Long strategyId);

    /**
     * 最近一次**真的召集了委员会**的痕迹（agent 调用次数 > 0）。
     *
     * <p>为什么不用账户上的 {@code lastEvalAt}：那个每天结算都会刷新，
     * 于是"太久没开会"这条触发理由永远不会到期 —— agent 会在规则停止出信号之后**永久沉默**。
     * 要的是"上次真的看过行情是什么时候"。
     */
    Optional<PaperTradeTrace> findFirstByStrategyIdAndAgentLlmCallsGreaterThanOrderByCreatedAtDesc(
            Long strategyId, Integer calls);
}
