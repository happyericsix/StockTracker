package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 预期登记与回填：**让"接下来会怎样"变成到期就能验的承诺**。
 *
 * <h3>这个文件在防什么</h3>
 * 预期这种东西最容易变成自我安慰：门槛随便定、到期悄悄消失、"样本不足"被写成"未达成"
 * （或反过来，把一次数据缺口说成策略失败）。四种状态必须分得清，而且**算不出来时不许下结论**。
 */
class ExpectationServiceTest {

    private final MemoryFactService factService = mock(MemoryFactService.class);
    private final MemoryService memoryService = mock(MemoryService.class);
    private final ExpectationService service = new ExpectationService(factService, memoryService);

    private static final Long USER = 1L;
    private static final Long STRATEGY = 10L;

    private Map<String, String> capturedFacts() {
        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(factService, atLeastOnce()).recordObjective(eq(USER), anyString(), captor.capture());
        Map<String, String> values = new LinkedHashMap<>();
        captor.getAllValues().forEach(batch -> batch.forEach(
                fact -> values.put(fact.getPredicate(), fact.getObject())));
        return values;
    }

    private static org.mockito.verification.VerificationMode atLeastOnce() {
        return org.mockito.Mockito.atLeastOnce();
    }

    private static PaperEquitySnapshot point(String date, String equity, String close, double shares) {
        return PaperEquitySnapshot.builder()
                .tradeDate(LocalDate.parse(date))
                .equity(new BigDecimal(equity))
                .cash(new BigDecimal("0.00"))
                .shares(BigDecimal.valueOf(shares))
                .closePrice(new BigDecimal(close))
                .build();
    }

