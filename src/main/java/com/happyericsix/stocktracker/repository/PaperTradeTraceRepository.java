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

    /**
     * 某策略全部<b>日线结算</b>痕迹按日正序（净值补账用，2026-11-18）。
     *
     * <p>只取 daily：realtime 一天可写几十条，全量拉会把回填撑爆；
     * daily 痕迹每天至多几条，量被交易日数天然封顶。equityAfter 等字段
     * 是"当天结算后的账户终态"，正是回填快照要照抄的数字。
     */
    List<PaperTradeTrace> findByStrategyIdAndSettlementKindOrderByTradeDateAscCreatedAtAsc(
            Long strategyId, String settlementKind);

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
