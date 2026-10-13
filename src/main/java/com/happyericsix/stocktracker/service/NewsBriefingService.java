package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsTopic;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

/**
 * 新闻知识组装器（agent 互通 P1/P3）：把库里的新闻事实组装成
 * **agent 可消费的紧凑形状**，供三处共用 ——
 * <ol>
 *   <li>Python 新闻工具（经 InternalNewsController 的 briefing 端点）；</li>
 *   <li>委员会证据包（PaperTradingService 决策前组装 news_briefing）；</li>
 *   <li>盯盘 news_shock 检测。</li>
 * </ol>
 *
 * <h3>设计纪律：只做数据拼装，不做 LLM 综合</h3>
 * "合起来怎么看"是消费方（聊天 agent / 委员会角色）的本职 —— 它们本来就是
 * LLM。这里如果再做一次综合，一次工具调用就会变成两级 LLM 链（延迟翻倍、
 * 预算记账混乱，还会形成 Python→Java→Python 的环）。所以本类只输出
 * **已落库的静态事实**（分析字段、可信度、专题 digest），全部 &lt;1s、零 LLM。
 *
 * <h3>token 纪律</h3>
 * 委员会证据包有硬预算（{@link #COMMITTEE_MAX_EVENTS} 条事件 +
 * {@link #COMMITTEE_MAX_TOPICS} 条专题，每条一句话截断，实测约 600~800 token）：
 * 证据是给 8 个角色看的，膨胀的代价是乘以 8。
 */
@Service
public class NewsBriefingService {

    private static final Logger log = LoggerFactory.getLogger(NewsBriefingService.class);
    private static final DateTimeFormatter PUBLISHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 事件摘要进证据包的截断（一句话够用，长文是给深读工具的） */
    private static final int SUMMARY_CLIP = 120;
    /** 委员会证据包上限 */
    static final int COMMITTEE_MAX_EVENTS = 5;
    static final int COMMITTEE_MAX_TOPICS = 3;
    /** news_shock 判定：high impact 且已判方向且已解读 */
    static final String IMPACT_HIGH = "high";

    private final NewsService newsService;
    private final NewsTopicService newsTopicService;
    private final ObjectMapper mapper;

    public NewsBriefingService(NewsService newsService, NewsTopicService newsTopicService,
                               ObjectMapper mapper) {
        this.newsService = newsService;
        this.newsTopicService = newsTopicService;
        this.mapper = mapper;
    }

    /**
     * 单条事件完整事实（read_news_detail / chat focus 注入的数据源）。
     * 找不到返回 null。形状与 {@link #briefing} 里的 events 元素一致（多 name/body 两列）。
     */
    public JsonNode eventFacts(Long eventId) {
        NewsEvent event = newsService.eventById(eventId);
        if (event == null) {
            return null;
        }
        ObjectNode node = eventNode(event, SUMMARY_CLIP * 4);
        node.put("name", event.getName() == null ? "" : event.getName());
        node.put("body", clip(event.getBody() == null ? "" : event.getBody(), SUMMARY_CLIP * 4));
        node.put("content", clip(event.getContent() == null ? "" : event.getContent(), SUMMARY_CLIP * 4));
        return node;
    }

    /**
     * 个股信息面简报（Python 工具 / 内部端点的形状）：近 days 天事件（含分析字段
     * 与可信度）+ 已撰稿专题。失败容忍：任何一段为空都不挡其余段。
     */
    public JsonNode briefing(String symbol, int days, int maxEvents) {
        ObjectNode root = mapper.createObjectNode();
        root.put("symbol", symbol == null ? "" : symbol);
        try {
            List<NewsEvent> events = newsService.timelineEvents(symbol, days, maxEvents);
            ArrayNode array = root.putArray("events");
            for (NewsEvent event : events) {
                array.add(eventNode(event, SUMMARY_CLIP * 2));
            }
            root.put("event_count", events.size());
        } catch (Exception e) {
            log.warn("简报事件段组装失败 symbol={}: {}", symbol, e.getMessage());
            root.putArray("events");
            root.put("event_count", 0);
        }
        try {
            List<NewsTopic> topics = newsTopicService.listTopics(days, symbol, false, false);
            ArrayNode array = root.putArray("topics");
            for (NewsTopic topic : topics) {
                array.add(topicNode(topic));
            }
        } catch (Exception e) {
            log.warn("简报专题段组装失败 symbol={}: {}", symbol, e.getMessage());
            root.putArray("topics");
        }
        return root;
    }

