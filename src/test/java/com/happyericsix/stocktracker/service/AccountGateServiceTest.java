package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperAuthorization;
import com.happyericsix.stocktracker.entity.PaperPositionLot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperAuthorizationRepository;
import com.happyericsix.stocktracker.repository.PaperPositionLotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.test.util.ReflectionTestUtils;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 账户守门员（ACCOUNT-GATE 阶段1/2）的单元测试：
 * 面板组装（持仓/风控线距离/授权/T+1 锁定/叠加度）、唤醒源检测（电平触发四源）、
 * 冷却与预算。对着 601988 事故的数字写断言（成本 7.17、止损 -5%、现价 6.9x）。
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class AccountGateServiceTest {

    @Mock
    private StrategyRepository strategyRepository;
    @Mock
    private PaperAccountRepository paperAccountRepository;
    @Mock
    private PaperAuthorizationRepository paperAuthorizationRepository;
    @Mock
    private PaperTradeRepository paperTradeRepository;
    @Mock
    private PaperPositionLotRepository lotRepository;
    @Mock
    private PortfolioService portfolioService;
    @Mock
    private StrategyClient strategyClient;

    private AccountGateService service;
    private final ObjectMapper mapper = new ObjectMapper();

    private static final String CONFIG = "{\"symbol\":\"601988\","
            + "\"entry\":{\"logic\":\"all\",\"conditions\":[]},"
            + "\"exit\":{\"logic\":\"any\",\"conditions\":["
            + "{\"type\":\"price_cross_ma\",\"direction\":\"below\",\"window\":10},"
            + "{\"type\":\"stop_loss_pct\",\"value\":-5.0}]},"
            + "\"position\":{\"type\":\"percent\",\"size_pct\":100.0}}";

    @BeforeEach
    void setUp() {
        service = new AccountGateService(strategyRepository, paperAccountRepository,
                paperAuthorizationRepository, paperTradeRepository, lotRepository,
                portfolioService, strategyClient);
        ReflectionTestUtils.setField(service, "enabled", true);
        ReflectionTestUtils.setField(service, "shadow", true);
        ReflectionTestUtils.setField(service, "allowOpen", false);
        ReflectionTestUtils.setField(service, "dailyBudget", 10);
        ReflectionTestUtils.setField(service, "cooldownMs", 900000L);
        ReflectionTestUtils.setField(service, "nearStopPct", new BigDecimal("1.0"));
        ReflectionTestUtils.setField(service, "concentrationPct", new BigDecimal("30"));
    }

    private User user() {
        return User.builder().id(1L).username("alice").password("x").email("a@b.c").build();
    }

    private Strategy holdingStrategy() {
        return Strategy.builder().id(10L).name("601988·上穿MA2→下穿MA10或止损-5%·全仓")
                .symbol("601988").configJson(CONFIG).user(user()).paperEnabled(true)
                .decisionMode("agent").build();
    }

    /** 601988 事故的持仓快照：成本 7.17、现价 6.90（-3.77%，距 -5% 止损线 1.23pt）。 */
    private PaperAccount holdingAccount(Strategy strategy) {
        PaperAccount account = new PaperAccount();
        account.setUser(strategy.getUser());
        account.setStrategy(strategy);
        account.setInitialCapital(new BigDecimal("100000.00"));
        account.setCash(new BigDecimal("80000.00"));
        account.setShares(new BigDecimal("2800"));
        account.setAvgCost(new BigDecimal("7.17"));
        account.setEquity(new BigDecimal("99320.00"));
        account.setHighWatermark(new BigDecimal("7.17"));
        account.setLastPrice(new BigDecimal("6.90"));
        return account;
    }

    private void mockHoldingPortfolio() {
        Strategy strategy = holdingStrategy();
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(1L))
                .thenReturn(List.of(strategy));
        when(paperAccountRepository.findByStrategyId(10L))
                .thenReturn(Optional.of(holdingAccount(strategy)));
        when(lotRepository.findByStrategyIdAndRemainingSharesGreaterThanOrderByOpenedDateAscIdAsc(
                eq(10L), any(BigDecimal.class)))
                .thenReturn(List.of());
        when(portfolioService.dayDrawdownPct(1L)).thenReturn(-1.0);
        when(portfolioService.haltThresholdPct()).thenReturn(new BigDecimal("5.0"));
    }

    @Test
    void panelContainsPositionWithStopDistance() {
        mockHoldingPortfolio();
        ObjectNode panel = service.buildAccountPanel(1L);
        var position = panel.path("account").path("positions").iterator().next();
        assertThat(position.path("symbol").asText()).isEqualTo("601988");
        assertThat(position.path("unrealized_pnl_pct").asText()).isEqualTo("-3.77");
        // 距 -5% 止损线：-3.77 - (-5.0) = 1.23 个百分点（near_stop 的判定输入）
        assertThat(position.path("stop_lines").path("distance_to_stop_pct").asText())
                .isEqualTo("1.23");
        assertThat(panel.path("account").path("same_symbol_exposure_pct")
                .path("601988").asDouble()).isGreaterThan(19.0);
    }

    @Test
    void nearStopFiresWhenWithinThreshold() {
        mockHoldingPortfolio();
        ObjectNode panel = service.buildAccountPanel(1L);
        // 距离 1.23pt > 1.0：此刻不唤醒
        assertThat(service.detectRiskWakes(panel)).isEmpty();
        // 现价跌到 6.90 → 距离 1.23；再压到 6.88（-4.04%，距离 0.96）→ near_stop 唤醒
        PaperAccount account = paperAccountRepository.findByStrategyId(10L).orElseThrow();
        account.setLastPrice(new BigDecimal("6.88"));
        ObjectNode closer = service.buildAccountPanel(1L);
        var wakes = service.detectRiskWakes(closer);
        assertThat(wakes).anyMatch(w -> "near_stop".equals(w.get("source")));
    }

    @Test
    void drawdownWarningFiresAtHalfOfHalt() {
        mockHoldingPortfolio();
        when(portfolioService.dayDrawdownPct(1L)).thenReturn(-2.6);   // 熔断 5.0 的一半是 2.5
        ObjectNode panel = service.buildAccountPanel(1L);
        assertThat(service.detectRiskWakes(panel))
                .anyMatch(w -> "drawdown_warning".equals(w.get("source")));
    }

    @Test
    void authExpiredFiresForStaleAuthorization() {
        mockHoldingPortfolio();
        PaperAuthorization stale = PaperAuthorization.builder()
                .user(user()).strategy(holdingStrategy())
                .intradayExecution(true).allowOpen(true).allowClose(true)
                .maxPositionPct(BigDecimal.ONE)
                .build();
        stale.setIssuedAt(java.time.LocalDateTime.now().minusDays(4));   // 持仓 TTL=3 天
        when(paperAuthorizationRepository.findFirstByStrategyIdOrderByIssuedAtDesc(10L))
                .thenReturn(Optional.of(stale));
        ObjectNode panel = service.buildAccountPanel(1L);
        var position = panel.path("account").path("positions").iterator().next();
        assertThat(position.path("authorization").path("stale").asBoolean()).isTrue();
        assertThat(service.detectRiskWakes(panel))
                .anyMatch(w -> "auth_expired".equals(w.get("source")));
    }

    @Test
    void decideRespectsCooldownAndBudget() {
        mockHoldingPortfolio();
        ObjectNode payload = mapper.createObjectNode();
        payload.putObject("account");
        ArrayNode wake = mapper.createArrayNode();
        String signature = "signal:buy:price_cross_ma";

        when(strategyClient.accountGate(any(), anyLong()))
                .thenReturn(mapper.createObjectNode().put("degraded", false));

        AccountGateService.GateCall first = service.decide(1L, payload, wake, signature);
        assertThat(first.status()).isEqualTo("ok");
        // 同签名冷却窗内：第二次直接 cooldown，不打 LLM
        assertThat(service.decide(1L, payload, wake, signature).status()).isEqualTo("cooldown");
        // 不同签名不受冷却影响，但预算烧完（10 次）后一律 budget_exceeded
        for (int i = 0; i < 9; i++) {
            assertThat(service.decide(1L, payload, wake, signature + ":v" + i).status())
                    .isEqualTo("ok");
        }
        assertThat(service.decide(1L, payload, wake, "signal:sell:other").status())
                .isEqualTo("budget_exceeded");
    }

    @Test
    void decideDegradedResponseIsUnavailable() {
        mockHoldingPortfolio();
        ObjectNode payload = mapper.createObjectNode();
        payload.putObject("account");
        when(strategyClient.accountGate(any(), anyLong()))
                .thenReturn(mapper.createObjectNode().put("degraded", true));
        AccountGateService.GateCall call = service.decide(
                1L, payload, mapper.createArrayNode(), "signal:buy:x");
        assertThat(call.status()).isEqualTo("unavailable");
        // 降级不冷却不烧预算：下一次同签名还能再问
        when(strategyClient.accountGate(any(), anyLong()))
                .thenReturn(mapper.createObjectNode().put("degraded", false));
        assertThat(service.decide(1L, payload, mapper.createArrayNode(), "signal:buy:x")
                .status()).isEqualTo("ok");
    }

    @Test
    void disabledGateReportsUnavailable() {
        ReflectionTestUtils.setField(service, "enabled", false);
        AccountGateService.GateCall call = service.decide(
                1L, mapper.createObjectNode(), mapper.createArrayNode(), "x");
        assertThat(call.status()).isEqualTo("unavailable");
    }
}
