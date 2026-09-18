package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

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
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 样本外验证（多标的 × 多时段）：**跑什么、记什么、怎么读回来**。
 *
 * <h3>为什么这三件事都要钉</h3>
 * ① 跑什么 —— 标的池必须由代码固定。"挑选样本＝挑选证据"：同一份策略换一批标的，
 *    平均超额能从 -1.35% 翻成 +1.08%（实测）。让模型或让报告临时凑一份池子，
 *    就等于允许"挑到结论为止"。
 * ② 记什么 —— 验证没跑成（没有可用样本）时**不许**用旧数字顶上去：
 *    那是把"这次没验证成"讲成上次的结论，比不记录危险得多。
 * ③ 怎么读回来 —— 读的必须是**当前有效**的那条事实（取代链末端），
 *    而不是"最新一行"（可能是已被取代的旧结论）。
 */
class StrategyVerificationTest {

    private final StrategyRepository strategyRepository = mock(StrategyRepository.class);
    private final StrategyClient strategyClient = mock(StrategyClient.class);
    private final UserRepository userRepository = mock(UserRepository.class);
    private final MemoryFactService memoryFactService = mock(MemoryFactService.class);
    private final MemoryService memoryService = mock(MemoryService.class);
    private final ObjectMapper mapper = new ObjectMapper();

    private final StrategyService service = new StrategyService(
            strategyRepository, strategyClient, userRepository, memoryFactService, memoryService);

    private static User user() {
        return User.builder().id(1L).username("alice").password("x").email("a@b.c").build();
    }

    private static Strategy strategy() {
        return Strategy.builder().id(10L).name("均线上穿").symbol("600519")
                .configJson("{\"initial_capital\":100000.0}").user(user()).build();
    }

    private JsonNode matrixResult(int validCells) throws Exception {
        return mapper.readTree("{\"valid\":true,\"adjust_mode\":\"qfq\",\"engine_version\":\"2a9dce9df73b\","
                + "\"cells_valid\":" + validCells + ",\"cells_total\":24,"
                + "\"summary\":{\"segments_valid\":" + validCells + ",\"beat_buy_and_hold\":10,"
                + "\"avg_excess_pct\":-1.35,\"avg_cost_pct_of_capital\":1.883}}");
    }

    private Map<String, MemoryFactRequest> captureFacts() {
        ArgumentCaptor<List<MemoryFactRequest>> captor = ArgumentCaptor.forClass(List.class);
        verify(memoryFactService).recordObjective(eq(1L), any(), captor.capture());
        return captor.getValue().stream()
                .collect(Collectors.toMap(MemoryFactRequest::getPredicate, fact -> fact));
    }

    // ==================== 1. 跑什么 ====================

    @Test
    void thePeerPoolIsFixedInCodeAndAlwaysIncludesTheStrategysOwnSymbol() {
        List<String> symbols = StrategyService.verificationSymbols(strategy());
        assertEquals("600519", symbols.get(0), "策略自己的标的必须排在第一个");
        assertTrue(symbols.containsAll(StrategyService.VERIFICATION_PEERS));
        assertEquals(symbols.size(), symbols.stream().distinct().count(), "不能有重复标的");

        // 换一个标的：池子不变，只有第一个位置变 —— 池子与策略无关，结论才可比
        Strategy other = Strategy.builder().id(11L).symbol("000001").user(user()).build();
        List<String> otherSymbols = StrategyService.verificationSymbols(other);
        assertEquals("000001", otherSymbols.get(0));
        assertEquals(symbols.size(), otherSymbols.size());
    }

