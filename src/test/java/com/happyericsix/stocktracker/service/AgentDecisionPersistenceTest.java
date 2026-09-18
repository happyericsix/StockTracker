package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.bean.override.mockito.MockitoBean;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.when;

/**
 * agent 决策**真的落在了现有结算管线的账上**吗？（真实仓库层 + 真实事务 + 真实 SQL）
 *
 * <h3>为什么必须有一条落到数据库上的用例</h3>
 * 单测里各层都是替身，它能证明"调用了同一个方法"，但不能证明"那一行真的写进去了、
 * 金额真的是定点数、decision_mode 真的存下来了"。
 * 而这次改动的核心承诺恰恰是**"换的只是谁做决定，执行与留痕一行不改"** ——
 * 这条承诺如果只写在注释里，新加一条结算分支的那天就会被悄悄破坏。
 *
 * <p>把 {@code StrategyClient} 换成替身（不让测试依赖在跑的 Python 服务），
 * 但它回的**载荷形状与真实响应一致**；其余全部是真的：仓库、事务、DECIMAL 列、唯一键。
 */
@SpringBootTest
class AgentDecisionPersistenceTest {

    @Autowired
    private PaperTradingService paperTradingService;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private StrategyRepository strategyRepository;

    @Autowired
    private PaperAccountRepository paperAccountRepository;

    @Autowired
    private PaperTradeRepository paperTradeRepository;

    @Autowired
    private PaperTradeTraceRepository traceRepository;

    @Autowired
    private PaperEquitySnapshotRepository snapshotRepository;

    @MockitoBean
    private StrategyClient strategyClient;

    private final ObjectMapper mapper = new ObjectMapper();

    private Strategy agentStrategy() {
        User user = userRepository.findAll().stream().findFirst()
                .orElseGet(() -> userRepository.save(User.builder()
                        .username("agent-it-user").password("x").email("agent-it@example.com").build()));
        return strategyRepository.save(Strategy.builder()
                .user(user)
                .name("agent 决策集成测试")
                .symbol("600519")
                .configJson("{\"initial_capital\":100000.0,"
                        + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                        + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}")
                .paperEnabled(true)
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .decisionModeSince(LocalDate.now())
                .build());
    }

    @Test
    void anAgentBuyLandsAsARealTradeTraceAndEquityPoint() throws Exception {
        Strategy strategy = agentStrategy();
        String today = LocalDate.now().toString();

        // 载荷形状与 /api/v1/agent/decide 的真实响应一致：**顶层 committee 与
        // snapshot.extra.committee 是同一份**（Java 从顶层读成本；痕迹只存快照，
        // 所以角色过程必须在快照里 —— 这条不变式由本用例钉住）。
        String committee = "{\"llm_calls\":8,\"total_tokens\":33449,\"as_of\":\"" + today + "\","
                + "\"roles\":[{\"role\":\"bull\",\"label\":\"bull-researcher\","
                + "\"sha256\":\"deadbeef0001\",\"excerpt\":\"均线多头排列\"}]}";
        when(strategyClient.agentDecide(anyString(), eq("600519"), anyString(), isNull()))
                .thenReturn(mapper.readTree(
                        "{\"valid\":true,\"decision\":\"buy\",\"signal\":\"buy\",\"size_fraction\":0.5,"
                                + "\"price\":10.0,\"matched_conditions\":[],\"fill_basis\":\"close\","
                                + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                                + "\"money_policy_version\":1,\"engine_version\":\"abc123\","
                                + "\"decision_mode\":\"agent\"},"
                                + "\"committee\":" + committee + ","
                                + "\"snapshot\":{\"schema_version\":1,"
                                + "\"bar\":{\"date\":\"" + today + "\",\"open\":10.0,\"high\":10.5,"
                                + "\"low\":9.8,\"close\":10.0,\"volume\":1000},"
                                + "\"indicators\":{\"ma_20\":9.8},"
                                + "\"params\":{\"decision_mode\":\"agent\"},"
                                + "\"extra\":{\"committee\":" + committee + "}}}"));

        paperTradingService.evaluateDaily();

        // ① 成交真的落库了，金额是定点数
        List<PaperTrade> trades = paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(
                strategy.getId());
        assertEquals(1, trades.size());
        PaperTrade trade = trades.get(0);
        assertEquals("BUY", trade.getSide());
        assertTrue(trade.getShares().compareTo(BigDecimal.ZERO) > 0);
        assertNotNull(trade.getTraceId(), "成交必须能反查到它的痕迹");

        // ② 痕迹落库，并且**标明决策来源 + agent 成本**
        PaperTradeTrace trace = traceRepository.findById(trade.getTraceId()).orElseThrow();
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, trace.getDecisionMode());
        assertEquals(Integer.valueOf(8), trace.getAgentLlmCalls());
        assertEquals(Integer.valueOf(33449), trace.getAgentTokens());
        // 注意：快照是按 JSON 存的，非 ASCII 会被序列化成反斜杠 u 开头的转义（无损 —— 前端 JSON.parse 会还原）。
        // 所以这里断言结构性的键，而不是中文原文：断言中文会让"序列化风格"这种东西把测试弄红。
        assertTrue(trace.getSnapshotJson() != null && trace.getSnapshotJson().contains("committee"),
                "角色过程要留在证据快照里，实际是：" + trace.getSnapshotJson());
        assertTrue(trace.getSnapshotJson().contains("sha256"), "角色的可对账哈希也要在");

