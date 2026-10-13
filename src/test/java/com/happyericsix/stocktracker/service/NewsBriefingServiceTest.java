package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsTopic;
import com.happyericsix.stocktracker.util.CnTime;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.when;

/**
 * 新闻知识组装器（agent 互通 P1/P3）：只做数据拼装、不做 LLM 综合 ——
 * 这里钉住"拼出来的形状"与"证据包预算"（委员会 8 角色共读，膨胀代价 ×8）。
 */
@ExtendWith(MockitoExtension.class)
class NewsBriefingServiceTest {

    @Mock
    private NewsService newsService;
    @Mock
    private NewsTopicService newsTopicService;

    private final ObjectMapper mapper = new ObjectMapper();
    private NewsBriefingService service;

    @BeforeEach
    void setUp() {
        service = new NewsBriefingService(newsService, newsTopicService, mapper);
    }

    private NewsEvent event(long id, String direction, String impact, int hoursAgo, boolean analyzed) {
        NewsEvent event = NewsEvent.builder()
                .id(id).symbol("SH600519").name("贵州茅台")
                .title("事件" + id).sourceLevel(1).sourceName("东方财富·公告")
                .publishedAt(CnTime.now().minusHours(hoursAgo))
                .direction(direction).impactLevel(impact)
                .build();
        if (analyzed) {
            event.setPlainSummary("一句话解读。");
        }
        return event;
    }

    private NewsTopic topic(long id, String digest) {
        return NewsTopic.builder().id(id).title("专题" + id).digest(digest)
                .direction("利好").eventCount(8).sourceCount(2)
                .status(NewsTopicContract.STATUS_OPEN).titleOrigin("editor")
                .firstEventAt(LocalDateTime.now().minusDays(1))
                .lastEventAt(LocalDateTime.now().minusHours(2))
                .build();
    }

    @Test
    void committeeBriefingCarriesSignalsAndBudget() {
        when(newsService.timelineEvents(eq("SH600519"), anyInt(), anyInt())).thenReturn(List.of(
                event(1, "利好", "high", 5, true),
                event(2, null, null, 10, true),
                event(3, "利空", "medium", 40, true)));
        when(newsTopicService.listTopics(anyInt(), anyString(), eq(false), eq(false)))
                .thenReturn(List.of(topic(7, "重组草案出炉")));
        when(newsService.marketReadIfCached()).thenReturn(null);

        JsonNode briefing = service.committeeBriefing("SH600519");

        // 事件按"有方向或 impact"过滤（信号优先，原始流不要）
        assertEquals(2, briefing.path("events").size());
        // 专题与免责提示在
        assertEquals("重组草案出炉",
                briefing.path("topics").get(0).path("digest").asText());
        assertTrue(briefing.path("note").asText("").contains("可信度"));
        assertFalse(NewsBriefingService.isEmptyBriefing(briefing));
    }

    @Test
    void emptyBriefingIsDetectedForFailOpen() {
        when(newsService.timelineEvents(anyString(), anyInt(), anyInt())).thenReturn(List.of());
        when(newsTopicService.listTopics(anyInt(), anyString(), eq(false), eq(false)))
                .thenReturn(List.of());
        when(newsService.marketReadIfCached()).thenReturn(null);

        assertTrue(NewsBriefingService.isEmptyBriefing(service.committeeBriefing("SH600519")),
                "全空证据包应判空（决策侧据此不带字段）");
    }

    @Test
    void latestShockRequiresHighImpactDirectionAndRecency() {
        lenient().when(newsService.timelineEvents(anyString(), anyInt(), anyInt())).thenReturn(List.of(
                event(1, "利好", "high", 30, true),       // 太老（>24h）
                event(2, "利空", "medium", 3, true),      // impact 不够
                event(3, null, "high", 3, true),          // 没方向
                event(4, "利空", "high", 3, false)));     // 没解读
        assertNull(service.latestShock("SH600519", 24), "四条都不满足 news_shock 判据");

        NewsEvent shock = event(5, "利空", "high", 3, true);
        when(newsService.timelineEvents(anyString(), anyInt(), anyInt())).thenReturn(List.of(shock));
        JsonNode alert = service.latestShock("SH600519", 24);
        assertEquals("利空", alert.path("direction").asText());
        assertEquals("事件5", alert.path("title").asText());
        assertTrue(alert.path("analyzed").asBoolean(false));
    }
}
