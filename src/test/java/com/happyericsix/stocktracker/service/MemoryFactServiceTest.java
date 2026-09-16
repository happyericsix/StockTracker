package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.MemoryFactsRequest;
import com.happyericsix.stocktracker.entity.MemoryFact;
import com.happyericsix.stocktracker.repository.MemoryFactRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * 事实层（L2）的核心：<b>改口 = 追加新事实并指向旧的</b>。
 *
 * 这是 M1 最重要的一条行为，"我改主意了，止损改成 5%"能不能正确生效全看这里，
 * 所以钉得细一点。
 */
@ExtendWith(MockitoExtension.class)
class MemoryFactServiceTest {

    @Mock
    private MemoryFactRepository factRepo;

    private MemoryFactService service;

    @BeforeEach
    void setUp() {
        service = new MemoryFactService(factRepo);
    }

    private MemoryFactRequest fact(String subject, String predicate, String value) {
        MemoryFactRequest request = new MemoryFactRequest();
        request.setSubject(subject);
        request.setPredicate(predicate);
        request.setObject(value);
        request.setFactType("constraint");
        request.setConfidence(0.9);
        return request;
    }

    private MemoryFactsRequest batch(MemoryFactRequest... facts) {
        MemoryFactsRequest request = new MemoryFactsRequest();
        request.setUserId(1L);
        request.setSessionKey("1:2026-09-16");
        request.setSourceEventIds(List.of(11L, 12L));
        request.setFacts(List.of(facts));
        return request;
    }

    private MemoryFact existing(Long id, String value) {
        return MemoryFact.builder()
                .id(id).userId(1L).subject("user").predicate("stop_loss_pct")
                .factValue(value).factType("constraint").confidence(0.9)
                .recordedAt(LocalDateTime.of(2026, 9, 1, 10, 0))
                .confirmed(true).trust("high")
                .build();
    }

    // ==================== 写入：同键即取代 ====================

    @Test
    void firstFactIsCreatedWithoutSupersedingAnything() {
        when(factRepo.findActiveByKey(1L, "user", "stop_loss_pct")).thenReturn(List.of());
        when(factRepo.save(any(MemoryFact.class))).thenAnswer(inv -> inv.getArgument(0));

        List<MemoryFactService.FactWriteResult> results =
                service.saveFacts(batch(fact("user", "stop_loss_pct", "-8")));

        assertEquals(1, results.size());
        assertEquals("created", results.get(0).action());
        assertNull(results.get(0).supersededId());

        ArgumentCaptor<MemoryFact> captor = ArgumentCaptor.forClass(MemoryFact.class);
        verify(factRepo).save(captor.capture());
        assertNull(captor.getValue().getSupersedesId());
        // 来源事件必须留痕，否则摘要/事实无法回溯到原始对话
        assertEquals("[11,12]", captor.getValue().getSourceEventIds());
    }

    /** 核心用例：止损从 -8 改成 5 —— 新事实指向旧的，旧事实就此失效。 */
    @Test
    void changedValueSupersedesThePreviousFact() {
        when(factRepo.findActiveByKey(1L, "user", "stop_loss_pct"))
                .thenReturn(List.of(existing(41L, "-8")));
        when(factRepo.save(any(MemoryFact.class))).thenAnswer(inv -> inv.getArgument(0));

        List<MemoryFactService.FactWriteResult> results =
                service.saveFacts(batch(fact("user", "stop_loss_pct", "5")));

        assertEquals("superseded", results.get(0).action());
        assertEquals(41L, results.get(0).supersededId());

        ArgumentCaptor<MemoryFact> captor = ArgumentCaptor.forClass(MemoryFact.class);
        verify(factRepo).save(captor.capture());
        assertEquals(41L, captor.getValue().getSupersedesId());
    }

    /** 同键同值：什么都不做。否则每天巩固一次就会把同一句话堆成一串重复事实。 */
    @Test
    void identicalValueIsNotWrittenAgain() {
        when(factRepo.findActiveByKey(1L, "user", "stop_loss_pct"))
                .thenReturn(List.of(existing(41L, "5")));

        List<MemoryFactService.FactWriteResult> results =
                service.saveFacts(batch(fact("user", "stop_loss_pct", "5")));

        assertEquals("unchanged", results.get(0).action());
        assertEquals(41L, results.get(0).id());
        verify(factRepo, never()).save(any(MemoryFact.class));
    }

    @Test
    void blankOrLowConfidenceFactsAreDropped() {
        MemoryFactRequest blank = fact("user", "stop_loss_pct", "   ");
        MemoryFactRequest unsure = fact("user", "risk_preference", "稳健");
        unsure.setConfidence(0.1);

        List<MemoryFactService.FactWriteResult> results = service.saveFacts(batch(blank, unsure));

        assertTrue(results.isEmpty());
        verify(factRepo, never()).save(any(MemoryFact.class));
    }

