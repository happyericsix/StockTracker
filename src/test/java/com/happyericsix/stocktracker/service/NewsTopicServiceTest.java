package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.NewsClient;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsTopic;
import com.happyericsix.stocktracker.entity.NewsTopicRel;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.NewsTopicRelRepository;
import com.happyericsix.stocktracker.repository.NewsTopicRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.lang.reflect.Field;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 事件线编排的纯单测（与 NewsServiceTest 同一套约定：Mockito，不开 Spring 容器）。
 *
 * <p>钉住的是升级方案点名的行为：
 * <ul>
 *   <li>聚类落库（新线创建 / 共享成员吸纳，editor 标题不被 code 覆写）；</li>
 *   <li>撰稿成功路径（summary/synthesizedAt 落库，预算记账）；</li>
 *   <li>每笔"没撰稿"都留痕且**原因专属**（封闭枚举逐一对应用例）。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class NewsTopicServiceTest {

    @Mock
    private NewsEventRepository newsEventRepository;
    @Mock
    private NewsTopicRepository newsTopicRepository;
    @Mock
    private NewsTopicRelRepository newsTopicRelRepository;
    @Mock
    private NewsClient newsClient;
    @Mock
    private PlatformTransactionManager transactionManager;

    private final ObjectMapper mapper = new ObjectMapper();
    private NewsTopicService service;

    @BeforeEach
    void setUp() throws Exception {
        service = new NewsTopicService(newsEventRepository, newsTopicRepository,
                newsTopicRelRepository, newsClient, mapper, transactionManager);
        set("topicsEnabled", true);
        set("editorEnabled", true);
        set("windowDays", 7);
        set("maxEvents", 500);
        set("dailyBudget", 30);
        set("cooldownMinutes", 90L);
    }

    private void set(String field, Object value) throws Exception {
        Field f = NewsTopicService.class.getDeclaredField(field);
        f.setAccessible(true);
        f.set(service, value);
    }

    // ==================== 夹具 ====================

    private NewsEvent event(long id, String title, int hoursAgo) {
        return NewsEvent.builder()
                .id(id).symbol("SH600519").name("贵州茅台").title(title)
                .sourceLevel(2).sourceName("证券时报网")
                .publishedAt(LocalDateTime.now().minusHours(hoursAgo))
                .direction("利好").plainSummary("一句解读")
                .build();
    }

    /** 仓储 save 桩：给新实体分配自增 id（linkMembers 要用 topic.getId()）。 */
    private void stubSaveAssignsIds() {
        AtomicLong seq = new AtomicLong(100);
        when(newsTopicRepository.save(any(NewsTopic.class))).thenAnswer(inv -> {
            NewsTopic topic = inv.getArgument(0);
            if (topic.getId() == null) {
                Field id = NewsTopic.class.getDeclaredField("id");
                id.setAccessible(true);
                id.set(topic, seq.incrementAndGet());
            }
            return topic;
        });
    }

    private ObjectNode cluster(String title, long... memberIds) {
        ObjectNode cluster = mapper.createObjectNode();
        cluster.put("title", title);
        cluster.put("member_count", memberIds.length);
        cluster.put("first_at", "2026-10-08 08:00:00");
        cluster.put("last_at", "2026-10-09 09:00:00");
        cluster.put("source_count", 2);
        ArrayNode members = cluster.putArray("members");
        for (long id : memberIds) {
            members.addObject().put("id", id);
        }
        ArrayNode symbols = cluster.putArray("symbols");
        symbols.add("SH600519");
        ArrayNode sources = cluster.putArray("sources");
        sources.add("东方财富·公告").add("证券时报网");
        return cluster;
    }

    private JsonNode okEnvelope(ObjectNode data) {
        ObjectNode root = mapper.createObjectNode();
        root.put("ok", true);
        root.set("data", data);
        return root;
    }

    private JsonNode clusterResponse(ObjectNode... clusters) {
        ObjectNode data = mapper.createObjectNode();
        ArrayNode array = data.putArray("clusters");
        for (ObjectNode cluster : clusters) {
            array.add(cluster);
        }
        data.put("unclustered_count", 0);
        return okEnvelope(data);
    }

    private JsonNode editorResponse(boolean worthDeep) {
        ObjectNode data = mapper.createObjectNode();
        data.put("ok", true);
        ArrayNode topics = data.putArray("topics");
        ObjectNode row = topics.addObject();
        row.put("index", 1);
        row.put("title", "茅台将开业绩说明会");
        row.put("worth_deep", worthDeep);
        row.put("reason", "公告+媒体跟进");
        return okEnvelope(data);
    }

    private JsonNode writerResponse(String story) {
        ObjectNode data = mapper.createObjectNode();
        data.put("ok", true);
        data.put("digest", "一句话结论");
        data.put("title", "茅台将开业绩说明会");
        data.put("story", story);
        data.put("outlook", "关注说明会表态。");
        data.put("direction", "利好");
        data.put("confidence", 0.6);
        ArrayNode affected = data.putArray("affected_symbols");
        affected.add("SH600519");
        return okEnvelope(data);
    }

    private void stubHappyPath(long... memberIds) {
        stubSaveAssignsIds();
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(event(1, "甲", 10), event(2, "乙", 5)));
        when(newsClient.cluster(anyList())).thenReturn(clusterResponse(cluster("代码标题", memberIds)));
        // rel 列表随 save 增长：linkMembers 落的成员，loadMembers（撰稿输入）要能读回来。
        // lenient：预算尽/低优先级等用例在撰稿前就 return，这三条桩按路径可能用不到
        //（strict 模式会揪出来，但"为每个用例拆一套夹具"比重复桩更难维护）。
        List<NewsTopicRel> rels = new ArrayList<>();
        lenient().when(newsTopicRelRepository.findByEventIdIn(anyList()))
                .thenAnswer(inv -> new ArrayList<>(rels));
        lenient().when(newsTopicRelRepository.findByTopicIdOrderByEventId(anyLong()))
                .thenAnswer(inv -> new ArrayList<>(rels));
        lenient().when(newsTopicRelRepository.save(any(NewsTopicRel.class))).thenAnswer(inv -> {
            rels.add(inv.getArgument(0));
            return inv.getArgument(0);
        });
        lenient().when(newsTopicRepository.countBySynthesizedAtGreaterThanEqual(any(LocalDateTime.class)))
                .thenReturn(0L);
        when(newsClient.topicEditor(anyList())).thenReturn(editorResponse(true));
        // 撰稿桩按用例自备：预算尽/低优先级等用例走不到撰稿，桩了会被 strict 模式揪出来
        // 撰稿要读成员事件
        lenient().when(newsEventRepository.findAllById(anyList()))
                .thenReturn(List.of(event(1, "甲", 10), event(2, "乙", 5)));
    }

    // ==================== 聚类落库 ====================

    @Test
    void newClusterCreatesTopicLinksMembersAndWrites() {
        stubHappyPath(1L, 2L);
        when(newsClient.topicWriter(anyString(), anyList()))
                .thenReturn(writerResponse("来龙去脉。（仅供参考，不构成投资建议）"));

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        NewsTopic topic = saved.getValue();
        assertEquals("茅台将开业绩说明会", topic.getTitle(), "总编标题应覆写代码标题");
        assertEquals("editor", topic.getTitleOrigin());
        assertEquals("一句话结论", topic.getDigest(), "一句话结论要落库（卡片摘要行靠它）");
        assertEquals("来龙去脉。（仅供参考，不构成投资建议）", topic.getSummary());
        assertEquals("利好", topic.getDirection());
        assertNotNull(topic.getSynthesizedAt(), "撰稿成功要记时间（预算记账靠它）");
        assertNull(topic.getSkipReason(), "撰稿成功清空未撰稿原因");
        assertTrue(topic.getSymbols().contains("SH600519"));
        // 成员关系落库（两条）
        verify(newsTopicRelRepository, atLeastOnce()).save(any(NewsTopicRel.class));
        // 撰稿输入只喂结论字段：不带 body/content
        ArgumentCaptor<List<JsonNode>> writerItems = ArgumentCaptor.captor();
        verify(newsClient).topicWriter(anyString(), writerItems.capture());
        assertFalse(writerItems.getValue().get(0).has("body"));
        assertTrue(writerItems.getValue().get(0).has("plain_summary"));
    }

    @Test
    void clusterFailureLeavesTopicsUntouched() {
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(event(1, "甲", 10), event(2, "乙", 5)));
        ObjectNode degraded = mapper.createObjectNode();
        degraded.put("ok", false).put("error", "聚类服务暂时不可用");
        when(newsClient.cluster(anyList())).thenReturn(degraded);

        service.runOnce();

        verify(newsTopicRepository, never()).save(any());
        verify(newsClient, never()).topicEditor(anyList());
    }

    @Test
    void fewerThanTwoEventsSkipsEverything() {
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(event(1, "甲", 1)));

        service.runOnce();

        verify(newsClient, never()).cluster(anyList());
    }

    @Test
    void sharedMemberClusterMergesIntoExistingTopicWithoutClobberingEditorTitle() {
        stubSaveAssignsIds();
        NewsEvent first = event(1, "甲", 10);
        NewsEvent second = event(2, "乙", 5);
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(first, second));
        when(newsClient.cluster(anyList()))
                .thenReturn(clusterResponse(cluster("老的代表标题", 1L, 2L)));
        // 已有专题：成员 1 已关联（共享成员 → 吸纳而非新建）。
        // rel 列表随 save 增长 —— 否则 countLinked 前后都读到 1，"新成员"永远测不出来。
        NewsTopic existing = NewsTopic.builder()
                .id(77L).title("总编起过的标题").titleOrigin("editor")
                .eventCount(1).sourceCount(1).status(NewsTopicContract.STATUS_CLOSED)
                .symbols("[\"SH600519\"]").sources("[\"证券时报网\"]")
                .summary("旧结论").synthesizedAt(LocalDateTime.now().minusHours(30))
                .lastEventAt(LocalDateTime.now().minusHours(10))
                .firstEventAt(LocalDateTime.now().minusHours(10))
                .skipReason("editor_cooldown")
                .build();
        List<NewsTopicRel> rels = new ArrayList<>(
                List.of(NewsTopicRel.builder().id(1L).topicId(77L).eventId(1L).build()));
        when(newsTopicRelRepository.findByEventIdIn(anyList())).thenReturn(new ArrayList<>(rels));
        when(newsTopicRepository.findById(77L)).thenReturn(Optional.of(existing));
        when(newsTopicRelRepository.findByTopicIdOrderByEventId(77L))
                .thenAnswer(inv -> new ArrayList<>(rels));
        when(newsTopicRelRepository.save(any(NewsTopicRel.class))).thenAnswer(inv -> {
            rels.add(inv.getArgument(0));
            return inv.getArgument(0);
        });
        when(newsTopicRepository.countBySynthesizedAtGreaterThanEqual(any(LocalDateTime.class))).thenReturn(0L);
        when(newsClient.topicEditor(anyList())).thenReturn(editorResponse(true));
        when(newsClient.topicWriter(anyString(), anyList())).thenReturn(writerResponse("更新后的来龙去脉。"));
        when(newsEventRepository.findAllById(anyList())).thenReturn(List.of(first, second));

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        NewsTopic topic = saved.getValue();
        assertEquals(77L, topic.getId(), "共享成员应吸纳进已有专题，不新建");
        assertEquals("茅台将开业绩说明会", topic.getTitle(),
                "标题只能被总编改（本轮总编给了新标题），code 代表标题不许覆写");
        assertNotEqualsByTitle(topic, "老的代表标题");
        assertEquals(NewsTopicContract.STATUS_OPEN, topic.getStatus(), "新成员到来要重开 closed 线");
        assertEquals(2, topic.getEventCount(), "成员数应更新为关联行数");
        assertEquals("更新后的来龙去脉。", topic.getSummary(), "冷却外的新成员应触发重写");
    }

    private static void assertNotEqualsByTitle(NewsTopic topic, String unexpected) {
        assertFalse(unexpected.equals(topic.getTitle()), () -> "不应出现 code 代表标题: " + unexpected);
    }

    // ==================== 没撰稿的原因逐一专属 ====================

    @Test
    void editorUnavailableMarksSkipReason() {
        stubSaveAssignsIds();
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(event(1, "甲", 10), event(2, "乙", 5)));
        when(newsClient.cluster(anyList())).thenReturn(clusterResponse(cluster("代码标题", 1L, 2L)));
        when(newsTopicRelRepository.findByEventIdIn(anyList())).thenReturn(new ArrayList<>());
        when(newsTopicRepository.countBySynthesizedAtGreaterThanEqual(any(LocalDateTime.class))).thenReturn(0L);
        ObjectNode degraded = mapper.createObjectNode();
        degraded.put("ok", true);
        ObjectNode data = mapper.createObjectNode();
        data.put("ok", false).put("error", "AI 服务未配置");
        degraded.set("data", data);
        when(newsClient.topicEditor(anyList())).thenReturn(degraded);

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        assertEquals(NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE, saved.getValue().getSkipReason());
        verify(newsClient, never()).topicWriter(anyString(), anyList());
    }

    @Test
    void editorLowPriorityMarksSkipReason() {
        stubHappyPath(1L, 2L);
        when(newsClient.topicEditor(anyList())).thenReturn(editorResponse(false));

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        assertEquals(NewsTopicContract.SKIP_EDITOR_LOW_PRIORITY, saved.getValue().getSkipReason());
        verify(newsClient, never()).topicWriter(anyString(), anyList());
    }

    @Test
    void budgetExhaustedMarksSkipReason() {
        stubHappyPath(1L, 2L);
        when(newsTopicRepository.countBySynthesizedAtGreaterThanEqual(any(LocalDateTime.class)))
                .thenReturn(30L);      // dailyBudget=30 已用满

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        assertEquals(NewsTopicContract.SKIP_EDITOR_BUDGET_EXCEEDED, saved.getValue().getSkipReason());
        verify(newsClient, never()).topicWriter(anyString(), anyList());
    }

    @Test
    void cooldownMarksSkipReasonAndKeepsOldSummary() {
        stubSaveAssignsIds();
        when(newsEventRepository.findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(event(1, "甲", 10), event(2, "乙", 5)));
        when(newsClient.cluster(anyList())).thenReturn(clusterResponse(cluster("代码标题", 1L, 2L)));
        // 已有专题：30 分钟前刚撰稿成功过（cooldown=90 分钟内），本轮又带来新成员
        NewsTopic existing = NewsTopic.builder()
                .id(77L).title("标题").titleOrigin("editor")
                .eventCount(1).sourceCount(1).status(NewsTopicContract.STATUS_OPEN)
                .summary("旧结论").synthesizedAt(LocalDateTime.now().minusMinutes(30))
                .lastEventAt(LocalDateTime.now().minusHours(1))
                .firstEventAt(LocalDateTime.now().minusHours(1))
                .build();
        List<NewsTopicRel> rels = new ArrayList<>(
                List.of(NewsTopicRel.builder().id(1L).topicId(77L).eventId(1L).build()));
        when(newsTopicRelRepository.findByEventIdIn(anyList())).thenReturn(new ArrayList<>(rels));
        when(newsTopicRepository.findById(77L)).thenReturn(Optional.of(existing));
        when(newsTopicRelRepository.findByTopicIdOrderByEventId(77L))
                .thenAnswer(inv -> new ArrayList<>(rels));
        when(newsTopicRelRepository.save(any(NewsTopicRel.class))).thenAnswer(inv -> {
            rels.add(inv.getArgument(0));
            return inv.getArgument(0);
        });
        when(newsTopicRepository.countBySynthesizedAtGreaterThanEqual(any(LocalDateTime.class))).thenReturn(0L);
        when(newsClient.topicEditor(anyList())).thenReturn(editorResponse(true));

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        assertEquals(NewsTopicContract.SKIP_EDITOR_COOLDOWN, saved.getValue().getSkipReason());
        assertEquals("旧结论", saved.getValue().getSummary(), "冷却期内老结论先顶着");
        verify(newsClient, never()).topicWriter(anyString(), anyList());
    }

    @Test
    void writerFailureMarksSkipReasonForRetry() {
        stubHappyPath(1L, 2L);
        ObjectNode degraded = mapper.createObjectNode();
        degraded.put("ok", true);
        ObjectNode data = mapper.createObjectNode();
        data.put("ok", false).put("error", "AI 服务暂时不可用");
        degraded.set("data", data);
        when(newsClient.topicWriter(anyString(), anyList())).thenReturn(degraded);

        service.runOnce();

        ArgumentCaptor<NewsTopic> saved = ArgumentCaptor.forClass(NewsTopic.class);
        verify(newsTopicRepository, atLeastOnce()).save(saved.capture());
        assertEquals(NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE, saved.getValue().getSkipReason());
        assertNull(saved.getValue().getSynthesizedAt());
    }

    // ==================== 查询（列表过滤） ====================

    @Test
    void listTopicsFiltersBySymbolJsonContains() {
        NewsTopic hit = NewsTopic.builder().id(1L).title("茅台线").eventCount(2).sourceCount(2)
                .status(NewsTopicContract.STATUS_OPEN).titleOrigin("code")
                .symbols("[\"SH600519\"]").lastEventAt(LocalDateTime.now())
                .summary("有综合结论").digest("一句话")
                .build();
        NewsTopic miss = NewsTopic.builder().id(2L).title("宏观线").eventCount(3).sourceCount(2)
                .status(NewsTopicContract.STATUS_OPEN).titleOrigin("code")
                .symbols("[\"SZ000001\"]").lastEventAt(LocalDateTime.now())
                .summary("有综合结论").digest("一句话")
                .build();
        when(newsTopicRepository.findActiveSince(any(LocalDateTime.class),
                any(org.springframework.data.domain.Pageable.class))).thenReturn(List.of(hit, miss));

        assertEquals(List.of(1L), ids(service.listTopics(7, "600519", false, false)),
                "600519 应折算成 SH600519 并按 JSON 数组包含匹配");
        assertEquals(List.of(1L, 2L), ids(service.listTopics(7, "", false, false)),
                "不带 symbol 看全部");
    }

    /**
     * 2026-10-09 用户实测反馈"整页专题没人看"后的修正：默认只返回已撰稿的线。
     * 未撰稿的线大半是总编判定"信息量不足"的程序性披露 —— 编辑都说没有
     * 增量了，就不该占页面位置；这条契约由本用例钉住。
     */
    @Test
    void listTopicsHidesUnsynthesizedByDefault() {
        NewsTopic written = NewsTopic.builder().id(1L).title("已撰稿").eventCount(2).sourceCount(1)
                .status(NewsTopicContract.STATUS_OPEN).titleOrigin("editor")
                .summary("结论").digest("一句话").lastEventAt(LocalDateTime.now()).build();
        NewsTopic pending = NewsTopic.builder().id(2L).title("未撰稿").eventCount(2).sourceCount(1)
                .status(NewsTopicContract.STATUS_OPEN).titleOrigin("code")
                .skipReason("editor_skip_low_priority").lastEventAt(LocalDateTime.now()).build();
        when(newsTopicRepository.findActiveSince(any(LocalDateTime.class),
                any(org.springframework.data.domain.Pageable.class)))
                .thenReturn(List.of(written, pending));

        assertEquals(List.of(1L), ids(service.listTopics(7, "", false, false)),
                "默认隐藏未撰稿的线");
        assertEquals(List.of(1L, 2L), ids(service.listTopics(7, "", false, true)),
                "includeUnsynthesized 打开全量（调试用）");
    }

    private static List<Long> ids(List<NewsTopic> topics) {
        return topics.stream().map(NewsTopic::getId).toList();
    }
}