    /** 一段"账户跑输买入持有"的曲线：净值不动，标的涨了 10%。 */
    private static List<PaperEquitySnapshot> flatButRising() {
        return List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "100000.00", "10.5000", 0),
                point("2026-09-03", "100000.00", "11.0000", 0));
    }

    // ==================== 1. 登记：度量必须是封闭集里的 ====================

    @Test
    void registeringWritesTheWholeCommitmentAsFacts() {
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");

        ExpectationService.Expectation expectation = service.register(USER, STRATEGY,
                PaperEquitySeries.METRIC_EXCESS_VS_BUY_AND_HOLD, new BigDecimal("0"), 20);

        assertNotNull(expectation);
        assertEquals(ExpectationService.STATUS_PENDING, expectation.status());
        assertEquals(LocalDate.now().plusDays(20), expectation.deadline());

        Map<String, String> facts = capturedFacts();
        assertEquals("excess_vs_buy_and_hold_pct", facts.get(ObjectiveFactKeys.EXPECTATION_METRIC));
        assertEquals("0", facts.get(ObjectiveFactKeys.EXPECTATION_THRESHOLD));
        assertEquals(LocalDate.now().plusDays(20).toString(),
                facts.get(ObjectiveFactKeys.EXPECTATION_DEADLINE));
        assertEquals("pending", facts.get(ObjectiveFactKeys.EXPECTATION_STATUS));
        assertNotNull(facts.get(ObjectiveFactKeys.EXPECTATION_AT));
        // 登记**不**预先写结果：没有发生的结论不许先写进去
        assertFalse(facts.containsKey(ObjectiveFactKeys.EXPECTATION_OUTCOME));
        assertFalse(facts.containsKey(ObjectiveFactKeys.EXPECTATION_EVALUATED_AT));
    }

    @Test
    void anUnknownMetricIsRefusedInsteadOfBeingRegistered() {
        /** 一个算不出来的预期比没有预期更糟：它会在到期那天悄悄消失。 */
        assertNull(service.register(USER, STRATEGY, "predicted_price", new BigDecimal("1500"), 20));
        assertNull(service.register(USER, STRATEGY, "", new BigDecimal("0"), 20));
        assertNull(service.register(USER, STRATEGY, "return_pct", null, 20));
        verify(factService, never()).recordObjective(any(), anyString(), anyList());
    }

    @Test
    void theMetricSetHasNoPricePredictionInIt() {
        /** 本项目第一条原则：可以承诺账户层面的可验证结果，不可以承诺股价会到哪。 */
        for (String metric : PaperEquitySeries.METRICS) {
            assertTrue(metric.endsWith("_pct"), metric);
            assertFalse(metric.contains("price_"), metric);
            assertFalse(metric.contains("target"), metric);
        }
        assertEquals(3, PaperEquitySeries.METRICS.size());
    }

    @Test
    void anAbsurdHorizonIsClamped() {
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");
        assertNotNull(service.register(USER, STRATEGY, PaperEquitySeries.METRIC_RETURN,
                BigDecimal.ZERO, 100000));
        Map<String, String> facts = capturedFacts();
        assertTrue(LocalDate.parse(facts.get(ObjectiveFactKeys.EXPECTATION_DEADLINE))
                .isBefore(LocalDate.now().plusDays(500)), "不能登记一个十年后的预期");
    }

    // ==================== 2. 回填：到期才结算，且三种结果分得清 ====================

    @Test
    void aPendingExpectationBeforeItsDeadlineIsNotEvaluated() {
        // 登记于今天、20 天后到期 → 今天不该下结论
        when(factService.activeFactValues(eq(USER), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_AT, LocalDate.now() + "T00:00:00",
                        ObjectiveFactKeys.EXPECTATION_METRIC, PaperEquitySeries.METRIC_RETURN,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0",
                        ObjectiveFactKeys.EXPECTATION_DEADLINE, LocalDate.now().plusDays(20).toString(),
                        ObjectiveFactKeys.EXPECTATION_STATUS, "pending"));

        assertNull(service.evaluate(USER, STRATEGY, flatButRising(), LocalDate.now()));
        verify(factService, never()).recordObjective(any(), anyString(), anyList());
    }

    @Test
    void anUnmetExpectationIsRecordedAsUnmetWithTheActualValue() {
        /** 账户一直空仓、标的涨 10% → 超额 -10%，门槛 0 → **未达成**。
         *  这正是"不动也要被记账"落到承诺上的样子。 */
        when(factService.activeFactValues(eq(USER), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_AT, "2026-09-01T00:00:00",
                        ObjectiveFactKeys.EXPECTATION_METRIC,
                        PaperEquitySeries.METRIC_EXCESS_VS_BUY_AND_HOLD,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0",
                        ObjectiveFactKeys.EXPECTATION_DEADLINE, "2026-09-03",
                        ObjectiveFactKeys.EXPECTATION_STATUS, "pending"));
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");

        ExpectationService.Expectation evaluated =
                service.evaluate(USER, STRATEGY, flatButRising(), LocalDate.parse("2026-09-03"));

        assertNotNull(evaluated);
        assertEquals(ExpectationService.STATUS_UNMET, evaluated.status());
        assertEquals(0, new BigDecimal("-10").compareTo(evaluated.outcome()));
        Map<String, String> facts = capturedFacts();
        assertEquals("unmet", facts.get(ObjectiveFactKeys.EXPECTATION_STATUS));
        assertEquals("-10", facts.get(ObjectiveFactKeys.EXPECTATION_OUTCOME));
        assertNotNull(facts.get(ObjectiveFactKeys.EXPECTATION_EVALUATED_AT));
    }

    @Test
    void aMetExpectationSaysMet() {
        when(factService.activeFactValues(eq(USER), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_AT, "2026-09-01T00:00:00",
                        ObjectiveFactKeys.EXPECTATION_METRIC, PaperEquitySeries.METRIC_RETURN,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0",
                        ObjectiveFactKeys.EXPECTATION_DEADLINE, "2026-09-03",
                        ObjectiveFactKeys.EXPECTATION_STATUS, "pending"));
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");

        // 账户上涨 20%（100000 → 120000），门槛 0 → 达成
        List<PaperEquitySnapshot> rising = List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-03", "120000.00", "12.0000", 0));
        ExpectationService.Expectation evaluated =
                service.evaluate(USER, STRATEGY, rising, LocalDate.parse("2026-09-03"));

        assertEquals(ExpectationService.STATUS_MET, evaluated.status());
        assertEquals(0, new BigDecimal("20").compareTo(evaluated.outcome()));
    }

    @Test
    void tooLittleEvidenceIsUnmeasurableNotUnmet() {
        /** **最重要的一条**：数据不够 ≠ 策略没做到。
         *  混起来会让一次数据缺口被读成"这条策略失败"。 */
        when(factService.activeFactValues(eq(USER), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_AT, "2026-09-01T00:00:00",
                        ObjectiveFactKeys.EXPECTATION_METRIC, PaperEquitySeries.METRIC_RETURN,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0",
                        ObjectiveFactKeys.EXPECTATION_DEADLINE, "2026-09-03",
                        ObjectiveFactKeys.EXPECTATION_STATUS, "pending"));
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");

        // 只有一格快照：一个点谈不上"期间表现"
        ExpectationService.Expectation evaluated = service.evaluate(USER, STRATEGY,
                List.of(point("2026-09-03", "100000.00", "10.0000", 0)),
                LocalDate.parse("2026-09-03"));

        assertEquals(ExpectationService.STATUS_UNMEASURABLE, evaluated.status());
        assertNull(evaluated.outcome());
        Map<String, String> facts = capturedFacts();
        assertEquals("unmeasurable", facts.get(ObjectiveFactKeys.EXPECTATION_STATUS));
        assertFalse(facts.containsKey(ObjectiveFactKeys.EXPECTATION_OUTCOME),
                "算不出来就不写数字 —— 写 0 会被读成'刚好达标'");
    }

    @Test
    void anAlreadyEvaluatedExpectationIsNotReEvaluated() {
        when(factService.activeFactValues(eq(USER), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_AT, "2026-09-01T00:00:00",
                        ObjectiveFactKeys.EXPECTATION_METRIC, PaperEquitySeries.METRIC_RETURN,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0",
                        ObjectiveFactKeys.EXPECTATION_DEADLINE, "2026-09-03",
                        ObjectiveFactKeys.EXPECTATION_STATUS, "met"));

        assertNull(service.evaluate(USER, STRATEGY, flatButRising(), LocalDate.parse("2026-09-04")));
        verify(factService, never()).recordObjective(any(), anyString(), anyList());
    }

    @Test
    void theDeadlineIsMeasuredFromTheRegistrationDateNotFromToday() {
        /** 度量区间必须从**登记日**起算；从今天起算会给出一个越来越好看的假数字。 */
        List<PaperEquitySnapshot> series = new ArrayList<>(List.of(
                point("2026-09-01", "100000.00", "10.0000", 0),
                point("2026-09-02", "90000.00", "9.0000", 0)));     // 这段亏了 10%
        series.add(point("2026-09-10", "108000.00", "10.8000", 0)); // 之后才涨回来

        BigDecimal fromRegistration = PaperEquitySeries.metricValue(
                series, PaperEquitySeries.METRIC_RETURN, LocalDate.parse("2026-09-01"));
        BigDecimal fromLater = PaperEquitySeries.metricValue(
                series, PaperEquitySeries.METRIC_RETURN, LocalDate.parse("2026-09-02"));

        assertEquals(0, new BigDecimal("8").compareTo(fromRegistration));
        assertEquals(0, new BigDecimal("20").compareTo(fromLater));
        assertNotNull(fromRegistration);
    }

    // ==================== 3. 读取与呈现 ====================

    @Test
    void noExpectationEverMeansNullNotAnEmptyCommitment() {
        when(factService.activeFactValues(eq(USER), anyString(), anyList())).thenReturn(Map.of());
        assertNull(service.latest(USER, STRATEGY));
    }

    @Test
    void theSentenceDistinguishesAllThreeOutcomes() {
        String pending = ExpectationService.describe(new ExpectationService.Expectation(
                LocalDate.parse("2026-09-01"), PaperEquitySeries.METRIC_RETURN, BigDecimal.ZERO,
                LocalDate.parse("2026-09-20"), ExpectationService.STATUS_PENDING, null, null));
        String met = ExpectationService.describe(new ExpectationService.Expectation(
                LocalDate.parse("2026-09-01"), PaperEquitySeries.METRIC_EXCESS_VS_BUY_AND_HOLD,
                BigDecimal.ZERO, LocalDate.parse("2026-09-20"), ExpectationService.STATUS_MET,
                new BigDecimal("3.2"), null));
        String unmeasurable = ExpectationService.describe(new ExpectationService.Expectation(
                LocalDate.parse("2026-09-01"), PaperEquitySeries.METRIC_RETURN, BigDecimal.ZERO,
                LocalDate.parse("2026-09-20"), ExpectationService.STATUS_UNMEASURABLE, null, null));

        assertTrue(pending.contains("尚未到期"), pending);
        assertTrue(met.contains("达成") && met.contains("3.2"), met);
        assertTrue(unmeasurable.contains("算不出来") && unmeasurable.contains("不等于未达成"),
                unmeasurable);
        assertFalse(unmeasurable.contains("未达成："), "算不出来不能被说成未达成");
    }

    @Test
    void evaluationNeverThrowsEvenIfTheFactChannelIsDown() {
        when(factService.activeFactValues(eq(USER), anyString(), anyList()))
                .thenThrow(new RuntimeException("db down"));
        assertNull(service.evaluate(USER, STRATEGY, flatButRising(), LocalDate.now()));

        when(factService.activeFactValues(eq(USER), anyString(), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.EXPECTATION_METRIC, PaperEquitySeries.METRIC_RETURN,
                        ObjectiveFactKeys.EXPECTATION_THRESHOLD, "0"));
        when(memoryService.currentSessionKey(USER)).thenReturn("1:x");
        when(factService.recordObjective(any(), anyString(), anyList()))
                .thenThrow(new RuntimeException("db down"));
        // 到期了、写不进去 → 仍然不抛
        assertNull(service.evaluate(USER, STRATEGY, flatButRising(), LocalDate.now().plusDays(2)));
        assertNotNull(service.latest(USER, STRATEGY), "读失败不该让 latest 抛异常");
    }

    @Test
    void registeringTwiceSupersedesTheOldOneThroughTheFactChain() {
        /** 复用事实通道的直接好处：**取代链免费给了历史** ——
         *  "上一次预期是什么、有没有达成"是一次历史查询，不需要新表。 */
        when(memoryService.currentSessionKey(USER)).thenReturn("1:2026-09-18");

        service.register(USER, STRATEGY, PaperEquitySeries.METRIC_RETURN, BigDecimal.ZERO, 20);
        service.register(USER, STRATEGY, PaperEquitySeries.METRIC_MAX_DRAWDOWN,
                new BigDecimal("-8"), 40);

        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(factService, times(2)).recordObjective(eq(USER), anyString(), captor.capture());
        assertEquals("return_pct",
                captor.getAllValues().get(0).stream()
                        .filter(f -> f.getPredicate().equals(ObjectiveFactKeys.EXPECTATION_METRIC))
                        .findFirst().orElseThrow().getObject());
        assertEquals("max_drawdown_pct",
                captor.getAllValues().get(1).stream()
                        .filter(f -> f.getPredicate().equals(ObjectiveFactKeys.EXPECTATION_METRIC))
                        .findFirst().orElseThrow().getObject());
        assertEquals("-8",
                captor.getAllValues().get(1).stream()
                        .filter(f -> f.getPredicate().equals(ObjectiveFactKeys.EXPECTATION_THRESHOLD))
                        .findFirst().orElseThrow().getObject());
        // 两次都写在同一批键上：取代链会自然保留"上一次是什么"
        assertTrue(captor.getAllValues().stream().allMatch(batch -> batch.stream().anyMatch(
                f -> f.getPredicate().equals(ObjectiveFactKeys.EXPECTATION_DEADLINE))));
    }
}
