package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperAuthorization;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperAuthorizationRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 盘中盯盘 agent（事件驱动，2026-10-09）：agent 模式盘中的主决策者。
 *
 * <h3>这个文件钉住什么</h3>
 * 盯盘是"真正的老手盯盘"的落点 —— 但它一旦出错，出错方式全都不报错：
 * 规则信号被误执行/误否决、预算失效烧 token、降级路径悄悄断了导致盘中瘫痪、
 * 越界建议直接成交。这里的每条用例都对应一种真实的事故形态。
 */
@ExtendWith(MockitoExtension.class)
class PaperTradingServiceWatchTest {

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
    private org.springframework.transaction.PlatformTransactionManager transactionManager;
    @Mock
    private MemoryFactService memoryFactService;
    @Mock
    private MemoryService memoryService;
    @Mock
    private ExpectationService expectationService;
    @Mock
    private PaperReviewReportService paperReviewReportService;
    @Mock
    private PaperAuthorizationRepository paperAuthorizationRepository;
    @Mock
    private NewsBriefingService newsBriefingService;

    @InjectMocks
    private PaperTradingService paperTradingService;

    private final ObjectMapper mapper = new ObjectMapper();

    /** @Value 字段在纯 Mockito 环境不注入（默认 false/0）——盯盘测试显式打开。 */
    @BeforeEach
    void enableWatch() {
        ReflectionTestUtils.setField(paperTradingService, "intradayWatchEnabled", true);
        ReflectionTestUtils.setField(paperTradingService, "intradayWatchDailyBudget", 20);
    }

    private static final String CONFIG = "{\"initial_capital\":10000.0,"
            + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0,\"stamp_tax_pct\":0.0},"
            + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";

    private Strategy agentStrategy() {
        User user = User.builder().id(1L).username("alice").password("x")
                .email("a@b.c").build();
        return Strategy.builder().id(10L).name("盯盘").symbol("601988")
                .configJson(CONFIG).user(user).paperEnabled(true)
                .decisionMode("agent").build();
    }

    private PaperAccount flatAccount(Strategy strategy) {
        PaperAccount account = new PaperAccount();
        account.setUser(strategy.getUser());
        account.setStrategy(strategy);
        account.setInitialCapital(new BigDecimal("10000.00"));
        account.setCash(new BigDecimal("10000.00"));
        account.setShares(BigDecimal.ZERO);
        account.setAvgCost(BigDecimal.ZERO);
        account.setEquity(new BigDecimal("10000.00"));
        account.setHighWatermark(BigDecimal.ZERO);
        return account;
    }

    private PaperAuthorization openAuthorization(Strategy strategy) {
        return PaperAuthorization.builder()
                .user(strategy.getUser()).strategy(strategy)
                .intradayExecution(true).allowOpen(true).allowClose(true)
                .maxPositionPct(BigDecimal.ONE)
                .build();
    }

