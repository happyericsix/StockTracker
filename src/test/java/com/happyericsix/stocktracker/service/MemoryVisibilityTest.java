package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 双层记忆（2026-10-09）的写入语义：
 * 客观遥测默认**不可见**（照常落库与注入，但不进用户门面）；
 * 聚合叙事可见（decision 类，用户"策略结论"区的展示主体）；
 * 里程碑判定（首次建仓/清仓）是"什么时候算第一次"的语义，钉死。
 */
class MemoryVisibilityTest {

    private static final tools.jackson.databind.ObjectMapper MAPPER = new tools.jackson.databind.ObjectMapper();

    private static final LocalDateTime AT = LocalDateTime.of(2026, 10, 9, 15, 0);

    @Test
    void objectiveObservationsAreInvisibleByDefault() {
        MemoryFactRequest snapshot = ObjectiveFactKeys.observation(
                "strategy:1", ObjectiveFactKeys.PAPER_EQUITY, "100000", AT);
        assertFalse(snapshot.getUserVisible(), "快照遥测不该进用户门面");
        assertEquals("observation", snapshot.getFactType());
        assertEquals("system", snapshot.getProvenance());
    }

    @Test
    void runStoryIsVisibleDecision() {
        MemoryFactRequest story = ObjectiveFactKeys.observationStory(
                "strategy:1", "回测结论：总收益 5.41%", AT);
        assertTrue(story.getUserVisible());
        assertEquals("decision", story.getFactType());
        assertEquals("run_summary", story.getPredicate());
        // 非法输入返回 null（调用方跳过），不做宽容修补 —— 与 observation 同一约定
        assertNull(ObjectiveFactKeys.observationStory("strategy:1", "  ", AT));
        assertNull(ObjectiveFactKeys.observationStory("", "x", AT));
    }

    @Test
    void telemetryPredicateSetMatchesObjectiveContractKeys() {
        // 分类集合必须覆盖标记区里的全部遥测键 —— 漏一个就会出现
        // "某个遥测谓词仍然可见"的漏网（集合由 ObjectiveFactKeys 单一定义，这里钉数量与代表键）
        assertTrue(ObjectiveFactKeys.TELEMETRY_PREDICATES.contains("backtest_total_return_pct"));
        assertTrue(ObjectiveFactKeys.TELEMETRY_PREDICATES.contains("paper_equity"));
        assertTrue(ObjectiveFactKeys.TELEMETRY_PREDICATES.contains("verify_avg_excess_pct"));
        assertTrue(ObjectiveFactKeys.TELEMETRY_PREDICATES.contains("expectation_status"));
        assertFalse(ObjectiveFactKeys.TELEMETRY_PREDICATES.contains("run_summary"),
                "叙事不是遥测");
    }

    // ==================== 里程碑（模拟盘） ====================

    @Test
    void positionMilestoneDetectsFirstFillAndFlat() {
        assertEquals("模拟盘首笔成交，开始持仓",
                PaperTradingService.positionMilestone("0", new java.math.BigDecimal("100")));
        assertEquals("模拟盘首笔成交，开始持仓",
                PaperTradingService.positionMilestone(null, new java.math.BigDecimal("100")),
                "无历史记录视为空仓：第一笔就是首笔成交");
        assertEquals("模拟盘已清仓",
                PaperTradingService.positionMilestone("100", java.math.BigDecimal.ZERO));
        assertNull(PaperTradingService.positionMilestone("100", new java.math.BigDecimal("200")),
                "加仓不是里程碑");
        assertNull(PaperTradingService.positionMilestone("0", java.math.BigDecimal.ZERO),
                "空仓到空仓无事发生");
        assertNull(PaperTradingService.positionMilestone("垃圾值", java.math.BigDecimal.ZERO),
                "脏历史值按空仓处理，不炸");
    }

    // ==================== 聚合叙事（回测/验证） ====================

    @Test
    void backtestStoryFromMetrics() {
        String story = StrategyService.backtestStory(MAPPER.createObjectNode()
                .put("total_return_pct", 5.41).put("buy_and_hold_return_pct", 47.88)
                .put("max_drawdown_pct", -15.57).put("trade_count", 29).put("win_rate", 28.57));
        assertTrue(story.startsWith("回测结论：总收益 5.41%"));
        assertTrue(story.contains("买入持有 47.88%"));
        assertTrue(story.contains("29 笔交易"));
    }

    @Test
    void verificationStorySeparatesNoSampleFromPoorPerformance() {
        // "没验证成"与"表现平平"必须分开 —— 混了就会把上次结论讲成这次
        String noSample = StrategyService.verificationStory(0, null);
        assertTrue(noSample.contains("没有可用样本"));

        String poor = StrategyService.verificationStory(24,
                MAPPER.createObjectNode()
                        .put("beat_buy_and_hold", 6).put("avg_excess_pct", -3.95)
                        .put("avg_cost_pct_of_capital", 1.536));
        assertTrue(poor.contains("24 个有效样本"));
        assertTrue(poor.contains("6 个跑赢买入持有"));
        assertTrue(poor.contains("平均超额 -3.95%"));
    }

    // ==================== 存量回填的叙事拼接 ====================

    @Test
    void backfillStoryPrefersVerificationOverBacktestOverPaper() {
        String verify = MemoryVisibilityBackfill.storyFromValues(Map.of(
                ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT, "24",
                ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT, "6",
                ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT, "-3.95",
                ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT, "5.41"));
        assertTrue(verify.startsWith("样本外验证"), "验证结论优先（信息量更大）");

        String backtest = MemoryVisibilityBackfill.storyFromValues(Map.of(
                ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT, "5.41",
                ObjectiveFactKeys.BACKTEST_BUY_AND_HOLD_RETURN_PCT, "47.88"));
        assertTrue(backtest.startsWith("回测结论"));

        String paper = MemoryVisibilityBackfill.storyFromValues(
                Map.of(ObjectiveFactKeys.PAPER_RETURN_PCT, "0"));
        assertTrue(paper.startsWith("模拟盘运行中"));

        assertEquals("", MemoryVisibilityBackfill.storyFromValues(Map.of()));
    }
}
