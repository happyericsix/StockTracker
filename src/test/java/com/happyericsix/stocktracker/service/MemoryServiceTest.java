package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.MemoryClient;
import com.happyericsix.stocktracker.dto.MemoryEpisodeRequest;
import com.happyericsix.stocktracker.entity.MemoryEpisode;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.repository.MemoryEpisodeRepository;
import com.happyericsix.stocktracker.repository.MemoryEventRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Pageable;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

@ExtendWith(MockitoExtension.class)
class MemoryServiceTest {

    @Mock
    private MemoryEventRepository eventRepo;

    @Mock
    private MemoryEpisodeRepository episodeRepo;

    @Mock
    private MemoryClient memoryClient;

    @Mock
    private MemoryFactService factService;

    @Mock
    private MemoryLessonService lessonService;

    private final ObjectMapper mapper = new ObjectMapper();

    private MemoryService service;

    @BeforeEach
    void setUp() {
        service = new MemoryService(eventRepo, episodeRepo, memoryClient, factService, lessonService, mapper);
    }

    // ==================== 会话键 ====================

    @Test
    void sessionKeyIsUserIdPlusLocalDate() {
        String expected = "7:" + LocalDate.now(ZoneId.of("Asia/Shanghai"));
        assertEquals(expected, service.currentSessionKey(7L));
        // 固定的日期换算：会话键按天切分，是"前情提要"能按天回顾的前提
        assertEquals("7:2026-09-16", service.sessionKeyFor(7L, LocalDate.of(2026, 9, 16)));
    }

    // ==================== 追加（只追加，且失败无害） ====================

    @Test
    void appendTruncatesOversizedContent() {
        when(eventRepo.save(any(MemoryEvent.class))).thenAnswer(inv -> inv.getArgument(0));

        MemoryEvent saved = service.append(MemoryEvent.builder()
                .userId(1L).sessionKey("1:2026-09-16").kind("chat_user")
                .content("x".repeat(MemoryService.MAX_CONTENT_CHARS + 500))
                .build());

        assertNotNull(saved);
        // 账本不是粘贴板：超长内容截断并标注，避免用户贴一份报告就把记忆撑爆
        assertTrue(saved.getContent().endsWith("…（已截断）"));
        assertEquals(MemoryService.MAX_CONTENT_CHARS + "…（已截断）".length(), saved.getContent().length());
    }

    @Test
    void appendSkipsIncompleteEventWithoutTouchingRepository() {
        assertNull(service.append(MemoryEvent.builder().userId(null).sessionKey("s").content("c").build()));
        assertNull(service.append(MemoryEvent.builder().userId(1L).sessionKey("s").content(null).build()));
        verify(eventRepo, never()).save(any(MemoryEvent.class));
    }

    @Test
    void appendChatMarksProvenanceAndTrust() {
        when(eventRepo.save(any(MemoryEvent.class))).thenAnswer(inv -> inv.getArgument(0));

        MemoryEvent fromUser = service.appendChat(1L, "1:2026-09-16", "chat_user", "user", "茅台多少钱", null);
        MemoryEvent fromBot = service.appendChat(1L, "1:2026-09-16", "chat_bot", "assistant", "约1500", "600519");

        // 用户亲口说的 vs 模型产出的，可信度不同：将来注入时要区别对待
        assertEquals("user", fromUser.getProvenance());
        assertEquals("high", fromUser.getTrust());
        assertEquals("model", fromBot.getProvenance());
        assertEquals("medium", fromBot.getTrust());
    }

    // ==================== 上下文组装 ====================

    @Test
    void contextAlwaysCarriesTodayEvenWithoutMemory() {
        Map<String, Object> context = service.buildContext(null, null);

        // 关键：即使一条记忆都没有，"今天几号"也必须给模型（否则"去年""上周"无从换算）
        assertEquals(service.today(), context.get("today"));
        assertTrue(String.valueOf(context.get("timeZone")).startsWith("Asia/Shanghai"));
        assertEquals(List.of(), context.get("recaps"));
        assertEquals(List.of(), context.get("pendingSessions"));
        assertEquals(List.of(), context.get("facts"));
    }

