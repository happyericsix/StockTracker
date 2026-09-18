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

    /** 规则模式（存量策略的默认）：盘中那条路会真的成交，所以"已成交"这条分支由它来触发。 */
    private Strategy ruleStrategy() {
        User user = userRepository.findAll().stream().findFirst()
                .orElseGet(() -> userRepository.save(User.builder()
                        .username("agent-it-user").password("x").email("agent-it@example.com").build()));
        return strategyRepository.save(Strategy.builder()
                .user(user)
                .name("盘中成交后的日线估值")
                .symbol("600519")
                .configJson("{\"initial_capital\":100000.0,"
                        + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                        + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}")
                .paperEnabled(true)
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
        when(strategyClient.agentDecide(anyString(), eq("600519"), anyString(), isNull(), any()))
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

    /**
     * 盘中已经成交过的当天，日线这一节**仍然要落到净值曲线上**（不许再变成一个缺口）。
     *
     * <h3>这条用例来自一次真库实测</h3>
     * 原先 {@code evaluateStrategy} 开头有一条守卫：当天已有成交就整段 {@code return null}。
     * 防重复下单是对的，但它把当天的净值快照、客观事实、到期回填、当日报告一起跳过了 ——
     * 实测里那条策略当天盘中成交、15:30 的日线结算整段跳过，于是**唯一有成交的那天
     * 在曲线上是空的**。这条用例把"只禁止下单、不禁止记录"钉在真实仓库层上。
     */
    @Test
    void anIntradayFillStillLandsOnTheEquityCurve() throws Exception {
        Strategy strategy = ruleStrategy();
        String today = LocalDate.now().toString();

        // 模拟盘中那条路的成交：账户已有 100 股，且今天已经有一笔成交记录
        PaperAccount account = paperAccountRepository.save(PaperAccount.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .initialCapital(new BigDecimal("100000.00"))
                .cash(new BigDecimal("89900.00"))
                .shares(new BigDecimal("100.0000"))
                .avgCost(new BigDecimal("10.0000"))
                .equity(new BigDecimal("90900.00"))
                .highWatermark(new BigDecimal("10.0000"))
                .lastSignal("buy")              // 盘中确实做过这个决定
                .build());
        paperTradeRepository.save(PaperTrade.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .symbol("600519")
                .side("BUY")
                .price(new BigDecimal("10.0000"))
                .shares(new BigDecimal("100.0000"))
                .amount(new BigDecimal("1000.00"))
                .tradeDate(LocalDate.now())
                .reason("price_above")
                .build());

        // 当日收盘 11.00：重估后净值 = 89900 + 100 × 11 = 91000
        when(strategyClient.evaluateBar(anyString(), eq("600519"), anyString(), any()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"buy\",\"price\":11.0,\"matched_conditions\":[\"price_above\"],"
                                + "\"snapshot\":{\"schema_version\":1,"
                                + "\"bar\":{\"date\":\"" + today + "\",\"close\":11.0},"
                                + "\"indicators\":{\"ma_20\":10.5},\"extra\":{}}}"));

        paperTradingService.evaluateDaily();

        // ① 没有第二笔成交（守卫原本的作用一点没丢）
        assertEquals(1, paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId()).size());

        // ② 当天有 daily 痕迹，且原因说的是真话
        PaperTradeTrace daily = traceRepository
                .findByStrategyIdAndTradeDateOrderByCreatedAtAsc(strategy.getId(), LocalDate.now())
                .stream()
                .filter(row -> ExecutionContract.SETTLEMENT_DAILY.equals(row.getSettlementKind()))
                .findFirst().orElseThrow();
        assertEquals(ExecutionContract.DECISION_SKIP, daily.getDecision());
        assertEquals(ExecutionContract.SKIP_ALREADY_TRADED_TODAY, daily.getSkipReason());
        assertEquals(ExecutionContract.TRIGGER_CRON, daily.getTrigger());

        // ③ 当天的净值点真的写进去了，且恒等式零容差成立 —— 这正是原来丢掉的那一格
        PaperEquitySnapshot snapshot = snapshotRepository
                .findByStrategyIdAndTradeDate(strategy.getId(), LocalDate.now()).orElseThrow();
        assertEquals(0, new BigDecimal("11.0000").compareTo(snapshot.getClosePrice()));
        assertEquals(0, new BigDecimal("91000.00").compareTo(snapshot.getEquity()));
        assertTrue(Money.equityIdentityHolds(snapshot.getCash(), snapshot.getShares(),
                snapshot.getClosePrice(), snapshot.getEquity()), "净值恒等式仍要零容差成立");

        // ④ 账户按收盘重估，但**当天真正做出的那个信号不许被估值用的响应覆盖**
        PaperAccount after = paperAccountRepository.findByStrategyId(strategy.getId()).orElseThrow();
        assertEquals(0, new BigDecimal("11.0000").compareTo(after.getLastPrice()));
        assertEquals("buy", after.getLastSignal(),
                "lastSignal 应当还是当天真正做出的那个（盘中成交时的 buy），而不是估值响应的值");
    }

    @Test
    void anAgentHoldStillLeavesATraceSoTheDayIsExplainable() throws Exception {
        Strategy strategy = agentStrategy();
        String today = LocalDate.now().toString();

        when(strategyClient.agentDecide(anyString(), eq("600519"), anyString(), isNull(), any()))
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
