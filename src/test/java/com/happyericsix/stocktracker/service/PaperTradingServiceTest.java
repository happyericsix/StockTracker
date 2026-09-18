package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PaperTradingServiceTest {

    @Mock
    private StrategyRepository strategyRepository;

    @Mock
    private PaperAccountRepository paperAccountRepository;

    @Mock
    private PaperTradeRepository paperTradeRepository;

    @Mock
    private PaperTraceService paperTraceService;

    @Mock
    private PaperEquitySnapshotRepository paperEquitySnapshotRepository;

    @Mock
    private StrategyClient strategyClient;

    @Mock
    private UserRepository userRepository;

    @Mock
    private PlatformTransactionManager transactionManager;

    @Mock
    private MemoryFactService memoryFactService;

    @Mock
    private MemoryService memoryService;

    @Mock
    private ExpectationService expectationService;

    @Mock
    private PaperReviewReportService paperReviewReportService;

    @InjectMocks
    private PaperTradingService paperTradingService;

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void buySignalCreatesTradeAndUpdatesAccount() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperAccount> accountCaptor = ArgumentCaptor.forClass(PaperAccount.class);
        verify(paperAccountRepository, times(2)).save(accountCaptor.capture());
        PaperAccount account = accountCaptor.getAllValues().get(1);
        assertTrue(account.getShares().signum() > 0);
        assertTrue(account.getCash().compareTo(account.getInitialCapital()) < 0);

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        PaperTrade trade = tradeCaptor.getValue();
        assertEquals("BUY", trade.getSide());
        assertEquals("ma_cross", trade.getReason());
        assertMoney("10", trade.getPrice());
        assertMoney("1000", trade.getShares());
    }

    /**
     * 钱的断言统一走这里：{@code BigDecimal} 比的是**值**，不是标度。
     *
     * <p>{@code assertEquals(new BigDecimal("10"), new BigDecimal("10.0000"))} 会失败
     * （equals 连标度一起比），而它们说的是同一个价格。用 {@code compareTo}
     * 才不会把"格式不同"报成"金额不同"。
     */
    private static void assertMoney(String expected, java.math.BigDecimal actual) {
        assertEquals(0, new java.math.BigDecimal(expected).compareTo(actual),
                "期望 " + expected + "，实际 " + actual);
    }

    private static void assertMoney(String expected, java.math.BigDecimal actual, String message) {
        assertEquals(0, new java.math.BigDecimal(expected).compareTo(actual),
                message + "（期望 " + expected + "，实际 " + actual + "）");
    }

    @Test
    void positiveCommissionReducesShares() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":10.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperAccount> accountCaptor = ArgumentCaptor.forClass(PaperAccount.class);
        verify(paperAccountRepository, times(2)).save(accountCaptor.capture());
        PaperAccount account = accountCaptor.getAllValues().get(1);
        assertMoney("900", account.getShares());
        assertMoney("100", account.getCash());

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        PaperTrade trade = tradeCaptor.getValue();
        assertMoney("900", trade.getShares());
        assertMoney("9000", trade.getAmount());
    }

    /**
     * 今天已经成交过（盘中那条路成交的）时：**不许再下单，但这一节必须走完**。
     *
     * <h3>这条用例守的是什么</h3>
     * 以前这里整段 return null。防重复下单是对的，但它把当天该做的四件事一起跳过了 ——
     * 净值快照、客观事实、到期回填、当日报告。后果不是报错，而是**"今天有成交"这条策略
     * 当天在净值曲线上就是一个缺口**，而那恰好是唯一值得看的一天；净值曲线又是判断
     * 策略有没有变好的主依据。真库实测过一次：盘中 15:29 成交，15:30 的日线结算整段跳过。
     *
     * <p>所以这里钉住两头：**不产生第二笔成交**，以及**当天的证据一件都不少**。
     */
    @Test
    void anIntradayFillStillGetsTheDaysValuation() throws Exception {
        String configJson = "{\"initial_capital\":100000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .build();
        PaperAccount account = PaperAccount.builder()
                .id(3L).strategy(strategy).user(user)
                .cash(new java.math.BigDecimal("74036.45"))
                .shares(new java.math.BigDecimal("100.0000"))
                .avgCost(new java.math.BigDecimal("1258.3771"))
                .highWatermark(new java.math.BigDecimal("1258.3771"))
                // 盘中成交那一刻写下的信号：估值这一节不许把它改掉（见下面的断言）
                .lastSignal("buy")
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(true);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-18");

        // 当日 bar 的收盘价 1261.07：收盘重估后净值 = 74036.45 + 100 × 1261.07 = 200143.45
        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":1261.07,\"matched_conditions\":[],"
                        + "\"snapshot\":{\"schema_version\":1,\"bar\":{\"date\":\"2026-09-18\",\"close\":1261.07},"
                        + "\"indicators\":{\"ma_20\":1250.0},\"extra\":{}}}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        // ① 不再产生第二笔成交 —— 这是原来那条守卫存在的理由，必须原样保留
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));

        // ② 当天该有的证据一件都不少
        ArgumentCaptor<PaperTradeTrace> traceCaptor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(traceCaptor.capture());
        PaperTradeTrace trace = traceCaptor.getValue();
        assertEquals(ExecutionContract.DECISION_SKIP, trace.getDecision());
        assertEquals(ExecutionContract.SKIP_ALREADY_TRADED_TODAY, trace.getSkipReason(),
                "原因必须是'今天已经动过了'，不能写成 rule_not_met —— 那天恰恰是有成交的一天");
        assertEquals(new java.math.BigDecimal("200143.45"), trace.getEquityAfter(),
                "痕迹里的净值必须是按当日收盘重估后的值");
        verify(paperEquitySnapshotRepository, times(1)).save(any(PaperEquitySnapshot.class));
        verify(memoryFactService, times(1)).recordObjective(eq(1L), anyString(), anyList());
        // ③ 账户按收盘重估并落库（不再是"一个字段都不动"）
        ArgumentCaptor<PaperAccount> accountCaptor = ArgumentCaptor.forClass(PaperAccount.class);
        verify(paperAccountRepository, times(1)).save(accountCaptor.capture());
        assertEquals(new java.math.BigDecimal("1261.0700"), accountCaptor.getValue().getLastPrice());
        assertEquals("buy", accountCaptor.getValue().getLastSignal(),
                "lastSignal 不能被估值用的规则端点覆盖 —— 它应当还是当天真正做出的那个信号");
    }

    /**
     * agent 模式下今天已成交时，**不许为了估值去开一次会**。
     *
     * <p>委员会一次约 2 万 token，而这一节的产出只是一次重估；它给出的信号也不允许执行。
     * 这条用例把"省下这次调用"变成可执行的约束 —— 否则某次重构很容易把估值路径
     * 又接回 agentDecide，账单上的表现是"什么都没变，就是贵了点"。
     */
    @Test
    void agentModeNeverCallsTheCommitteeJustToValueTheDay() throws Exception {
        String configJson = "{\"initial_capital\":100000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode("agent").decisionModeSince(LocalDate.now())
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(true);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        JsonNode result = mapper.readTree(
                "{\"signal\":\"hold\",\"price\":1261.07,\"matched_conditions\":[]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        verify(strategyClient, never()).agentDecide(anyString(), anyString(), anyString(), any(), any());
        verify(strategyClient, times(1)).evaluateBar(eq(configJson), eq("600519"), anyString(), any());
        // 估值行落库时按**策略声明的模式**标注（它不是一次决策，但必须落在同一段曲线上）
        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, captor.getValue().getDecisionMode());
    }

    @Test
    void startPaperEvaluatesImmediatelyWhenNoTradeToday() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(false)
                .build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(strategyRepository.findByIdAndUserId(10L, 1L)).thenReturn(Optional.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(strategyRepository.save(any(Strategy.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"],\"bar_time\":\"2026-09-03 10:00:00\"}");
        when(strategyClient.evaluateBarRealtime(eq(configJson), eq("AAPL"), isNull()))
                .thenReturn(result);

        PaperAccountResponse response = paperTradingService.startPaper("alice", 10L);

        assertMoney("1000", response.getShares());
        assertMoney("10", response.getLastPrice());
        assertEquals("buy", response.getLastSignal());
        assertTrue(response.getLastEvalAt() != null);
        verify(strategyClient, times(1)).evaluateBarRealtime(eq(configJson), eq("AAPL"), isNull());
    }

    /**
     * 每日结算要把账户状态记成**客观事实**（W1），而且**只在每日结算里记**。
     *
     * <p>为什么这条用例重要：实时结算由价格刷新触发（一天几十上百次）。
     * 如果那里也写事实，同一个键上一天会出现上百个值，取代链会被冲成噪声 ——
     * "净值什么时候真的变了"这个问题就再也答不出来了。所以这里同时钉住
     * "记了"和"只记一次"。
     */
    @Test
    @SuppressWarnings("unchecked")
    void dailySettlementRecordsObjectiveFacts() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");
        // 天数类事实要靠净值序列算：给它一格（单点 → 回撤不可知，"连续空仓"= 1 天）
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of(
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-17"))
                        .equity(new java.math.BigDecimal("10000.00")).cash(new java.math.BigDecimal("0.00"))
                        .shares(new java.math.BigDecimal("1000.0000"))
                        .closePrice(new java.math.BigDecimal("10.0000")).build()));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(memoryFactService, times(1)).recordObjective(eq(1L), eq("1:2026-09-17"), captor.capture());

        Map<String, MemoryFactRequest> sent = captor.getValue().stream()
                .collect(Collectors.toMap(MemoryFactRequest::getPredicate, fact -> fact));
        assertEquals("strategy:10", sent.get(ObjectiveFactKeys.PAPER_EQUITY).getSubject());
        // 整数值的浮点归一成整数：避免"值没变、取代链却多一条"的假变更
        assertEquals("10000", sent.get(ObjectiveFactKeys.PAPER_EQUITY).getObject());
        assertEquals("0", sent.get(ObjectiveFactKeys.PAPER_CASH).getObject());
        assertEquals("1000", sent.get(ObjectiveFactKeys.PAPER_SHARES).getObject());
        // 收益率口径在服务里算，不让调用方各算一遍：买满仓后净值等于本金
        assertEquals("0", sent.get(ObjectiveFactKeys.PAPER_RETURN_PCT).getObject());
        assertTrue(sent.containsKey(ObjectiveFactKeys.PAPER_LAST_EVAL_AT));
        // 由净值序列派生的两个量：这一天只有一格，回撤算不出来（**不记**，而不是记 0），
        // 但"连续空仓天数"算得出来
        assertTrue(sent.containsKey(ObjectiveFactKeys.PAPER_FLAT_DAYS));
        assertFalse(sent.containsKey(ObjectiveFactKeys.PAPER_MAX_DRAWDOWN_PCT),
                "只有一个点时回撤不可知，记成 0 会被读成「从未回撤」");
    }

    @Test
    @SuppressWarnings("unchecked")
    void theDrawdownFactAppearsOnceThereIsACurve() throws Exception {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");
        // 曲线：100000 → 120000 → 90000，峰值回落到 90000 即 -25%
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of(
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-15"))
                        .equity(new java.math.BigDecimal("100000.00")).cash(new java.math.BigDecimal("0.00"))
                        .shares(java.math.BigDecimal.ZERO).closePrice(new java.math.BigDecimal("10.0000")).build(),
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-16"))
                        .equity(new java.math.BigDecimal("120000.00")).cash(new java.math.BigDecimal("0.00"))
                        .shares(java.math.BigDecimal.ZERO).closePrice(new java.math.BigDecimal("12.0000")).build(),
                PaperEquitySnapshot.builder().tradeDate(LocalDate.parse("2026-09-17"))
                        .equity(new java.math.BigDecimal("90000.00")).cash(new java.math.BigDecimal("0.00"))
                        .shares(java.math.BigDecimal.ZERO).closePrice(new java.math.BigDecimal("9.0000")).build()));

        JsonNode result = mapper.readTree("{\"signal\":\"hold\",\"price\":9.0,\"matched_conditions\":[]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(memoryFactService, times(1)).recordObjective(eq(1L), anyString(), captor.capture());
        Map<String, MemoryFactRequest> sent = captor.getValue().stream()
                .collect(Collectors.toMap(MemoryFactRequest::getPredicate, fact -> fact));
        assertEquals("-25", sent.get(ObjectiveFactKeys.PAPER_MAX_DRAWDOWN_PCT).getObject());
        assertEquals("3", sent.get(ObjectiveFactKeys.PAPER_FLAT_DAYS).getObject());
    }

    /**
     * 取数失败时**跳过结算**，绝不能把净值砸到现金。
     *
     * <p>以前会带着 {@code price=0} 一路走到 {@code updateEquityAndHighWatermark}，
     * 于是"净值 = 现金 + 股数 × 0 = 现金" —— 一次瞬时取数失败就抹掉持仓市值，
     * 而净值现在还会写进客观事实通道被**长期记住**（假装那天的净值就是现金）。
     */
    @Test
    void missingPriceSkipsSettlementInsteadOfZeroingEquity() {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("MACD cross").symbol("AAPL")
                .configJson(configJson).user(user).paperEnabled(true).build();
        PaperAccount account = PaperAccount.builder()
                .id(5L).user(user).strategy(strategy)
                .initialCapital(new java.math.BigDecimal("10000.00")).cash(new java.math.BigDecimal("0.00"))
                .shares(new java.math.BigDecimal("1000.0000")).avgCost(new java.math.BigDecimal("10.0000"))
                .equity(new java.math.BigDecimal("12345.00")).highWatermark(new java.math.BigDecimal("12.0000")).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        ObjectNode result = mapper.createObjectNode();
        result.put("signal", "hold");
        result.putNull("price");
        result.put("error", "insufficient history data");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        assertEquals(0, new java.math.BigDecimal("12345.00").compareTo(account.getEquity()),
                "取数失败时净值不能被砸到 0");
        assertMoney("1000", account.getShares());
        assertMoney("0", account.getCash());
        assertNull(account.getLastEvalAt(), "跳过结算就不该写 lastEvalAt");
    }

    // ==================== 痕迹：每条"什么都没发生"的路都要说出原因 ====================

    /**
     * 现金不够一手：这是最容易"什么都没发生、也没人知道为什么"的一条。
     *
     * <p>真实的例子就在本项目的回测里：¥10 万本金买茅台（一手约 ¥14 万）——
     * 信号触发了 6 次，成交 0 笔，账户数字一动不动。以前这条路径只有一行 warn 日志。
     */
    @Test
    void notEnoughCashForOneLotLeavesATraceInsteadOfSilence() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();
        PaperAccount account = PaperAccount.builder()
                .id(5L).user(user).strategy(strategy)
                .initialCapital(new java.math.BigDecimal("10000.00")).cash(new java.math.BigDecimal("10000.00"))
                .shares(java.math.BigDecimal.ZERO).avgCost(java.math.BigDecimal.ZERO)
                .equity(new java.math.BigDecimal("10000.00")).highWatermark(java.math.BigDecimal.ZERO).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":1500.0,\"matched_conditions\":[\"price_cross_ma\"],"
                        + "\"fill_basis\":\"close\",\"fingerprint\":{\"engine_version\":\"abc123\","
                        + "\"adjust_mode\":\"qfq\",\"money_policy_version\":1,\"fill_basis\":\"close\"},"
                        + "\"snapshot\":{\"schema_version\":1,\"bar\":{\"date\":\"2026-09-17\",\"close\":1500.0}}}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        PaperTradeTrace trace = captor.getValue();
        assertEquals(ExecutionContract.DECISION_SKIP, trace.getDecision());
        assertEquals(ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT, trace.getSkipReason());
        // 账户一个字段都不能动
        assertMoney("10000", account.getCash());
        assertMoney("0", account.getShares());
        // 结算前后都记下来，痕迹才能回答"从什么状态到（没）什么状态"
        assertEquals(0, trace.getEquityBefore().compareTo(trace.getEquityAfter()));
        // 证据与口径一起留：事后要能看出"当时价格是 1500、按 close 成交"
        assertEquals(0, new java.math.BigDecimal("1500.0000").compareTo(trace.getBarClose()));
        assertEquals(ExecutionContract.FILL_CLOSE, trace.getFillBasis());
        assertEquals("abc123", trace.getEngineVersion());
        assertEquals(1, trace.getSnapshotSchemaVersion());
        assertTrue(trace.getSnapshotJson().contains("1500.0"), trace.getSnapshotJson());
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
    }

    /**
     * 成交那一次的痕迹必须能被**反向找到**（成交行 → 痕迹行）。
     *
     * <p>没有这条链接，界面只能把成交和痕迹并排显示，读者自己猜哪条对应哪笔。
     */
    @Test
    void aFillLinksBackToItsTrace() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        // 痕迹落库后带回了 id
        when(paperTraceService.record(any(PaperTradeTrace.class))).thenAnswer(invocation -> {
            PaperTradeTrace trace = invocation.getArgument(0);
            trace.setId(99L);
            return trace;
        });

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        assertEquals(99L, tradeCaptor.getValue().getTraceId());

        ArgumentCaptor<PaperTradeTrace> traceCaptor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(traceCaptor.capture());
        assertEquals(ExecutionContract.DECISION_BUY, traceCaptor.getValue().getDecision());
        assertNull(traceCaptor.getValue().getSkipReason(), "成交那一次不许带跳过原因");
    }

    /**
     * 引擎给的 `skip_reason` 必须被**原样采信**：`warmup` 与 `rule_not_met` 是两件事。
     *
     * <p>Java 手里没有指标值，无法自己判断"是条件不成立还是指标还没算出来"。
     * 让它去猜，痕迹里就会出现一个看起来很确定、实际是编的原因。
     */
    @Test
    void theEngineReasonIsPassedThroughNotGuessed() throws Exception {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"hold\",\"price\":10.0,\"matched_conditions\":[],"
                        + "\"decision\":\"skip\",\"skip_reason\":\"warmup\"}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.SKIP_WARMUP, captor.getValue().getSkipReason());
    }

    @Test
    void aHoldWithoutAnEngineReasonFallsBackToRuleNotMet() throws Exception {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree("{\"signal\":\"hold\",\"price\":10.0,\"matched_conditions\":[]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.SKIP_RULE_NOT_MET, captor.getValue().getSkipReason());
    }

    @Test
    void aMissingBarLeavesATraceWithNoSnapshot() throws Exception {
        // 休市/数据未出：没有证据，但**口径与原因**都要留下，否则这天为什么没结算无从查起
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"hold\",\"price\":null,\"matched_conditions\":[],\"bar_date_missing\":true,"
                        + "\"skip_reason\":\"no_bar\",\"fill_basis\":\"close\",\"snapshot\":null,"
                        + "\"fingerprint\":{\"engine_version\":\"abc123\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"fill_basis\":\"close\"}}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        PaperTradeTrace trace = captor.getValue();
        assertEquals(ExecutionContract.SKIP_NO_BAR, trace.getSkipReason());
        assertNull(trace.getSnapshotJson());
        assertEquals(ExecutionContract.FILL_CLOSE, trace.getFillBasis(), "没有证据也要有口径");
    }

    @Test
    void anUnreachableEngineLeavesATraceToo() {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(strategyClient.evaluateBar(anyString(), anyString(), anyString(), isNull())).thenReturn(null);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.SKIP_DATA_UNAVAILABLE, captor.getValue().getSkipReason());
    }

    /**
     * T+1 挡单也要留痕：它是**执行层**的阻塞，不是"规则没成立"。
     *
     * <p>以前这条路径只有一行 debug 日志（默认还不打印），
     * 于是"信号出现了却没卖出去"在痕迹里完全不存在。
     */
    @Test
    void t1BlockedSellIsTracedAsABlockingSkip() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();
        PaperAccount account = PaperAccount.builder()
                .id(5L).user(user).strategy(strategy)
                .initialCapital(new java.math.BigDecimal("10000.00")).cash(new java.math.BigDecimal("0.00"))
                .shares(new java.math.BigDecimal("100.0000")).avgCost(new java.math.BigDecimal("10.0000"))
                .equity(new java.math.BigDecimal("1000.00")).highWatermark(new java.math.BigDecimal("10.0000"))
                .lastBuyBar("2026-09-17 09:35:00")
                .build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(strategyRepository.findByIdAndUserId(10L, 1L)).thenReturn(Optional.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"sell\",\"price\":11.0,\"matched_conditions\":[\"stop_loss_pct\"],"
                        + "\"bar_time\":\"2026-09-17 09:40:00\"}");
        when(strategyClient.evaluateBarRealtime(eq(configJson), eq("600519"), any()))
                .thenReturn(result);

        paperTradingService.startPaper("alice", 10L);

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        PaperTradeTrace trace = captor.getValue();
        assertEquals(ExecutionContract.SKIP_T1_BLOCKED, trace.getSkipReason());
        assertEquals(ExecutionContract.SETTLEMENT_REALTIME, trace.getSettlementKind());
        assertEquals(ExecutionContract.TRIGGER_MANUAL, trace.getTrigger(),
                "手动启动触发的首次评估要能被区分出来");
        // 没卖出去：持仓与现金都不许变
        assertMoney("100", account.getShares());
        assertMoney("0", account.getCash());
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
    }

    /**
     * 信号与账户状态对不上时，宁可留一条"状态不一致"，也不能写"规则没成立"。
     *
     * <p>编一个看起来合理的原因是这类痕迹系统最容易犯的错 —— 它会把一个真 bug
     * 藏进一堆正常行里。
     */
    @Test
    void aDailySettlementWritesExactlyOneEquityPoint() throws Exception {
        /**
         * 净值曲线是"这次结算到底把账户变成什么样"的时序记录，
         * 也是报告里"机会成本"那一行的唯一依据。所以每次日线结算必须**恰好**留下一格，
         * 且格子里的值就是结算后的账户值（不是结算前的）。
         */
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperEquitySnapshot> captor = ArgumentCaptor.forClass(PaperEquitySnapshot.class);
        verify(paperEquitySnapshotRepository, times(1)).save(captor.capture());
        PaperEquitySnapshot snapshot = captor.getValue();
        // 满仓买入后：现金 0、1000 股、收盘价 10 → 净值 10000
        assertEquals(0, new java.math.BigDecimal("0.00").compareTo(snapshot.getCash()));
        assertEquals(0, new java.math.BigDecimal("1000.0000").compareTo(snapshot.getShares()));
        assertEquals(0, new java.math.BigDecimal("10.0000").compareTo(snapshot.getClosePrice()));
        assertEquals(0, new java.math.BigDecimal("10000.00").compareTo(snapshot.getEquity()));
        // 快照必须自洽（净值 = 现金 + 股数 × 价格），否则曲线从第一天起就是错的
        assertTrue(Money.equityIdentityHolds(snapshot.getCash(), snapshot.getShares(),
                snapshot.getClosePrice(), snapshot.getEquity()));
    }

    // ==================== agent 决策：换的是谁做决定，不是执行与留痕 ====================

    /**
     * agent 模式下**必须走同一条路**：同一个结算函数、同一套痕迹/快照/客观事实。
     *
     * <p>这是这次"把决策源换成 agent"时最容易出事的地方：新写一条结算分支，
     * 于是净值的记账、痕迹、报告悄悄变成第二套 —— 而两套的差别不会报错，
     * 只会在几个月后表现为"两段曲线对不上账"。
     * 这条用例把"只换了决策源"钉成可执行的约束。
     */
    @Test
    void agentModeStillGoesThroughTheSameSettlementAndRecords() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode("agent").decisionModeSince(LocalDate.now())
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-18");
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        // agent 端点回的载荷：买 30% 仓位，带委员会成本。
        // 形状与真实响应一致：committee 在**顶层**（Java 从这里读成本），
        // 同一份记录也在 snapshot.extra 里（痕迹读它拿角色过程）。
        JsonNode result = mapper.readTree(
                "{\"valid\":true,\"decision\":\"buy\",\"signal\":\"buy\",\"size_fraction\":0.3,"
                        + "\"price\":10.0,\"matched_conditions\":[],\"fill_basis\":\"close\","
                        + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"engine_version\":\"abc123\",\"decision_mode\":\"agent\"},"
                        + "\"committee\":{\"llm_calls\":8,\"total_tokens\":33449,\"as_of\":\"2026-09-18\","
                        + "\"roles\":[{\"role\":\"bull\",\"label\":\"多头研究员\"}]},"
                        + "\"snapshot\":{\"schema_version\":1,\"bar\":{\"date\":\"2026-09-18\",\"close\":10.0},"
                        + "\"indicators\":{\"ma_20\":9.8},"
                        + "\"extra\":{\"committee\":{\"llm_calls\":8,\"total_tokens\":33449}}}}");
        when(strategyClient.agentDecide(eq(configJson), eq("600519"), anyString(), isNull(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        // ① 走的是 agent 端点，而且**没有**去调规则端点
        verify(strategyClient, times(1)).agentDecide(eq(configJson), eq("600519"), anyString(), isNull(), any());
        verify(strategyClient, never()).evaluateBar(anyString(), anyString(), anyString(), any());

        // ② 决策被执行（成交 + 账户变化）
        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        assertEquals("BUY", tradeCaptor.getValue().getSide());
        // ②' "动多大"也得是模型说的那个数：配置是 full/100%，模型说 0.3 →
        // 预算 3000 元 ÷ 10 元 = 300 股。这一条是补上来的 —— 之前执行层**根本没读**
        // size_fraction，模型说 30% 却按配置满仓买，而这种偏差不会报错，只会让
        // "agent 的仓位判断"这类结论永远无法从成交记录里验证。
        assertEquals(300, tradeCaptor.getValue().getShares().intValue(),
                "agent 给的 size_fraction 必须真的决定仓位，而不是被配置悄悄覆盖");

        // ③ 痕迹照旧写，并且**标明是 agent 做的**、成本可查
        ArgumentCaptor<PaperTradeTrace> traceCaptor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(traceCaptor.capture());
        PaperTradeTrace trace = traceCaptor.getValue();
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, trace.getDecisionMode());
        assertEquals(8, trace.getAgentLlmCalls());
        assertEquals(33449, trace.getAgentTokens());
        assertTrue(trace.getSnapshotJson().contains("committee"), "角色过程必须留在证据快照里");

        // ④ 净值快照与客观事实照旧写（"原有机制一条都不能丢"的落点）
        verify(paperEquitySnapshotRepository, times(1)).save(any(PaperEquitySnapshot.class));
        verify(memoryFactService, times(1)).recordObjective(eq(1L), anyString(), anyList());
    }

    /**
     * agent 模式下**盘中不再问规则引擎**（2026-09-18 裁决，方案 A）。
     *
     * <p>这条用例守的是一件事：换了决策来源就是换了决策者。之前盘中那条路无条件调 DSL，
     * 于是委员会当天说"不动"，盘中的 DSL 信号照样能建仓 —— 决定被覆盖，而且"agent 段"的
     * 曲线里混着 DSL 触发的成交。这种覆盖不会报错，只会让两段曲线悄悄失去可比性。
     */
    @Test
    void agentModeNeverAsksTheRuleEngineIntraday() {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode("agent").decisionModeSince(LocalDate.now())
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));

        paperTradingService.evaluateRealtime();

        // 一次都不许问：既没有 DSL 信号，也不会有任何成交
        verify(strategyClient, never()).evaluateBarRealtime(anyString(), anyString(), any());
        verify(strategyClient, never()).agentDecide(anyString(), anyString(), anyString(), any(), any());
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
        // 连账户都不该被这次盘中检查改写（净值只由每日结算更新）
        verify(paperAccountRepository, never()).save(any(PaperAccount.class));
    }

    /** 规则模式下盘中照旧走 DSL —— 上面那条守卫不能顺手把规则那条路也关掉。 */
    @Test
    void ruleModeStillAsksTheRuleEngineIntraday() throws Exception {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)   // decisionMode 未设 = 存量数据
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        // bar_time 为空 → 结算提前返回（这条用例只关心"问了没问"）
        JsonNode result = mapper.readTree("{\"signal\":\"hold\",\"price\":10.0,\"bar_time\":\"\"}");
        when(strategyClient.evaluateBarRealtime(eq(configJson), eq("600519"), any()))
                .thenReturn(result);

        paperTradingService.evaluateRealtime();

        verify(strategyClient, times(1)).evaluateBarRealtime(eq(configJson), eq("600519"), any());
    }

    /**
     * agent 响应里**没有** size_fraction 时：不许炸，也不许改变仓位口径。
     *
     * <p>这条守的是一个刚踩到的坑：读缺省值走的那个三元表达式把 {@code Double} 拆箱，
     * 于是"缺字段"这一天会抛 NPE，整笔结算失败（而且只在缺字段时才炸，平时完全看不出来）。
     * 所以这里同时钉住两头：不抛异常，且仓位口径仍由配置决定（full → 全部现金）。
     */
    @Test
    void anAgentResponseWithoutSizeFractionFallsBackToTheConfiguredPosition() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode("agent").decisionModeSince(LocalDate.now())
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(false);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-18");
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        // 整个 size_fraction 字段都不存在（旧版服务、被裁剪的响应）
        JsonNode result = mapper.readTree(
                "{\"valid\":true,\"decision\":\"buy\",\"signal\":\"buy\",\"price\":10.0,"
                        + "\"matched_conditions\":[],"
                        + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"engine_version\":\"abc123\",\"decision_mode\":\"agent\"}}");
        when(strategyClient.agentDecide(eq(configJson), eq("600519"), anyString(), isNull(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTrade> captor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(captor.capture());
        assertEquals(1000, captor.getValue().getShares().intValue(),
                "缺 size_fraction 时按配置口径（full → 全部现金 10000 ÷ 10 元 = 1000 股）");
    }

    // ==================== 每个交易日一条简报（可发现性的那一半） ====================

    /**
     * "有事才推"的日报没发时，**必须补一条简报**。
     *
     * <p>这条守的是可发现性：安静的日子什么都不发，用户那边"没消息"与"服务没在跑"
     * 就长得一模一样（实测反馈：不问 AI 都不知道有模拟盘这个功能）。
     */
    @Test
    void aQuietSettlementStillPushesTheDailyBriefing() throws Exception {
        Strategy strategy = ruleStrategyForDaily();
        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(false);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());
        when(strategyClient.evaluateBar(anyString(), eq("600519"), anyString(), any()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"hold\",\"price\":10.0,\"matched_conditions\":[],"
                                + "\"skip_reason\":\"rule_not_met\"}"));
        // 有事才推的日报：这一天没什么事 → 返回 null（替身默认就是 null）
        when(paperReviewReportService.composeDailyReport(any(), any(), any(LocalDate.class)))
                .thenReturn(null);

        paperTradingService.evaluateDaily();

        verify(paperReviewReportService, times(1))
                .composeDailyBriefing(any(), any(), any(LocalDate.class));
    }

    /** 日报已经推了的日子**不再叠一条简报**：一天一条，不能因为补漏变成两条。 */
    @Test
    void anEventfulDayDoesNotAlsoPushABriefing() throws Exception {
        Strategy strategy = ruleStrategyForDaily();
        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(false);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());
        when(strategyClient.evaluateBar(anyString(), eq("600519"), anyString(), any()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"price_above\"]}"));
        when(paperReviewReportService.composeDailyReport(any(), any(), any(LocalDate.class)))
                .thenReturn(com.happyericsix.stocktracker.entity.Message.builder().id(1L).build());

        paperTradingService.evaluateDaily();

        verify(paperReviewReportService, never())
                .composeDailyBriefing(any(), any(), any(LocalDate.class));
    }

    private static Strategy ruleStrategyForDaily() {
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        return Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson("{\"initial_capital\":10000.0,"
                        + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                        + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}")
                .user(user).paperEnabled(true)
                .build();
    }

    // ==================== 模拟盘总览：一次请求回答四个问题 ====================

    /**
     * 总览必须一次说清：**在跑什么 / 赚没赚 / 今天动没动 / 下次什么时候**。
     *
     * <p>这条用例守的是这次改动的目的本身：模拟盘的失败方式不是算错，
     * 而是用户根本不知道它在跑（实测反馈：不问 AI 都不知道有这个功能）。
     */
    @Test
    void theOverviewAnswersWhatIsRunningHowItDidAndWhenItRunsNext() {
        String configJson = "{\"initial_capital\":100000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .decisionModeSince(LocalDate.of(2026, 9, 1))
                .build();
        PaperAccount account = PaperAccount.builder()
                .id(3L).strategy(strategy).user(user)
                .initialCapital(new java.math.BigDecimal("100000.00"))
                .cash(new java.math.BigDecimal("89900.00"))
                .shares(new java.math.BigDecimal("100.0000"))
                .avgCost(new java.math.BigDecimal("10.0000"))
                .lastPrice(new java.math.BigDecimal("11.0000"))
                .equity(new java.math.BigDecimal("91000.00"))
                .lastSignal("buy")
                .lastEvalAt(LocalDate.now().atTime(15, 30))
                .build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(1L)).thenReturn(List.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of(
                PaperEquitySnapshot.builder().strategy(strategy).user(user)
                        .tradeDate(LocalDate.now().minusDays(1))
                        .cash(new java.math.BigDecimal("100000.00"))
                        .shares(new java.math.BigDecimal("0.0000"))
                        .closePrice(new java.math.BigDecimal("10.0000"))
                        .equity(new java.math.BigDecimal("100000.00")).build(),
                PaperEquitySnapshot.builder().strategy(strategy).user(user)
                        .tradeDate(LocalDate.now())
                        .cash(new java.math.BigDecimal("89900.00"))
                        .shares(new java.math.BigDecimal("100.0000"))
                        .closePrice(new java.math.BigDecimal("11.0000"))
                        .equity(new java.math.BigDecimal("91000.00")).build()));
        when(paperTraceService.count(10L)).thenReturn(3L);
        when(paperTraceService.listRecent(10L, 1)).thenReturn(List.of(PaperTradeTrace.builder()
                .id(9L).strategy(strategy).tradeDate(LocalDate.now())
                .settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                .trigger(ExecutionContract.TRIGGER_CRON)
                .decision(ExecutionContract.DECISION_SKIP)
                .skipReason(ExecutionContract.SKIP_ALREADY_TRADED_TODAY)
                .signal("buy")
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .agentLlmCalls(8).agentTokens(21457)
                .repeatCount(1)
                .createdAt(LocalDate.now().atTime(15, 30))
                .build()));
        when(expectationService.latest(1L, 10L)).thenReturn(new ExpectationService.Expectation(
                LocalDate.now().minusDays(1), "excess_vs_buy_and_hold_pct", java.math.BigDecimal.ZERO,
                LocalDate.now().plusDays(20), ExpectationService.STATUS_PENDING, null, null));

        List<com.happyericsix.stocktracker.dto.PaperOverviewResponse> overview =
                paperTradingService.getOverview("alice");

        assertEquals(1, overview.size());
        var item = overview.get(0);

        // ① 在跑什么
        assertEquals(10L, item.getStrategyId());
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, item.getDecisionMode());
        assertEquals(LocalDate.of(2026, 9, 1), item.getDecisionModeSince());
        assertTrue(item.isPaperEnabled());
        assertTrue(item.isEvaluated());
        // ② 赚没赚：持仓市值按**最新价**算（不是成本），汇总走同一份算法
        assertEquals(0, new java.math.BigDecimal("1100.00").compareTo(item.getPositionValue()));
        assertNotNull(item.getSummary());
        assertTrue(item.getSummary().hasData());
        assertEquals(0, new java.math.BigDecimal("91000.00").compareTo(item.getSummary().latestEquity()));
        // ③ 今天动没动：结论 + 一句人话 + agent 成本
        assertNotNull(item.getLastSettlement());
        assertEquals(ExecutionContract.SKIP_ALREADY_TRADED_TODAY, item.getLastSettlement().skipReason());
        assertTrue(item.getLastSettlement().sentence().contains("今天已经成交过"),
                item.getLastSettlement().sentence());
        assertEquals(Integer.valueOf(8), item.getLastSettlement().agentLlmCalls());
        assertEquals(3, item.getTraceCount());
        // ④ 下次什么时候：agent 模式不许承诺盘中评估
        assertNotNull(item.getNextEvaluation());
        assertEquals("daily", item.getNextEvaluation().kind());
        assertTrue(item.getNextEvaluation().note().contains("只在日线结算时决策"));
        assertTrue(item.getNextEvaluationNote().contains("按工作日近似"),
                "没有交易日历这件事必须写在明处：" + item.getNextEvaluationNote());
        // 预期也在：把"有没有变好"变成能判的问题
        assertNotNull(item.getExpectation());
        assertTrue(item.getExpectation().pending());
        assertTrue(item.getExpectation().sentence().contains("已登记预期"));
        // 隔离：只查这个用户自己的策略
        verify(strategyRepository, times(1)).findByUserIdOrderByUpdatedAtDesc(1L);
    }

    /**
     * 还没被评估过的账户：总览必须说"没有"，而不是拿初始资金冒充净值。
     *
     * <p>这正是实测里"看起来像坏了"的那个画面（agent 模式刚启动、盘中不会评估），
     * 所以读视图要给出可判定的 {@code evaluated=false}，让界面能解释而不是摆一串 N/A。
     */
    @Test
    void aNeverEvaluatedAccountIsFlaggedRatherThanShownAsFlat() {
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("委员会策略").symbol("600519")
                .configJson("{\"initial_capital\":1000000.0}").user(user).paperEnabled(true)
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .build();
        PaperAccount fresh = PaperAccount.builder()
                .id(4L).strategy(strategy).user(user)
                .initialCapital(new java.math.BigDecimal("1000000.00"))
                .cash(new java.math.BigDecimal("1000000.00"))
                .shares(new java.math.BigDecimal("0.0000"))
                .avgCost(new java.math.BigDecimal("0.0000"))
                .equity(new java.math.BigDecimal("1000000.00"))
                .build();   // lastEvalAt / lastPrice 都是 null

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(1L)).thenReturn(List.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(fresh));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperTraceService.count(10L)).thenReturn(0L);
        when(paperTraceService.listRecent(10L, 1)).thenReturn(List.of());

        var item = paperTradingService.getOverview("alice").get(0);

        assertFalse(item.isEvaluated(), "没评估过就是没评估过，不能靠初始资金装作有净值");
        assertNull(item.getPositionValue(), "拿不到最新价时不许用成本冒充市值");
        assertNull(item.getSummary());
        assertNull(item.getLastSettlement());
        assertNull(item.getExpectation());
        assertEquals(0, new java.math.BigDecimal("1000000.00").compareTo(item.getInitialCapital()));
    }

    @Test
    void ruleModeStillUsesTheRuleEndpoint() throws Exception {
        String configJson = "{\"initial_capital\":10000.0}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)   // decisionMode 未设 = 存量数据
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());

        JsonNode result = mapper.readTree("{\"signal\":\"hold\",\"price\":10.0,\"matched_conditions\":[]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        verify(strategyClient, times(1)).evaluateBar(eq(configJson), eq("600519"), anyString(), isNull());
        verify(strategyClient, never()).agentDecide(anyString(), anyString(), anyString(), any(), any());
        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.DECISION_MODE_RULE, captor.getValue().getDecisionMode(),
                "存量策略（decisionMode 为 null）必须按 rule 处理");
    }

    /**
     * 模型说的仓位再大，也大不过用户设置的仓位上限。
     *
     * <p>谁说了算必须只有一种答案：策略配置是用户的风险约束，模型只能在它**之内**决定大小。
     * 反过来的话，一个"最多 20% 仓位"的策略会被模型一句话变成满仓 —— 而这种越权
     * 同样不会报错，只会体现在某天的净值上。
     */
    @Test
    void theConfiguredPositionCeilingOutranksTheAgent() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"percent\",\"size_pct\":20.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .decisionMode("agent").decisionModeSince(LocalDate.now())
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-18");
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        // 模型要 50%，配置只允许 20% → 预算 2000 元 ÷ 10 元 = 200 股
        JsonNode result = mapper.readTree(
                "{\"valid\":true,\"decision\":\"buy\",\"signal\":\"buy\",\"size_fraction\":0.5,"
                        + "\"price\":10.0,\"matched_conditions\":[],"
                        + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"engine_version\":\"abc123\",\"decision_mode\":\"agent\"},"
                        + "\"committee\":{\"llm_calls\":8,\"total_tokens\":21457}}");
        when(strategyClient.agentDecide(eq(configJson), eq("600519"), anyString(), isNull(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTrade> captor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(captor.capture());
        assertEquals(200, captor.getValue().getShares().intValue(),
                "配置的仓位上限必须夹住模型的 size_fraction");
    }

    /**
     * 规则那条路**不许**被 size_fraction 影响：指纹写着 rule 时，仓位口径一点都不能变。
     *
     * <p>反过来说，一个非 agent 响应里出现 size_fraction（旧版服务、被改过的中间层）
     * 也必须当没看见 —— 否则规则的仓位口径会在别人手里悄悄变掉。
     */
    @Test
    void ruleModeIgnoresAStraySizeFraction() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());
        when(paperEquitySnapshotRepository.findByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(Optional.empty());

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"size_fraction\":0.3,\"matched_conditions\":[],"
                        + "\"fingerprint\":{\"fill_basis\":\"close\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"engine_version\":\"abc123\",\"decision_mode\":\"rule\"}}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTrade> captor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(captor.capture());
        assertEquals(1000, captor.getValue().getShares().intValue(),
                "rule 模式只认配置：full → 全部现金 10000 ÷ 10 元 = 1000 股");
    }

    /**
     * 信号与账户状态对不上时，宁可留一条"状态不一致"，也不能写"规则没成立"。
     *
     * <p>编一个看起来合理的原因是这类痕迹系统最容易犯的错 —— 它会把一个真 bug
     * 藏进一堆正常行里。
     */
    @Test
    void aSignalContradictingTheAccountIsNotDisguisedAsRuleNotMet() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0}}";
        User user = User.builder()
                .id(1L).username("alice").password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .configJson(configJson).user(user).paperEnabled(true).build();
        PaperAccount account = PaperAccount.builder()
                .id(5L).user(user).strategy(strategy)
                .initialCapital(new java.math.BigDecimal("10000.00")).cash(new java.math.BigDecimal("0.00"))
                .shares(new java.math.BigDecimal("100.0000")).avgCost(new java.math.BigDecimal("10.0000"))
                .equity(new java.math.BigDecimal("1000.00")).highWatermark(new java.math.BigDecimal("10.0000"))
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));

        // 已经持仓，却收到买入信号（结构上不该发生 → 一旦出现多半是并发或契约变更）
        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("600519"), anyString(), any()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, times(1)).record(captor.capture());
        assertEquals(ExecutionContract.SKIP_STATE_MISMATCH, captor.getValue().getSkipReason());
        assertMoney("100", account.getShares(), "不该加仓");
    }
}