    /** 事实层进上下文时必须带上本次话题（query），否则挑出来的只是"最近的事实"而不是"相关的"。 */
    @Test
    void contextPassesTheCurrentTopicToFactRetrieval() {
        when(episodeRepo.findByUserIdOrderByCreatedAtDesc(eq(1L), any(Pageable.class))).thenReturn(List.of());
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class))).thenReturn(List.of());
        when(factService.searchFacts(1L, "把止损改成5%", "600519", null, MemoryService.FACT_LIMIT))
                .thenReturn(List.of(Map.of("predicate", "stop_loss_pct", "object", "5")));

        Map<String, Object> context = service.buildContext(1L, "1:2026-09-16", "把止损改成5%", "600519");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> facts = (List<Map<String, Object>>) context.get("facts");
        assertEquals(1, facts.size());
        assertEquals("stop_loss_pct", facts.get(0).get("predicate"));
    }

    @Test
    void contextSkipsTheCurrentSessionEpisode() throws Exception {
        MemoryEpisode previous = MemoryEpisode.builder()
                .userId(1L).sessionKey("1:2026-09-15").version(2)
                .summary("用户在建均线策略").keyPoints(mapper.writeValueAsString(List.of("关注回撤")))
                .openQuestions(mapper.writeValueAsString(List.of("是否改用周线")))
                .build();
        MemoryEpisode current = MemoryEpisode.builder()
                .userId(1L).sessionKey("1:2026-09-16").version(1)
                .summary("今天的对话").build();

        when(episodeRepo.findByUserIdOrderByCreatedAtDesc(eq(1L), any(Pageable.class)))
                .thenReturn(List.of(current, previous));
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class)))
                .thenReturn(List.of());

        Map<String, Object> context = service.buildContext(1L, "1:2026-09-16");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> recaps = (List<Map<String, Object>>) context.get("recaps");
        assertEquals(1, recaps.size());
        assertEquals("1:2026-09-15", recaps.get(0).get("sessionKey"));
        assertEquals(2, recaps.get(0).get("version"));
        assertEquals(List.of("关注回撤"), recaps.get(0).get("keyPoints"));
        assertEquals(List.of("是否改用周线"), recaps.get(0).get("openQuestions"));
    }

    @Test
    void pendingSessionsSkipSummarizedAndTooShortOnes() {
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class)))
                .thenReturn(List.of("1:2026-09-15", "1:2026-09-14", "1:2026-09-13"));
        when(episodeRepo.existsByUserIdAndSessionKey(1L, "1:2026-09-15")).thenReturn(true);
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-14")).thenReturn(5L);
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-13")).thenReturn(1L);

        List<String> pending = service.pendingSessions(1L, "1:2026-09-16");

        // 已总结的跳过；事件太少的也跳过（用户只发一句就再没回来，不值得生成前情提要）
        assertEquals(List.of("1:2026-09-14"), pending);
    }

    /**
     * 兜底原文摘录：摘要是跨天时异步生成的，用户隔几天回来问"上次那个策略"，
     * 第一条消息时摘要极可能还没生成好 —— 只靠摘要就等于第一条必然失忆。
     */
    @Test
    void contextCarriesRawDigestForTheUnsummarizedSession() {
        when(episodeRepo.findByUserIdOrderByCreatedAtDesc(eq(1L), any(Pageable.class))).thenReturn(List.of());
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class)))
                .thenReturn(List.of("1:2026-09-15"));
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-15")).thenReturn(2L);
        when(eventRepo.findByUserIdAndSessionKeyOrderByIdAsc(1L, "1:2026-09-15")).thenReturn(List.of(
                MemoryEvent.builder().role("user").content("帮我做一个20日上穿60日买入的策略")
                        .occurredAt(LocalDateTime.of(2026, 9, 15, 10, 0)).build(),
                MemoryEvent.builder().role("assistant").content("已生成策略「MA cross」（600519）")
                        .occurredAt(LocalDateTime.of(2026, 9, 15, 10, 1)).build()));

        Map<String, Object> context = service.buildContext(1L, "1:2026-09-16");

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> digest = (List<Map<String, Object>>) context.get("pendingDigest");
        assertEquals(2, digest.size());
        assertEquals("user", digest.get(0).get("role"));
        assertEquals("帮我做一个20日上穿60日买入的策略", digest.get(0).get("content"));
        assertEquals("2026-09-15T10:00", digest.get(0).get("occurredAt"));
    }

    @Test
    void digestIsCappedAndTrimsLongContent() {
        List<MemoryEvent> events = new ArrayList<>();
        for (int i = 0; i < 10; i++) {
            events.add(MemoryEvent.builder().role("user").content("x".repeat(300)).build());
        }
        when(eventRepo.findByUserIdAndSessionKeyOrderByIdAsc(1L, "1:2026-09-15")).thenReturn(events);

        List<Map<String, Object>> digest = service.digest(1L, "1:2026-09-15");

        assertEquals(MemoryService.DIGEST_MAX_EVENTS, digest.size());
        String first = String.valueOf(digest.get(0).get("content"));
        assertTrue(first.endsWith("…"));
        assertTrue(first.length() <= MemoryService.DIGEST_CONTENT_CHARS + 1);
    }

    // ==================== 摘要落库（版本化，不覆盖） ====================

    @Test
    void saveEpisodeAppendsNextVersionInsteadOfOverwriting() {
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-15"))
                .thenReturn(Optional.of(MemoryEpisode.builder().version(2).build()));

        MemoryEpisodeRequest request = new MemoryEpisodeRequest();
        request.setUserId(1L);
        request.setSessionKey("1:2026-09-15");
        request.setSummary("用户关注均线策略");
        request.setKeyPoints(List.of("要点一"));
        request.setSourceEventIds(List.of(11L, 12L));
        request.setModel("deepseek-chat");

        int version = service.saveEpisode(request);

        assertEquals(3, version);
        ArgumentCaptor<MemoryEpisode> captor = ArgumentCaptor.forClass(MemoryEpisode.class);
        verify(episodeRepo).save(captor.capture());
        MemoryEpisode saved = captor.getValue();
        assertEquals(3, saved.getVersion());
        assertEquals("[11,12]", saved.getSourceEventIds());
        assertEquals("[\"要点一\"]", saved.getKeyPoints());
    }

    @Test
    void firstEpisodeIsVersionOne() {
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(anyLong(), anyString()))
                .thenReturn(Optional.empty());

        MemoryEpisodeRequest request = new MemoryEpisodeRequest();
        request.setUserId(1L);
        request.setSessionKey("1:2026-09-15");
        request.setSummary("第一次总结");

        assertEquals(1, service.saveEpisode(request));
    }

    // ==================== 巩固触发 ====================

    @Test
    void consolidationUsesMemoryClientForPendingSessions() {
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class)))
                .thenReturn(List.of("1:2026-09-15"));
        when(episodeRepo.existsByUserIdAndSessionKey(1L, "1:2026-09-15")).thenReturn(false);
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-15")).thenReturn(4L);

        service.ensurePreviousSessionConsolidated(1L, "1:2026-09-16");

        verify(memoryClient, times(1)).consolidate(1L, "1:2026-09-15");
    }

    /**
     * 记忆是增强功能：巩固阶段无论怎么炸，都不能影响用户那条消息的回复。
     */
    @Test
    void consolidationFailureIsSwallowed() {
        when(eventRepo.findRecentOtherSessionKeys(eq(1L), anyString(), any(Pageable.class)))
                .thenReturn(List.of("1:2026-09-15"));
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-15")).thenReturn(4L);
        when(memoryClient.consolidate(1L, "1:2026-09-15")).thenThrow(new RuntimeException("python down"));

        assertDoesNotThrow(() -> service.ensurePreviousSessionConsolidated(1L, "1:2026-09-16"));
    }

    @Test
    void consolidationSkipsWhenUserIdOrSessionKeyMissing() {
        service.ensurePreviousSessionConsolidated(null, "1:2026-09-16");
        service.ensurePreviousSessionConsolidated(1L, null);

        verifyNoInteractions(memoryClient);
    }

    // ==================== 会话中途的节奏巩固（预热递增） ====================

    /**
     * 计数永远是奇数：事件是"用户消息先落账 → 助手回复后落账"，
     * 而本方法在处理用户消息时调用。测试里必须用真实序列（1、3、5、7…），
     * 否则用偶数写出来的断言会掩盖"取模判定永不命中"这类 bug。
     */
    @Test
    void warmupTriggersAfterTheFirstExchange() {
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(3L);
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.empty());

        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");

        verify(memoryClient, times(1)).consolidate(1L, "1:2026-09-16");
    }

    @Test
    void warmupSecondTriggerIsAtFourEvents() {
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(5L);
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.of(MemoryEpisode.builder().version(1).build()));

        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");

        verify(memoryClient, times(1)).consolidate(1L, "1:2026-09-16");
    }

    /** 稳定阶段：version=3 时阈值是 24 条，9 条不该触发（省 LLM 调用）。 */
    @Test
    void steadyStateDoesNotTriggerBeforeTheThreshold() {
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.of(MemoryEpisode.builder().version(3).build()));
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(9L);

        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");

        verify(memoryClient, never()).consolidate(anyLong(), anyString());
    }

    @Test
    void steadyStateTriggersOnceThresholdIsReached() {
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(25L);
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.of(MemoryEpisode.builder().version(3).build()));

        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");

        verify(memoryClient, times(1)).consolidate(1L, "1:2026-09-16");
    }

    /** 冷却：异步巩固还没写完摘要时，紧接着的消息不能重复触发。 */
    @Test
    void cooldownPreventsDuplicateConsolidation() {
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(3L);
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.empty());

        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");
        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");
        service.maybeConsolidateCurrentSession(1L, "1:2026-09-16");

        verify(memoryClient, times(1)).consolidate(1L, "1:2026-09-16");
    }

    @Test
    void warmupFailureIsSwallowed() {
        when(eventRepo.countByUserIdAndSessionKey(1L, "1:2026-09-16")).thenReturn(3L);
        when(episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(1L, "1:2026-09-16"))
                .thenReturn(Optional.empty());
        when(memoryClient.consolidate(1L, "1:2026-09-16")).thenThrow(new RuntimeException("python down"));

        assertDoesNotThrow(() -> service.maybeConsolidateCurrentSession(1L, "1:2026-09-16"));
    }
}
