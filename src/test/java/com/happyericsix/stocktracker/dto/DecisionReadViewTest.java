package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.service.ExecutionContract;
import com.happyericsix.stocktracker.service.ExpectationService;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 读视图必须**说出这一条是谁做的、花了多少**。
 *
 * <h3>为什么单为一个字段写用例</h3>
 * 决策来源与 agent 成本的失败方式是**静默**的：字段缺了，界面读不到，
 * 就会回落成默认值"规则引擎" —— 于是委员会做的决定在界面上显示成规则做的，
 * 而两条路的结论含义完全不同（一个是 DSL 条件成立，一个是 8 个角色吵出来的）。
 * 这种错不会有任何报错，只会让"这段曲线是谁做出来的"永远说不清。
 *
 * <p>所以这里钉住三件事：字段在、存量数据被归一化成 {@code rule}、没登记的预期返回 null
 * （"没登记"不能被渲染成一条空承诺）。
 */
class DecisionReadViewTest {

    private static Strategy strategy(String decisionMode) {
        return Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson("{}").paperEnabled(true)
                .decisionMode(decisionMode)
                .decisionModeSince(LocalDate.of(2026, 9, 1))
                .build();
    }

    @Test
    void strategyReadViewCarriesTheDecisionSource() {
        StrategyResponse agent = StrategyResponse.from(strategy(ExecutionContract.DECISION_MODE_AGENT));
        assertNotNull(agent);
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, agent.getDecisionMode());
        assertEquals(LocalDate.of(2026, 9, 1), agent.getDecisionModeSince(),
                "生效日必须一起出去：曲线从哪天分成两段，界面与报告都得能说出来");
    }

    /**
     * 存量策略（{@code decisionMode} 为 null）在**读视图里**就是 rule，不是 null。
     *
     * <p>null 会让每个调用方各自解释一次，而"缺字段"和"规则"在界面上必须长得一样：
     * 它们本来就是同一件事（没有 agent 之前，所有决策都是规则做的）。
     */
    @Test
    void legacyStrategiesAreReadViewRuleNotNull() {
        StrategyResponse legacy = StrategyResponse.from(strategy(null));
        assertNotNull(legacy);
        assertEquals(ExecutionContract.DECISION_MODE_RULE, legacy.getDecisionMode());
    }

    @Test
    void nullEntityDoesNotBecomeAnEmptyView() {
        assertNull(StrategyResponse.from(null));
    }

    @Test
    void traceReadViewCarriesDecisionSourceAndAgentCost() {
        PaperTradeTrace trace = PaperTradeTrace.builder()
                .id(7L)
                .strategy(strategy(ExecutionContract.DECISION_MODE_AGENT))
                .symbol("600519")
                .settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                .tradeDate(LocalDate.of(2026, 9, 18))
                .decision(ExecutionContract.DECISION_BUY)
                .barClose(new BigDecimal("1341.99"))
                .fillBasis(ExecutionContract.FILL_CLOSE)
                .equityAfter(new BigDecimal("10012.34"))
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .agentLlmCalls(8)
                .agentTokens(21457)
                .build();

        PaperTradeTraceResponse response = PaperTradeTraceResponse.from(trace);
        assertNotNull(response);
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, response.getDecisionMode());
        assertEquals(8, response.getAgentLlmCalls());
        assertEquals(21457, response.getAgentTokens());
        // 一句人话照旧要有（读视图的两层：能算的 + 能读的）
        assertTrue(response.getSummary().contains("成交：买入"), response.getSummary());
    }

    @Test
    void expectationReadViewExplainsItsStatusInWords() {
        ExpectationService.Expectation pending = new ExpectationService.Expectation(
                LocalDate.of(2026, 9, 18), "excess_vs_buy_and_hold_pct", BigDecimal.ZERO,
                LocalDate.of(2026, 10, 16), ExpectationService.STATUS_PENDING, null, null);
        ExpectationResponse response = ExpectationResponse.from(pending);
        assertNotNull(response);
        assertTrue(response.pending());
        assertEquals("相对买入持有的超额", response.metricLabel(),
                "度量的措辞只有一处（ExpectationService.label），读视图不能自己再写一遍");
        assertTrue(response.sentence().contains("尚未到期"), response.sentence());

        // 到期但算不出来：**不是**未达成 —— 这两种状态在界面上必须能分开
        ExpectationService.Expectation unmeasurable = new ExpectationService.Expectation(
                LocalDate.of(2026, 9, 18), "excess_vs_buy_and_hold_pct", BigDecimal.ZERO,
                LocalDate.of(2026, 10, 16), ExpectationService.STATUS_UNMEASURABLE, null,
                LocalDateTime.of(2026, 10, 16, 16, 30));
        assertTrue(ExpectationResponse.from(unmeasurable).sentence().contains("算不出来"));
    }

    @Test
    void noExpectationIsNullNotAnEmptyPromise() {
        assertNull(ExpectationResponse.from(null));
    }
}