    /**
     * 委员会证据包（精简版）：top 专题 + 高信号事件 + 市场信息面（仅缓存值）。
     * 事件按"有方向且已解读"优先 —— 委员会要的是信号，不是原始流。
     */
    public JsonNode committeeBriefing(String symbol) {
        ObjectNode root = mapper.createObjectNode();
        List<NewsEvent> events = newsService.timelineEvents(symbol, 3, 30).stream()
                .filter(event -> event.getPlainSummary() != null && !event.getPlainSummary().isBlank())
                .filter(event -> event.getDirection() != null || event.getImpactLevel() != null)
                .limit(COMMITTEE_MAX_EVENTS)
                .toList();
        ArrayNode eventArray = root.putArray("events");
        events.forEach(event -> eventArray.add(eventNode(event, SUMMARY_CLIP)));
        ArrayNode topicArray = root.putArray("topics");
        newsTopicService.listTopics(3, symbol, false, false).stream()
                .limit(COMMITTEE_MAX_TOPICS)
                .forEach(topic -> topicArray.add(topicNode(topic)));
        JsonNode marketRead = newsService.marketReadIfCached();
        if (marketRead != null && !marketRead.path("read").asText("").isBlank()) {
            root.put("market_read", marketRead.path("read").asText());
        }
        root.put("note", "信息面证据：可信度分级见各条 credibility；未经证实的传闻不能作为主要论据");
        return root;
    }

    /**
     * 盯盘 news_shock 检测：近 {@code hours} 小时内该标的最新一条
     * high impact 且已判方向的已解读事件；没有返回 null（不触发）。
     */
    public JsonNode latestShock(String symbol, int hours) {
        LocalDateTime since = CnTime.now().minusHours(Math.max(1, hours));
        return newsService.timelineEvents(symbol, 2, 30).stream()
                .filter(event -> event.getPublishedAt() != null && event.getPublishedAt().isAfter(since))
                .filter(event -> IMPACT_HIGH.equals(event.getImpactLevel()))
                .filter(event -> event.getDirection() != null && !event.getDirection().isBlank())
                .filter(event -> event.getPlainSummary() != null && !event.getPlainSummary().isBlank())
                .findFirst()
                .map(event -> eventNode(event, SUMMARY_CLIP * 2))
                .orElse(null);
    }

    /** 事件 → 紧凑 JSON（Python 工具与委员会共用同一形状，字段名是两侧契约）。 */
    private ObjectNode eventNode(NewsEvent event, int summaryClip) {
        ObjectNode node = mapper.createObjectNode();
        node.put("id", event.getId());
        node.put("title", nullToEmpty(event.getTitle()));
        node.put("symbol", event.getSymbol());
        node.put("source_level", event.getSourceLevel() == null ? 2 : event.getSourceLevel());
        node.put("source_name", nullToEmpty(event.getSourceName()));
        node.put("published_at", event.getPublishedAt() == null
                ? "" : event.getPublishedAt().format(PUBLISHED_AT));
        // 日期级条目（公告等）：00:00 是占位不是事实时刻，标出来让消费方正确转述
        if (event.getPublishedAt() != null) {
            LocalDateTime at = event.getPublishedAt();
            node.put("date_only", at.getHour() == 0 && at.getMinute() == 0 && at.getSecond() == 0);
        } else {
            node.put("date_only", false);
        }
        node.put("direction", event.getDirection());
        node.put("impact_level", event.getImpactLevel());
        node.put("event_type", event.getEventType());
        if (event.getCredibility() != null) {
            node.put("credibility", event.getCredibility());
        }
        if (event.getCredibilityGrade() != null) {
            node.put("credibility_grade", event.getCredibilityGrade());
        }
        String summary = event.getPlainSummary();
        if (summary != null && !summary.isBlank()) {
            node.put("analyzed", true);
            node.put("plain_summary", clip(summary, summaryClip));
        } else {
            node.put("analyzed", false);
            node.put("plain_summary", "");
            node.put("content", clip(event.getContent() == null ? "" : event.getContent(), summaryClip));
        }
        return node;
    }

    /** 专题 → 紧凑 JSON（digest 是一句话结论，story 不进证据包 —— 那是深读工具的事）。 */
    private ObjectNode topicNode(NewsTopic topic) {
        ObjectNode node = mapper.createObjectNode();
        node.put("id", topic.getId());
        node.put("title", nullToEmpty(topic.getTitle()));
        node.put("digest", nullToEmpty(topic.getDigest()));
        node.put("direction", topic.getDirection());
        node.put("event_count", topic.getEventCount() == null ? 0 : topic.getEventCount());
        node.put("source_count", topic.getSourceCount() == null ? 0 : topic.getSourceCount());
        node.put("first_event_at", topic.getFirstEventAt() == null
                ? "" : topic.getFirstEventAt().format(DateTimeFormatter.ISO_LOCAL_DATE));
        node.put("last_event_at", topic.getLastEventAt() == null
                ? "" : topic.getLastEventAt().format(DateTimeFormatter.ISO_LOCAL_DATE));
        return node;
    }

    private static String clip(String value, int max) {
        if (value == null) {
            return "";
        }
        return value.length() <= max ? value : value.substring(0, max) + "…";
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    /** 供决策侧判断"信息面是否值得带上"：全空就不注入（fail-open 的入口判断）。 */
    static boolean isEmptyBriefing(JsonNode briefing) {
        return briefing == null
                || (briefing.path("events").isEmpty() && briefing.path("topics").isEmpty()
                        && isBlank(briefing.path("market_read").asText("")));
    }
}