    @Test
    void unknownFactTypeFallsBackToObservation() {
        when(factRepo.findActiveByKey(anyLong(), anyString(), anyString())).thenReturn(List.of());
        when(factRepo.save(any(MemoryFact.class))).thenAnswer(inv -> inv.getArgument(0));

        MemoryFactRequest weird = fact("user", "risk_preference", "稳健");
        weird.setFactType("乱写的类型");
        service.saveFacts(batch(weird));

        ArgumentCaptor<MemoryFact> captor = ArgumentCaptor.forClass(MemoryFact.class);
        verify(factRepo).save(captor.capture());
        assertEquals("observation", captor.getValue().getFactType());
    }

    @Test
    void batchIsCappedToProtectAgainstRunawayExtraction() {
        when(factRepo.findActiveByKey(anyLong(), anyString(), anyString())).thenReturn(List.of());
        when(factRepo.save(any(MemoryFact.class))).thenAnswer(inv -> inv.getArgument(0));

        List<MemoryFactRequest> many = new ArrayList<>();
        for (int i = 0; i < MemoryFactService.MAX_FACTS_PER_BATCH + 5; i++) {
            many.add(fact("user", "predicate_" + i, "v" + i));
        }
        MemoryFactsRequest request = new MemoryFactsRequest();
        request.setUserId(1L);
        request.setFacts(many);

        assertEquals(MemoryFactService.MAX_FACTS_PER_BATCH, service.saveFacts(request).size());
    }

    // ==================== 检索与打分 ====================

    @Test
    void activeFactsAreRankedByTopicRelevance() {
        MemoryFact aboutSymbol = MemoryFact.builder()
                .id(2L).userId(1L).subject("600519").predicate("holding_cost").factValue("1500")
                .factType("holding").confidence(0.9).recordedAt(LocalDateTime.now())
                .trust("high").confirmed(true).build();
        MemoryFact unrelated = MemoryFact.builder()
                .id(3L).userId(1L).subject("user").predicate("sector_preference").factValue("白酒")
                .factType("preference").confidence(0.9).recordedAt(LocalDateTime.now())
                .trust("medium").confirmed(false).build();
        when(factRepo.findActiveByUserId(1L)).thenReturn(List.of(unrelated, aboutSymbol));

        List<Map<String, Object>> found = service.searchFacts(1L, "600519 的成本是多少", "600519", null, 5);

        // 与话题标的完全匹配的事实必须排第一
        assertEquals("600519", found.get(0).get("subject"));
        assertEquals("1500", found.get(0).get("object"));
    }

    @Test
    void filterByFactTypeAndSymbol() {
        MemoryFact holding = MemoryFact.builder()
                .id(2L).userId(1L).subject("600519").predicate("holding_cost").factValue("1500")
                .factType("holding").confidence(0.9).recordedAt(LocalDateTime.now()).build();
        MemoryFact preference = MemoryFact.builder()
                .id(3L).userId(1L).subject("user").predicate("risk_preference").factValue("稳健")
                .factType("preference").confidence(0.9).recordedAt(LocalDateTime.now()).build();
        when(factRepo.findActiveByUserId(1L)).thenReturn(List.of(holding, preference));

        assertEquals(1, service.searchFacts(1L, null, null, "holding", 5).size());
        assertEquals(0, service.searchFacts(1L, null, "000001", null, 5).size());
        assertEquals(2, service.searchFacts(1L, null, null, null, 5).size());
    }

    /** 变更链要能被检索结果带出来，否则模型只能凭空给出一个数字。 */
    @Test
    void searchResultCarriesPreviousValueFromTheSupersedeChain() {
        MemoryFact previous = existing(41L, "-8");
        MemoryFact current = MemoryFact.builder()
                .id(57L).userId(1L).subject("user").predicate("stop_loss_pct").factValue("5")
                .factType("constraint").confidence(0.9).supersedesId(41L)
                .recordedAt(LocalDateTime.of(2026, 9, 16, 10, 0)).confirmed(true).trust("high")
                .build();
        when(factRepo.findActiveByUserId(1L)).thenReturn(List.of(current));
        when(factRepo.findById(41L)).thenReturn(Optional.of(previous));

        List<Map<String, Object>> found = service.searchFacts(1L, "止损", null, null, 5);

        assertEquals("5", found.get(0).get("object"));
        assertEquals("-8", found.get(0).get("previousValue"));
        assertEquals("2026-09-01T10:00", found.get(0).get("previousRecordedAt"));
    }

    @Test
    void emptyInputsReturnNothingRatherThanFailing() {
        assertTrue(service.searchFacts(null, "x", null, null, 5).isEmpty());
        assertTrue(service.factHistory(1L, null, "p", 5).isEmpty());
        assertTrue(service.saveFacts(null).isEmpty());
    }

    // ==================== 撤回（逻辑撤回，走取代链） ====================