    /** 规则引擎响应：信号 buy + events 含 rule_signal（事件由 evaluate-bar realtime 附带）。 */
    private JsonNode ruleResultWithBuyEvent(String barTime) throws Exception {
        return mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"price_cross_ma\"],"
                        + "\"bar_time\":\"" + barTime + "\","
                        + "\"events\":[{\"type\":\"rule_signal\",\"detail\":\"规则引擎给出 buy\"}],"
                        + "\"snapshot\":{\"schema_version\":1,\"bar\":{\"date\":\"2026-10-09\",\"close\":10.0},"
                        + "\"indicators\":{},\"extra\":{}},"
                        + "\"fingerprint\":{\"fill_basis\":\"realtime_last\",\"adjust_mode\":\"qfq\","
                        + "\"money_policy_version\":1,\"engine_version\":\"abc\",\"decision_mode\":\"rule\"}}");
    }

    private JsonNode watchResponse(String action, double size) throws Exception {
        return mapper.readTree(String.format(
                "{\"valid\":true,\"action\":\"%s\",\"size_fraction\":%s,\"confidence\":0.7,"
                        + "\"reason\":\"测试判断\",\"watch_tokens\":1800,\"degraded\":false,\"warnings\":[]}",
                action, size));
    }

    /** 公共 stub 一律 lenient：成交类与留痕类用例各自只用到其中一部分。 */
    private void stubCommon(Strategy strategy, PaperAccount account) {
        lenient().when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        lenient().when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        lenient().when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.of(account));
        lenient().when(paperAuthorizationRepository.findLatest(10L))
                .thenReturn(Optional.of(openAuthorization(strategy)));
        lenient().when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(inv -> inv.getArgument(0));
        lenient().when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(inv -> inv.getArgument(0));
        lenient().when(strategyRepository.save(any(Strategy.class)))
                .thenAnswer(inv -> inv.getArgument(0));
    }

    /**
     * 未完结的 bar（标注时刻在未来）不结算。守的是 2026-10-12 的真事故：
     * 数据源在盘前预创建含竞价数据的首根 bar，结算循环 09:27:04 就"成交"了——
     * 真实市场那个时刻不可能成交。同一规则也挡住"走了一半的分钟 bar"上的信号重绘。
     */
    @Test
    void anUnfinishedBarIsNotSettled() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        String futureBar = java.time.LocalDateTime.now().plusMinutes(3)
                .format(java.time.format.DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss"));
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent(futureBar));

        paperTradingService.evaluateRealtime();

        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
        verify(paperTraceService, never()).record(any(PaperTradeTrace.class));
        assertNull(account.getLastBarTime(), "未完结 bar 不得占用 lastBarTime——否则 bar 走完后反而不结算");
    }

    /**
     * 盯盘说不动 → 规则信号被否决：不成交，留 watch_hold 痕迹（trigger=watch）。
     * 这是"agent 盘中到底工没工作"最直接的审计证据。
     */
    @Test
    void watchHoldOverrulesTheRuleSignalAndLeavesATrace() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:00:00"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), isNull(), isNull(), any()))
                .thenReturn(watchResponse("hold", 0.0));

        paperTradingService.evaluateRealtime();

        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService).record(captor.capture());
        PaperTradeTrace trace = captor.getValue();
        assertEquals(ExecutionContract.SKIP_WATCH_HOLD, trace.getSkipReason());
        assertEquals(ExecutionContract.TRIGGER_WATCH, trace.getTrigger(),
                "盯盘痕迹必须能区分'盯盘 agent 判断的'与'例行评估'");
        assertEquals(ExecutionContract.DECISION_MODE_AGENT, trace.getDecisionMode());
    }

    /** 盯盘说买（40% 仓位）→ 授权边界内直接成交，痕迹 trigger=watch、reason 带盯盘动作。 */
    @Test
    void watchBuyExecutesInsideTheAuthorizationBoundary() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:01:00"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), isNull(), isNull(), any()))
                .thenReturn(watchResponse("buy", 0.4));

        paperTradingService.evaluateRealtime();

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        assertEquals("BUY", tradeCaptor.getValue().getSide());
        assertTrue(tradeCaptor.getValue().getReason().contains("watch_buy"),
                "成交原因要能看出这是盯盘动作，实际：" + tradeCaptor.getValue().getReason());
        // size_fraction=0.4 × 现金 10000 → 400 股（整手），不是全仓
        assertEquals(0, new BigDecimal("400").compareTo(tradeCaptor.getValue().getShares()));
        ArgumentCaptor<PaperTradeTrace> traceCaptor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService).record(traceCaptor.capture());
        assertEquals(ExecutionContract.TRIGGER_WATCH, traceCaptor.getValue().getTrigger());
    }

    /** 盯盘建议越界（allow_open=false 时的 buy）→ 不执行，留 outside_authorization。 */
    @Test
    void watchBuyOutsideTheBoundaryIsBlockedNotExecuted() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        PaperAuthorization noOpen = openAuthorization(strategy);
        noOpen.setAllowOpen(false);
        when(paperAuthorizationRepository.findLatest(10L)).thenReturn(Optional.of(noOpen));
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:02:00"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), isNull(), isNull(), any()))
                .thenReturn(watchResponse("buy", 0.5));

        paperTradingService.evaluateRealtime();

        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
        ArgumentCaptor<PaperTradeTrace> captor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService).record(captor.capture());
        assertEquals(ExecutionContract.SKIP_OUTSIDE_AUTHORIZATION, captor.getValue().getSkipReason());
        assertEquals(ExecutionContract.TRIGGER_WATCH, captor.getValue().getTrigger());
    }

    /** LLM 降级（degraded）→ 盯盘不接管，回落"规则+授权"：规则 buy 信号照常执行。 */
    @Test
    void degradedWatchFallsBackToRuleExecution() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:03:00"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), isNull(), isNull(), any()))
                .thenReturn(mapper.readTree(
                        "{\"valid\":true,\"action\":\"hold\",\"degraded\":true,\"watch_tokens\":0}"));

        paperTradingService.evaluateRealtime();

        // 盯盘没问成 → 规则信号在授权内照常执行：盘中绝不因 LLM 故障瘫痪
        verify(paperTradeRepository, times(1)).save(any(PaperTrade.class));
    }

    /** 预算耗尽 → 留一行"盯盘下班"的痕 + 回落规则执行（预算是成本止损线，不是停摆）。 */
    @Test
    void exhaustedBudgetFallsBackAndLeavesATrace() throws Exception {
        ReflectionTestUtils.setField(paperTradingService, "intradayWatchDailyBudget", 0);
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:04:00"));

        paperTradingService.evaluateRealtime();

        // 预算 0：一次都不问模型
        verify(strategyClient, never()).intradayWatch(anyString(), anyString(), any(), any(), any(), any(), any(), any());
        ArgumentCaptor<PaperTradeTrace> traceCaptor = ArgumentCaptor.forClass(PaperTradeTrace.class);
        verify(paperTraceService, atLeastOnce()).record(traceCaptor.capture());
        assertTrue(traceCaptor.getAllValues().stream()
                        .anyMatch(t -> ExecutionContract.SKIP_INTRADAY_WATCH_BUDGET_EXCEEDED
                                .equals(t.getSkipReason())),
                "预算尽要留痕，否则'盯盘今天为什么没工作'无从回答");
        // 且回落规则路径：规则 buy 信号照常成交
        verify(paperTradeRepository, times(1)).save(any(PaperTrade.class));
    }

    /** 止损硬通道绕过盯盘：不等模型，直接执行（盯盘一次都不许被调）。 */
    @Test
    void stopLossBypassesTheWatchEntirely() throws Exception {
        Strategy strategy = agentStrategy();
        User user = strategy.getUser();
        PaperAccount account = new PaperAccount();
        account.setUser(user);
        account.setStrategy(strategy);
        account.setInitialCapital(new BigDecimal("10000.00"));
        account.setCash(BigDecimal.ZERO.setScale(2));
        account.setShares(new BigDecimal("1000.0000"));
        account.setAvgCost(new BigDecimal("10.0000"));
        account.setEquity(new BigDecimal("10000.00"));
        account.setHighWatermark(new BigDecimal("10.5000"));
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), any()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"sell\",\"price\":9.0,\"matched_conditions\":[\"stop_loss_pct\"],"
                                + "\"bar_time\":\"2026-10-09 10:05:00\",\"events\":[{\"type\":\"near_stop\"}]}"));

        paperTradingService.evaluateRealtime();

        verify(strategyClient, never()).intradayWatch(anyString(), anyString(), any(), any(), any(), any(), any(), any());
        verify(paperTradeRepository, times(1)).save(any(PaperTrade.class));
    }

    /** 无事件（evaluate-bar 响应 events 为空）→ 盯盘零调用，规则路径照旧。 */
    @Test
    void noEventsMeansZeroWatchCalls() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"hold\",\"price\":10.0,\"matched_conditions\":[],"
                                + "\"bar_time\":\"2026-10-09 10:06:00\",\"events\":[]}"));

        paperTradingService.evaluateRealtime();

        verify(strategyClient, never()).intradayWatch(anyString(), anyString(), any(), any(), any(), any(), any(), any());
    }

    /** 同一事件类型在冷却窗口内不重复叫醒模型（第二次 evaluateRealtime 不再调盯盘）。 */
    @Test
    void sameEventTypeIsCooledDownWithinTheWindow() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:07:00"))
                .thenReturn(ruleResultWithBuyEvent("2026-10-09 10:08:00"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), isNull(), isNull(), any()))
                .thenReturn(watchResponse("hold", 0.0));

        paperTradingService.evaluateRealtime();
        paperTradingService.evaluateRealtime();   // 同类事件 15 分钟内第二次

        verify(strategyClient, times(1)).intradayWatch(anyString(), anyString(), any(), any(), any(), any(), any(), any());
    }

    // ==================== news_shock（agent 互通 P3，2026-10-09） ====================

    /** 无盘中事件 + high impact 新闻 → news_shock 单独叫醒盯盘，事件与详情都要带上。 */
    @Test
    void newsShockWakesTheWatchWhenNoMarketEvents() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"hold\",\"price\":10.0,\"bar_time\":\"2026-10-09 10:00:00\","
                                + "\"events\":[],"
                                + "\"snapshot\":{\"schema_version\":1,\"bar\":{\"date\":\"2026-10-09\",\"close\":10.0},"
                                + "\"indicators\":{},\"extra\":{}},"
                                + "\"fingerprint\":{\"fill_basis\":\"realtime_last\",\"adjust_mode\":\"qfq\","
                                + "\"money_policy_version\":1,\"engine_version\":\"abc\",\"decision_mode\":\"rule\"}}"));
        when(newsBriefingService.latestShock(eq("601988"), anyInt()))
                .thenReturn(mapper.readTree(
                        "{\"id\":321,\"title\":\"公司被立案调查\",\"direction\":\"利空\","
                                + "\"impact_level\":\"high\",\"credibility\":82,\"analyzed\":true,"
                                + "\"plain_summary\":\"证监会立案调查公告。\"}"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), any()))
                .thenReturn(watchResponse("hold", 0.0));

        paperTradingService.evaluateRealtime();

        ArgumentCaptor<JsonNode> eventsCaptor = ArgumentCaptor.forClass(JsonNode.class);
        ArgumentCaptor<JsonNode> alertCaptor = ArgumentCaptor.forClass(JsonNode.class);
        verify(strategyClient).intradayWatch(eq(CONFIG), eq("601988"), eventsCaptor.capture(),
                isNull(), any(), alertCaptor.capture(), any(), any());
        assertEquals("news_shock", eventsCaptor.getValue().get(0).path("type").asText());
        assertEquals("利空", alertCaptor.getValue().path("direction").asText());
        verify(paperTraceService).record(any(PaperTradeTrace.class));
    }

    /** 同一条新闻短时间内不重复叫醒（类型冷却挡在第二次之前）：盯盘只被问一次。 */
    @Test
    void newsShockDoesNotWakeTheWatchTwiceForTheSameNews() throws Exception {
        Strategy strategy = agentStrategy();
        PaperAccount account = flatAccount(strategy);
        stubCommon(strategy, account);
        when(strategyClient.evaluateBarRealtime(eq(CONFIG), eq("601988"), isNull()))
                .thenReturn(mapper.readTree(
                        "{\"signal\":\"hold\",\"price\":10.0,\"bar_time\":\"2026-10-09 10:00:00\","
                                + "\"events\":[],\"snapshot\":{\"schema_version\":1,"
                                + "\"bar\":{\"date\":\"2026-10-09\",\"close\":10.0},\"indicators\":{},\"extra\":{}},"
                                + "\"fingerprint\":{\"fill_basis\":\"realtime_last\",\"adjust_mode\":\"qfq\","
                                + "\"money_policy_version\":1,\"engine_version\":\"abc\",\"decision_mode\":\"rule\"}}"));
        when(newsBriefingService.latestShock(eq("601988"), anyInt()))
                .thenReturn(mapper.readTree(
                        "{\"id\":321,\"title\":\"公司被立案调查\",\"direction\":\"利空\",\"analyzed\":true}"));
        when(strategyClient.intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), any()))
                .thenReturn(watchResponse("hold", 0.0));

        paperTradingService.evaluateRealtime();
        paperTradingService.evaluateRealtime();

        verify(strategyClient, times(1))
                .intradayWatch(eq(CONFIG), eq("601988"), any(), isNull(), any(), any(), any(), any());
    }
}
