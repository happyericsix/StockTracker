package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.MemoryEpisodeRequest;
import com.happyericsix.stocktracker.dto.MemoryFactsRequest;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.service.MemoryFactService;
import com.happyericsix.stocktracker.service.MemoryLessonService;
import com.happyericsix.stocktracker.service.MemoryService;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.Mockito.when;

/**
 * 记忆内部接口的鉴权与入参校验。
 *
 * <p>这些接口能读到用户<b>全部</b>对话记忆，而且被 {@code permitAll} 放过了
 * Spring Security（因为调用方是 Python，不带 JWT）。所以共享密钥是唯一的门，
 * 必须钉住：没令牌、令牌错、密钥没配 —— 三种情况一律拒绝。
 */
class InternalMemoryControllerTest {

    private static final String TOKEN = "s3cret-internal";

    private InternalMemoryController controller(MemoryService service) {
        return new InternalMemoryController(service, Mockito.mock(MemoryFactService.class),
                Mockito.mock(MemoryLessonService.class), TOKEN);
    }

    private MemoryService serviceWithContext() {
        MemoryService service = Mockito.mock(MemoryService.class);
        when(service.buildContext(anyLong(), any(), any(), any(), Mockito.anyInt()))
                .thenReturn(Map.of("today", "2026-09-16"));
        return service;
    }

    @Test
    void rejectsMissingAndWrongToken() {
        InternalMemoryController controller = controller(serviceWithContext());

        assertEquals(401, controller.context(1L, "1:2026-09-16", null, null, 8, null).getStatusCode().value());
        assertEquals(401, controller.context(1L, "1:2026-09-16", null, null, 8, "wrong").getStatusCode().value());
        assertEquals(401, controller.events(1L, "1:2026-09-16", null).getStatusCode().value());
        assertEquals(401, controller.saveEpisode(new MemoryEpisodeRequest(), null).getStatusCode().value());
        assertEquals(401, controller.facts(1L, "止损", null, null, 8, null).getStatusCode().value());
        assertEquals(401, controller.lessons(1L, "止损", null, 3, null).getStatusCode().value());
        assertEquals(401, controller.index(1L, null).getStatusCode().value());
    }

    @Test
    void acceptsCorrectToken() {
        InternalMemoryController controller = controller(serviceWithContext());

        assertEquals(200, controller.context(1L, "1:2026-09-16", "止损", null, 30, TOKEN).getStatusCode().value());
    }

    /**
     * 未配置密钥时一律拒绝（fail closed）。
     * 这里能读到用户全部对话记忆，"配置漏了就先敞开"是不可接受的默认值。
     */
    @Test
    void rejectsEverythingWhenTokenIsNotConfigured() {
        InternalMemoryController controller = new InternalMemoryController(
                serviceWithContext(), Mockito.mock(MemoryFactService.class),
                Mockito.mock(MemoryLessonService.class), "");

        assertEquals(401, controller.context(1L, "1:2026-09-16", null, null, 8, "").getStatusCode().value());
        assertEquals(401, controller.context(1L, "1:2026-09-16", null, null, 8, null).getStatusCode().value());
    }

    @Test
    void episodeRequiresUserSessionAndSummary() {
        MemoryService service = Mockito.mock(MemoryService.class);
        when(service.saveEpisode(any())).thenReturn(1);
        InternalMemoryController controller = controller(service);

        MemoryEpisodeRequest incomplete = new MemoryEpisodeRequest();
        incomplete.setUserId(1L);
        assertEquals(400, controller.saveEpisode(incomplete, TOKEN).getStatusCode().value());

        MemoryEpisodeRequest blankSummary = new MemoryEpisodeRequest();
        blankSummary.setUserId(1L);
        blankSummary.setSessionKey("1:2026-09-16");
        blankSummary.setSummary("   ");
        assertEquals(400, controller.saveEpisode(blankSummary, TOKEN).getStatusCode().value());

        MemoryEpisodeRequest valid = new MemoryEpisodeRequest();
        valid.setUserId(1L);
        valid.setSessionKey("1:2026-09-16");
        valid.setSummary("用户关注均线策略");
        assertEquals(200, controller.saveEpisode(valid, TOKEN).getStatusCode().value());
        // 入参不完整时不能落库
        Mockito.verify(service, Mockito.times(1)).saveEpisode(any());
    }

    /** 事实写入必须带 userId，否则不知道写给谁 —— 这种请求要直接拒掉。 */
    @Test
    void factWriteRequiresUserId() {
        InternalMemoryController controller = controller(Mockito.mock(MemoryService.class));

        assertEquals(400, controller.saveFacts(new MemoryFactsRequest(), TOKEN).getStatusCode().value());
    }

    @Test
    void eventsEndpointReturnsLedgerEntries() {
        MemoryService service = Mockito.mock(MemoryService.class);
        when(service.listEvents(1L, "1:2026-09-16")).thenReturn(List.of());
        InternalMemoryController controller = controller(service);

        Map<String, Object> body = controller.events(1L, "1:2026-09-16", TOKEN).getBody();

        assertEquals(List.of(), body.get("events"));
    }

    /**
     * 账本事件必须把 meta 一并返回。
     *
     * <p>meta 里放的是结构化细节：工具调用的<b>参数</b>（按声明脱敏后）与每轮<b>用量</b>
     * （P1 的 kind=usage 事件把 delta 放在这里）。不返回它，
     * "这个月花了多少 token""那次调用到底要了什么"就只能靠 SQL 看，接口层等于没有这个能力。
     */
    @Test
    void eventsEndpointExposesMeta() {
        MemoryService service = Mockito.mock(MemoryService.class);
        MemoryEvent event = new MemoryEvent();
        event.setId(7L);
        event.setKind("usage");
        event.setRole("system");
        event.setContent("本轮用量：LLM 2 次/9006 tokens");
        event.setMeta("{\"delta\":{\"llm.total_tokens\":9006}}");
        event.setOccurredAt(java.time.LocalDateTime.of(2026, 9, 16, 14, 29));
        when(service.listEvents(1L, "1:2026-09-16")).thenReturn(List.of(event));
        InternalMemoryController controller = controller(service);

        Map<String, Object> body = controller.events(1L, "1:2026-09-16", TOKEN).getBody();

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> events = (List<Map<String, Object>>) body.get("events");
        assertEquals(1, events.size());
        assertEquals("{\"delta\":{\"llm.total_tokens\":9006}}", events.get(0).get("meta"));
        assertEquals("usage", events.get(0).get("kind"));
    }
}