    /**
     * 撤回不删数据：追加一条 retraction 行指向它，让它自然失去"当前有效"资格。
     * 这样历史仍然可回溯 —— "我之前说过成本 1500 吗" 依然答得出来。
     */
    @Test
    void retractAppendsARetractionRowInsteadOfDeleting() {
        MemoryFact target = existing(41L, "1500");
        when(factRepo.findById(41L)).thenReturn(Optional.of(target));
        when(factRepo.save(any(MemoryFact.class))).thenAnswer(inv -> inv.getArgument(0));

        assertTrue(service.retract(1L, 41L));

        ArgumentCaptor<MemoryFact> captor = ArgumentCaptor.forClass(MemoryFact.class);
        verify(factRepo).save(captor.capture());
        MemoryFact retraction = captor.getValue();
        assertEquals(MemoryFactService.FACT_TYPE_RETRACTION, retraction.getFactType());
        assertEquals(41L, retraction.getSupersedesId());
        assertEquals("user", retraction.getProvenance());
        verify(factRepo, never()).delete(any(MemoryFact.class));
        verify(factRepo, never()).deleteById(anyLong());
    }

    @Test
    void retractRefusesToTouchAnotherUsersFact() {
        when(factRepo.findById(41L)).thenReturn(Optional.of(
                MemoryFact.builder().id(41L).userId(2L).subject("user").predicate("p").factValue("v").build()));

        assertFalse(service.retract(1L, 41L));
        verify(factRepo, never()).save(any(MemoryFact.class));
    }

    @Test
    void retractionRowsNeverShowUpInSearchOrPersona() {
        MemoryFact retraction = MemoryFact.builder()
                .id(60L).userId(1L).subject("user").predicate("holding_cost").factValue("已撤回")
                .factType(MemoryFactService.FACT_TYPE_RETRACTION).confidence(1.0)
                .recordedAt(LocalDateTime.now()).build();
        MemoryFact realFact = MemoryFact.builder()
                .id(61L).userId(1L).subject("user").predicate("risk_preference").factValue("稳健")
                .factType("preference").confidence(0.9).recordedAt(LocalDateTime.now()).build();
        when(factRepo.findActiveByUserId(1L)).thenReturn(List.of(retraction, realFact));

        List<Map<String, Object>> found = service.searchFacts(1L, null, null, null, 10);
        assertEquals(1, found.size());
        assertEquals("risk_preference", found.get(0).get("predicate"));

        List<Map<String, Object>> persona = service.persona(1L);
        assertEquals(1, persona.size());
        assertEquals("risk_preference", persona.get(0).get("predicate"));
    }

    // ==================== 长期画像（确定性派生） ====================

    /**
     * 画像不用 LLM：稳定 + 可解释 + 零成本。
     * 只收"偏好/约束/目标/持仓"，且要求用户确认过或置信度够高 ——
     * 观察类的推测不该被当成"你是谁"。
     */
    @Test
    void personaKeepsOnlyIdentityFactsWithEnoughTrust() {
        LocalDateTime now = LocalDateTime.now();
        MemoryFact preference = MemoryFact.builder()
                .id(3L).userId(1L).subject("user").predicate("risk_preference").factValue("稳健")
                .factType("preference").confidence(0.9).confirmed(true).recordedAt(now).build();
        MemoryFact observation = MemoryFact.builder()
                .id(4L).userId(1L).subject("600519").predicate("watch_reason").factValue("低估值")
                .factType("observation").confidence(0.9).recordedAt(now).build();
        MemoryFact shaky = MemoryFact.builder()
                .id(5L).userId(1L).subject("user").predicate("investing_style").factValue("激进")
                .factType("preference").confidence(0.4).confirmed(false).recordedAt(now).build();
        when(factRepo.findActiveByUserId(1L)).thenReturn(List.of(preference, observation, shaky));

        List<Map<String, Object>> persona = service.persona(1L);

        assertEquals(1, persona.size());
        assertEquals("稳健", persona.get(0).get("object"));
    }

    @Test
    void personaIsCappedAndOrderedByTypePriority() {
        LocalDateTime now = LocalDateTime.now();
        List<MemoryFact> facts = new ArrayList<>();
        for (int i = 0; i < MemoryFactService.PERSONA_LIMIT + 3; i++) {
            facts.add(MemoryFact.builder()
                    .id((long) (100 - i)).userId(1L).subject("user").predicate("p" + i).factValue("v" + i)
                    .factType(i % 2 == 0 ? "preference" : "holding").confidence(0.9).confirmed(true)
                    .recordedAt(now).build());
        }
        when(factRepo.findActiveByUserId(1L)).thenReturn(facts);

        List<Map<String, Object>> persona = service.persona(1L);

        assertEquals(MemoryFactService.PERSONA_LIMIT, persona.size());
        // 偏好排在持仓之前
        assertEquals("preference", persona.get(0).get("factType"));
    }
}
