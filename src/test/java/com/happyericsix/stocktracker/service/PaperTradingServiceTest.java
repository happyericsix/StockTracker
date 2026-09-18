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
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
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

    @Test
    void sameDateIsIdempotent() {
        String configJson = "{\"initial_capital\":100000.0}";
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
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(true);

        paperTradingService.evaluateDaily();

        verifyNoInteractions(strategyClient);
        verify(paperAccountRepository, never()).save(any(PaperAccount.class));
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
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