        // ③ 净值快照照旧：曲线不会因为换了决策源而断掉
        List<PaperEquitySnapshot> series =
                snapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategy.getId());
        assertEquals(1, series.size());
        PaperEquitySnapshot snapshot = series.get(0);
        assertEquals(0, new BigDecimal("10.0000").compareTo(snapshot.getClosePrice()));
        assertTrue(Money.equityIdentityHolds(snapshot.getCash(), snapshot.getShares(),
                snapshot.getClosePrice(), snapshot.getEquity()), "净值恒等式仍要零容差成立");

        // ④ 账户也真的动了
        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElseThrow();
        assertTrue(account.getShares().compareTo(BigDecimal.ZERO) > 0);
        assertTrue(account.getCash().compareTo(account.getInitialCapital()) < 0);
    }

    @Test
    void anAgentHoldStillLeavesATraceSoTheDayIsExplainable() throws Exception {
        Strategy strategy = agentStrategy();
        String today = LocalDate.now().toString();

        when(strategyClient.agentDecide(anyString(), eq("600519"), anyString(), isNull()))
                .thenReturn(mapper.readTree(
                        "{\"valid\":true,\"decision\":\"skip\",\"signal\":\"hold\","
                                + "\"size_fraction\":0.0,\"skip_reason\":\"rule_not_met\","
                                + "\"price\":10.0,\"matched_conditions\":[],\"fill_basis\":\"close\","
                                + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                                + "\"money_policy_version\":1,\"engine_version\":\"abc123\","
                                + "\"decision_mode\":\"agent\"},"
                                + "\"committee\":{\"llm_calls\":8,\"total_tokens\":33000,"
                                + "\"roles\":[{\"role\":\"risk_judge\",\"label\":\"风控裁决\"}]},"
                                + "\"snapshot\":{\"schema_version\":1,"
                                + "\"bar\":{\"date\":\"" + today + "\",\"close\":10.0},"
                                + "\"indicators\":{\"ma_20\":9.8},"
                                + "\"extra\":{\"committee\":{\"llm_calls\":8,\"total_tokens\":33000}}}}"));

        paperTradingService.evaluateDaily();

        // "今天为什么没动"在 agent 模式下同样要能查：痕迹里有原因，只是没有成交
        PaperTradeTrace trace = traceRepository
                .findByStrategyIdAndTradeDateOrderByCreatedAtAsc(strategy.getId(), LocalDate.now())
                .stream().findFirst().orElseThrow();
        assertEquals(ExecutionContract.DECISION_SKIP, trace.getDecision());
        assertEquals(ExecutionContract.SKIP_RULE_NOT_MET, trace.getSkipReason());
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, trace.getDecisionMode());
        assertEquals(0, paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId()).size());
    }
}