    @Test
    void verificationAsksForAFixedRangeAndSegmentCount() {
        when(strategyRepository.findByIdAndUserId(10L, 1L)).thenReturn(Optional.of(strategy()));
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user()));
        when(strategyClient.backtestMatrix(anyString(), anyList(), anyInt(), anyString(), anyString()))
                .thenReturn(mapper.createObjectNode());
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");

        service.verifyMatrix("alice", 10L);

        ArgumentCaptor<String> start = ArgumentCaptor.forClass(String.class);
        verify(strategyClient).backtestMatrix(anyString(), anyList(),
                eq(StrategyService.VERIFICATION_SEGMENTS), start.capture(), anyString());
        LocalDate today = LocalDate.now();
        assertEquals(today.minusYears(StrategyService.VERIFICATION_YEARS).toString(), start.getValue());
    }

    // ==================== 2. 记什么 ====================

    @Test
    void aSuccessfulVerificationRecordsTheSummaryAsObjectiveFacts() throws Exception {
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");
        when(strategyClient.backtestMatrix(anyString(), anyList(), anyInt(), anyString(), anyString()))
                .thenReturn(matrixResult(21));

        service.verifyMatrix(user(), strategy());

        Map<String, MemoryFactRequest> facts = captureFacts();
        assertEquals("21", facts.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT).getObject());
        assertEquals("10", facts.get(ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT).getObject());
        assertEquals("-1.35", facts.get(ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT).getObject());
        assertEquals("1.883", facts.get(ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT).getObject());
        assertEquals("2a9dce9df73b", facts.get(ObjectiveFactKeys.VERIFY_ENGINE_VERSION).getObject());
        assertNotNull(facts.get(ObjectiveFactKeys.VERIFY_AT));
        assertEquals("strategy:10", facts.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT).getSubject());
        // 走的是客观事实通道：provenance=system / trust=high / confirmed=true
        assertEquals(ObjectiveFactKeys.PROVENANCE_SYSTEM,
                facts.get(ObjectiveFactKeys.VERIFY_AT).getProvenance());
    }

    @Test
    void aVerificationWithoutSampleDoesNotOverwriteTheRealNumbers() throws Exception {
        // 关键：只写"时间 + 样本量 0"，不写其余四个键。
        // 写了就会取代掉上一次的真实数字，于是报告把"这次没验证成"讲成上次的结论。
        when(strategyClient.backtestMatrix(anyString(), anyList(), anyInt(), anyString(), anyString()))
                .thenReturn(matrixResult(0));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");

        service.verifyMatrix(user(), strategy());

        Map<String, MemoryFactRequest> facts = captureFacts();
        assertEquals("0", facts.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT).getObject());
        assertFalse(facts.containsKey(ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT));
        assertFalse(facts.containsKey(ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT));
        assertFalse(facts.containsKey(ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT));
        assertFalse(facts.containsKey(ObjectiveFactKeys.VERIFY_ENGINE_VERSION));
    }

    @Test
    void aFailingMemoryWriteNeverBreaksTheVerification() throws Exception {
        when(strategyClient.backtestMatrix(anyString(), anyList(), anyInt(), anyString(), anyString()))
                .thenReturn(matrixResult(21));
        when(memoryService.currentSessionKey(1L)).thenReturn("1:2026-09-17");
        when(memoryFactService.recordObjective(any(), anyString(), anyList()))
                .thenThrow(new RuntimeException("db down"));

        JsonNode result = service.verifyMatrix(user(), strategy());

        assertNotNull(result, "记忆写不进去不该让验证本身失败");
    }

    // ==================== 3. 怎么读回来 ====================

    @Test
    void theVerificationSummaryIsParsedFromTheActiveFacts() {
        when(memoryFactService.activeFactValues(eq(1L), eq("strategy:10"), anyList()))
                .thenReturn(Map.of(
                        ObjectiveFactKeys.VERIFY_AT, "2026-09-14T09:00:00",
                        ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT, "21",
                        ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT, "10",
                        ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT, "-1.35",
                        ObjectiveFactKeys.VERIFY_FEE_DRAG_PCT, "1.883",
                        ObjectiveFactKeys.VERIFY_ENGINE_VERSION, "2a9dce9df73b"));

        StrategyService.VerificationSummary summary = service.latestVerification(1L, 10L);

        assertNotNull(summary);
        assertTrue(summary.hasSample());
        assertEquals(21, summary.validCells());
        assertEquals(10, summary.beatBuyAndHold());
        assertEquals(-1.35, summary.avgExcessPct(), 1e-9);
        assertEquals("2a9dce9df73b", summary.engineVersion());
    }

    @Test
    void noVerificationEverMeansNoSummaryRatherThanZeros() {
        // "没验证过"必须是 null（报告据此说"还没有做过样本外验证"），
        // 而不是一堆 0 —— 0 会被读成"验证过，结果是零"
        when(memoryFactService.activeFactValues(eq(1L), anyString(), anyList())).thenReturn(Map.of());
        assertNull(service.latestVerification(1L, 10L));

        when(memoryFactService.activeFactValues(eq(1L), anyString(), anyList()))
                .thenReturn(Map.of(ObjectiveFactKeys.VERIFY_AT, "2026-09-14T09:00:00"));
        StrategyService.VerificationSummary summary = service.latestVerification(1L, 10L);
        assertNotNull(summary);
        assertFalse(summary.hasSample(), "只有时间、没有样本量：这次验证不成结论");
    }

    @Test
    void aVerificationMissingTheTimestampIsTreatedAsNoVerification() {
        when(memoryFactService.activeFactValues(eq(1L), anyString(), anyList()))
                .thenReturn(Map.of(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT, "21"));
        assertNull(service.latestVerification(1L, 10L), "没有时间戳的结论没法判断新旧，等于没有");
    }

    @Test
    void readingWithoutMemoryServiceIsSafe() {
        StrategyService withoutMemory = new StrategyService(strategyRepository, strategyClient,
                userRepository, null, null);
        assertNull(withoutMemory.latestVerification(1L, 10L));
        assertNull(withoutMemory.verifyMatrix(user(), null));
        verify(strategyClient, never()).backtestMatrix(anyString(), anyList(), anyInt(), anyString(), anyString());
    }
}
