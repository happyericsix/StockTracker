package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.event.DecisionModeSwitchedEvent;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.context.ApplicationEventPublisher;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class StrategyServiceTest {

    @Mock
    private StrategyRepository strategyRepository;

    @Mock
    private StrategyClient strategyClient;

    @Mock
    private UserRepository userRepository;

    @Mock
    private MemoryFactService memoryFactService;

    @Mock
    private MemoryService memoryService;

    @Mock
    private ApplicationEventPublisher eventPublisher;

    @InjectMocks
    private StrategyService strategyService;

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void cannotUpdateOtherUsersStrategy() {
        User owner = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(99L, owner.getId())).thenReturn(Optional.empty());

        StrategyRequest request = new StrategyRequest("foreign", "AAPL", "{}");

        assertThrows(IllegalArgumentException.class,
                () -> strategyService.updateStrategy("alice", 99L, request));

        verifyNoInteractions(strategyClient);
    }

    // ==================== 编辑面板的守门员（2026-10-10） ====================

    private User alice() {
        return User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com").build();
    }

    private Strategy ownedStrategy(User owner) {
        return Strategy.builder()
                .id(42L).name("均线上穿").symbol("601988")
                .configJson("{\"entry\":{\"logic\":\"all\",\"conditions\":[]}}")
                .user(owner).build();
    }

    /** 改坏的规则进不了库：DSL 校验失败时拒绝保存（fail-closed），错误原文透传给用户。 */
    @Test
    void invalidConfigIsRejectedBeforeSave() {
        User owner = alice();
        Strategy strategy = ownedStrategy(owner);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, owner.getId())).thenReturn(Optional.of(strategy));
        when(strategyClient.validateConfig(any())).thenReturn(
                mapper.readTree("{\"valid\":false,\"error\":\"stop_loss_pct 的 value 必须为负数\"}"));

        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> strategyService.updateStrategy("alice", 42L,
                        new StrategyRequest("改名", null,
                                "{\"exit\":{\"conditions\":[{\"type\":\"stop_loss_pct\",\"value\":8}]}}")));

        assertTrue(ex.getMessage().contains("必须为负数"), ex.getMessage());
        verify(strategyRepository, never()).save(any());
    }

    /** 校验服务本身挂了也 fail-closed：宁可不保存，不把没验过的规则放进下一次结算。 */
    @Test
    void unavailableValidatorBlocksTheEdit() {
        User owner = alice();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, owner.getId()))
                .thenReturn(Optional.of(ownedStrategy(owner)));
        when(strategyClient.validateConfig(any())).thenThrow(new RuntimeException("python down"));

        assertThrows(IllegalArgumentException.class,
                () -> strategyService.updateStrategy("alice", 42L,
                        new StrategyRequest("改名", null, "{\"entry\":{}}")));
        verify(strategyRepository, never()).save(any());
    }

    /** 换标的被拒绝：账户、成交历史、验证结论都挂在原标的上，换票=新建策略。 */
    @Test
    void changingSymbolIsRejected() {
        User owner = alice();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, owner.getId()))
                .thenReturn(Optional.of(ownedStrategy(owner)));

        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> strategyService.updateStrategy("alice", 42L,
                        new StrategyRequest("改名", "600519", null)));
        assertTrue(ex.getMessage().contains("换票"), ex.getMessage());
        verifyNoInteractions(strategyClient);
    }

    /** 合法编辑：校验通过才落库，且 configUpdatedAt 打上口径分界点（改名不动它）。 */
    @Test
    void validEditStampsConfigUpdatedAt() {
        User owner = alice();
        Strategy strategy = ownedStrategy(owner);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, owner.getId())).thenReturn(Optional.of(strategy));
        when(strategyClient.validateConfig(any())).thenReturn(mapper.readTree("{\"valid\":true}"));
        when(strategyRepository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        String newConfig = "{\"entry\":{\"logic\":\"all\",\"conditions\":"
                + "[{\"type\":\"price_cross_ma\",\"direction\":\"above\",\"window\":30}]}}";
        var response = strategyService.updateStrategy("alice", 42L,
                new StrategyRequest("新名字", null, newConfig));

        assertEquals("新名字", response.getName());
        assertNotNull(strategy.getConfigUpdatedAt(), "规则变更必须打点（口径分界）");
        assertEquals(newConfig, strategy.getConfigJson());

        // 只改名不打点：口径分界线只属于"规则真的变了"这件事
        strategy.setConfigUpdatedAt(null);
        strategyService.updateStrategy("alice", 42L, new StrategyRequest("再改名", null, null));
        assertEquals(null, strategy.getConfigUpdatedAt());
    }

    /**
     * 回测指标必须变成**客观事实**（W1）。
     *
     * <p>这条用例钉住的是"记忆里终于有了可复算的观测"：键名、单位、以及
     * system/high/confirmed 这套标注 —— 少任何一个，这条事实在注入时都会被当成
     * "未经确认的推断"，与用户随口一说混在一起，通道的意义就没了。
     */
    @Test
    @SuppressWarnings("unchecked")
    void backtestMetricsAreRecordedAsObjectiveFacts() {
        User owner = User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(42L).name("MA cross").symbol("600519").configJson("{}").user(owner).build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 1L)).thenReturn(Optional.of(strategy));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");

        // 夹具必须与 Python 端真实契约一致：{"valid": true, "backtest": {...}} 信封。
        // 旧夹具把指标平铺在顶层，导致 recordBacktestFacts 读错层级的 bug 从未被测出。
        ObjectNode result = mapper.createObjectNode();
        ObjectNode backtest = mapper.createObjectNode();
        backtest.put("total_return_pct", 12.5);
        backtest.put("max_drawdown_pct", -18.3);
        backtest.put("trade_count", 7);
        result.put("valid", true);
        result.set("backtest", backtest);
        when(strategyClient.backtestStrategy("{}")).thenReturn(result);

        assertNotNull(strategyService.runBacktest("alice", 42L));

        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(memoryFactService).recordObjective(eq(1L), eq("1:2026-09-17"), captor.capture());

        Map<String, MemoryFactRequest> sent = captor.getValue().stream()
                .collect(Collectors.toMap(MemoryFactRequest::getPredicate, fact -> fact));
        assertEquals("12.5", sent.get(ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT).getObject());
        assertEquals("-18.3", sent.get(ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT).getObject());
        assertEquals("7", sent.get(ObjectiveFactKeys.BACKTEST_TRADE_COUNT).getObject());
        // 没出现在结果里的字段不许补 0：假的 0 会取代上一次真实的值
        // +1 = 聚合叙事（run_summary，双层记忆 2026-10-09）：用户看到的"策略结论"
        assertEquals(5, sent.size());
        assertEquals("strategy:42", sent.get(ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT).getSubject());
        MemoryFactRequest story = sent.get(ObjectiveFactKeys.RUN_SUMMARY);
        assertNotNull(story, "回测必须附一句话结论");
        assertTrue(story.getObject().contains("总收益 12.5%"));
        assertEquals("decision", story.getFactType());
        assertTrue(story.getUserVisible(), "叙事是给用户看的，必须可见");
        assertFalse(sent.get(ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT).getUserVisible(),
                "裸指标只供取代链，不进用户门面");
    }

    /**
     * 记忆写不进去，回测结果也必须照常返回。
     *
     * <p>这不是"顺手加个 try/catch"：记忆是增强功能，而回测是用户等了几秒拿到的结果。
     * 让前者把后者拖没，是这个项目里最不该犯的错（与 ChatService 写账本同一条纪律）。
     */
    @Test
    void backtestSurvivesMemoryFailure() {
        User owner = User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(42L).name("MA cross").symbol("600519").configJson("{}").user(owner).build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 1L)).thenReturn(Optional.of(strategy));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");
        when(memoryFactService.recordObjective(any(), any(), any()))
                .thenThrow(new RuntimeException("memory down"));

        ObjectNode result = mapper.createObjectNode();
        ObjectNode backtest = mapper.createObjectNode();
        backtest.put("total_return_pct", 1.0);
        result.put("valid", true);
        result.set("backtest", backtest);
        when(strategyClient.backtestStrategy("{}")).thenReturn(result);

        assertNotNull(strategyService.runBacktest("alice", 42L));
    }

    /**
     * 回测失败（valid=false，只有 error 字段）时**一条事实都不能记**：
     * 失败响应里没有指标，把 error 文本当观测写进记忆比不写更糟。
     */
    @Test
    void failedBacktestRecordsNoFacts() {
        User owner = User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(42L).name("MA cross").symbol("600519").configJson("{}").user(owner).build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 1L)).thenReturn(Optional.of(strategy));

        ObjectNode result = mapper.createObjectNode();
        result.put("valid", false);
        result.put("error", "策略回测失败，请稍后重试");
        when(strategyClient.backtestStrategy("{}")).thenReturn(result);

        assertNotNull(strategyService.runBacktest("alice", 42L));

        verify(memoryFactService, never()).recordObjective(any(), any(), any());
    }

    /**
     * 切换决策来源只在**真正变化**时广播一次（模拟盘侧据此立刻开第一次委员会）。
     *
     * <p>重复设成同一个模式是空操作：多广播一次就会多开一次会（多花一次 LLM 往返），
     * 而"确认一下"不该有任何副作用 —— 生效日也不许刷新（否则分界点往后漂）。
     */
    /** 2026-10-15：切换接口已下线（决策来源统一为 agent）——调用必须被明确拒绝。 */
    @Test
    void switchingDecisionModeIsRejectedForGood() {
        User owner = User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com").build();
        Strategy strategy = Strategy.builder()
                .id(42L).name("MA cross").symbol("600519").configJson("{}")
                .user(owner).paperEnabled(true).decisionMode("agent").build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 1L)).thenReturn(Optional.of(strategy));

        IllegalArgumentException ex = assertThrows(IllegalArgumentException.class,
                () -> strategyService.switchDecisionMode("alice", 42L, "rule"));
        assertTrue(ex.getMessage().contains("统一为多角色委员会"));
        verify(eventPublisher, never()).publishEvent(any(Object.class));
    }
}
