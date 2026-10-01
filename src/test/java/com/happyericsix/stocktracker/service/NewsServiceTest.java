package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.client.NewsClient;
import com.happyericsix.stocktracker.dto.NewsEventResponse;
import com.happyericsix.stocktracker.dto.NewsSearchRequest;
import com.happyericsix.stocktracker.dto.StockNewsTimelineResponse;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsStockRel;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.NewsStockRelRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageImpl;
import org.springframework.transaction.PlatformTransactionManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executor;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyBoolean;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * N3 的纯单测（约定 8）：{@code @Mock} + {@code @InjectMocks}，
 * **不开 Spring 容器、不用 MockMvc**。
 *
 * <p>钉住的是施工图 §N3「验收」点名的那几条：
 * <ul>
 *   <li>{@code upsert} 去重（同 url 不重复、重抓不覆盖已分析内容）；</li>
 *   <li>{@code search} 库命中**不发请求** / 库空**触发兜底**；</li>
 *   <li>{@code scope=mine} 只返回自选相关（查询范围被自选代码约束住）；</li>
 *   <li>{@code analyze} 幂等（已有解读不重复花模型调用）。</li>
 * </ul>
 */
@ExtendWith(MockitoExtension.class)
class NewsServiceTest {

    @Mock
    private NewsEventRepository newsEventRepository;

    @Mock
    private NewsStockRelRepository newsStockRelRepository;

    @Mock
    private FavoriteStockRepository favoriteStockRepository;

    @Mock
    private UserRepository userRepository;

    @Mock
    private NewsClient newsClient;

    /**
     * 只为综合解读取一次最新行情。用例不桩它时 `getStockQuote` 返回 null，
     * `quoteOf` 会走 catch 分支返回空 Map —— 这正是"行情取不到不该影响资讯结论"的行为。
     */
    @Mock
    private AkshareStockClient stockClient;

    /** 事务模板用的是它的 mock：{@code TransactionTemplate} 在 getTransaction 返回 null 时照常执行回调。 */
    @Mock
    private PlatformTransactionManager transactionManager;

    /**
     * 补数据执行器在本用例里是**同步**的（{@code Runnable::run}）。
     *
     * <p>被测的是"抓什么、补什么、补多少条"，不是线程调度 —— 真异步会让断言变成
     * "等一会儿再看"，那是最脆的一类用例（在慢机器上偶发翻绿/翻红，而且失败信息
     * 完全指不出原因）。同步执行器同时把"后台任务会在返回前跑完"这条边界也覆盖到了
     * （{@code timeline} 里那段 {@code scheduled && !analyzing} 的重读就是为它写的）。
     */
    private NewsService newsService;

    @BeforeEach
    void setUp() {
        newsService = new NewsService(
                newsEventRepository, newsStockRelRepository, favoriteStockRepository,
                userRepository, newsClient, stockClient, transactionManager, Runnable::run);
    }

    private final ObjectMapper mapper = new ObjectMapper();

    // ==================== upsert 去重 ====================

    @Test
    void upsertSkipsTheSameUrlAndNeverOverwritesExistingAnalysis() {
        ObjectNode item = eventItem("SH600519", "贵州茅台：2026年中报", "https://finance.eastmoney.com/a/1.html",
                "2026-09-14 10:00:00", 2);
        NewsEvent existing = NewsEvent.builder()
                .id(7L)
                .symbol("SH600519")
                .title("贵州茅台：2026年中报")
                .url("https://finance.eastmoney.com/a/1.html")
                .sourceLevel(2)
                .direction("利好")
                .plainSummary("已生成的解读（重抓不许覆盖）")
                .publishedAt(LocalDateTime.of(2026, 9, 14, 10, 0))
                .build();

        when(newsEventRepository.findByUrl("https://finance.eastmoney.com/a/1.html"))
                .thenReturn(Optional.of(existing));
        when(newsStockRelRepository.existsByEventIdAndSymbol(7L, "SH600519")).thenReturn(true);

        int added = newsService.upsert(List.of(item));

        assertEquals(0, added, "同 url 不该重复入库");
        // 「只增不改」管的是**分析结论**：解读与方向一个字都不能被重抓覆盖。
        // 唯一允许写回的是**缺失的展示字段**（name）—— 展示层要靠它判断"这条是否关于这只票"。
        ArgumentCaptor<NewsEvent> saved = ArgumentCaptor.forClass(NewsEvent.class);
        verify(newsEventRepository).save(saved.capture());
        assertEquals("已生成的解读（重抓不许覆盖）", saved.getValue().getPlainSummary());
        assertEquals("利好", saved.getValue().getDirection());
        assertEquals("贵州茅台", saved.getValue().getName(), "缺失的股票名应被补上");
        verify(newsStockRelRepository, never()).save(any(NewsStockRel.class));
    }

    @Test
    void upsertSavesANewEventAndCreatesTheStockRelation() {
        ObjectNode item = eventItem("600519", "捷捷微电：关于调整激励计划的公告",
                "https://data.eastmoney.com/notices/detail/300623/AN2026.html?from=app", "2026-09-14 09:30:00", 1);

        when(newsEventRepository.findByUrl(
                "https://data.eastmoney.com/notices/detail/300623/AN2026.html")).thenReturn(Optional.empty());
        when(newsEventRepository.save(any(NewsEvent.class))).thenAnswer(invocation -> {
            NewsEvent event = invocation.getArgument(0);
            event.setId(42L);
            return event;
        });
        when(newsStockRelRepository.existsByEventIdAndSymbol(42L, "SH600519")).thenReturn(false);

        int added = newsService.upsert(List.of(item));

        assertEquals(1, added);
        ArgumentCaptor<NewsEvent> captor = ArgumentCaptor.forClass(NewsEvent.class);
        verify(newsEventRepository).save(captor.capture());
        NewsEvent saved = captor.getValue();
        assertEquals("SH600519", saved.getSymbol(), "代码必须归一化成库里存的形态");
        assertEquals(Integer.valueOf(1), saved.getSourceLevel(), "1 = 公告");
        assertEquals(LocalDateTime.of(2026, 9, 14, 9, 30), saved.getPublishedAt());
        // url 里的 tracking 参数必须先被去掉，否则唯一索引挡不住"同一篇两副面孔"
        assertEquals("https://data.eastmoney.com/notices/detail/300623/AN2026.html", saved.getUrl());
        verify(newsStockRelRepository).save(argThat(rel ->
                Long.valueOf(42L).equals(rel.getEventId()) && "SH600519".equals(rel.getSymbol())));
    }

    @Test
    void upsertFallsBackToTheTitleTripleWhenThereIsNoUrl() {
        ObjectNode item = eventItem("SH600519", "关于回购公司股份的公告", "", "2026-09-14 09:00:00", 1);
        NewsEvent existing = NewsEvent.builder()
                .id(7L)
                .symbol("SH600519")
                .title("关于回购公司股份的公告")
                .sourceLevel(1)
                .publishedAt(LocalDateTime.of(2026, 9, 14, 9, 0))
                .build();

        when(newsEventRepository.findByTitle("关于回购公司股份的公告")).thenReturn(List.of(existing));
        when(newsStockRelRepository.existsByEventIdAndSymbol(7L, "SH600519")).thenReturn(true);

        assertEquals(0, newsService.upsert(List.of(item)), "无 url 时靠 (symbol,title,published_at) 去重");
        // 该条目已经有 name（fixture 里本是空的），补 name 之外的字段一律不许写 ——
        // 具体的"哪些能改"由 upsertSkipsTheSameUrlAndNeverOverwritesExistingAnalysis 钉住
        verify(newsStockRelRepository, never()).save(any(NewsStockRel.class));
    }

    @Test
    void upsertDoesNotBackfillNameWhenItIsAlreadyPresent() {
        ObjectNode item = eventItem("SH600519", "关于回购公司股份的公告", "", "2026-09-14 09:00:00", 1);
        NewsEvent existing = NewsEvent.builder()
                .id(7L).symbol("SH600519").name("贵州茅台")
                .title("关于回购公司股份的公告").sourceLevel(1)
                .publishedAt(LocalDateTime.of(2026, 9, 14, 9, 0))
                .build();
        when(newsEventRepository.findByTitle("关于回购公司股份的公告")).thenReturn(List.of(existing));
        when(newsStockRelRepository.existsByEventIdAndSymbol(7L, "SH600519")).thenReturn(true);

        newsService.upsert(List.of(item));

        // 已经有名字就一个字都不用写：这条路径上的写库只服务于"补缺失的展示字段"
        verify(newsEventRepository, never()).save(any(NewsEvent.class));
    }

    // ==================== search：库优先 + 兜底 ====================

    @Test
    void searchHitsTheDatabaseAndNeverCallsTheUpstreamWhenTheLatestRowIsFromToday() {
        NewsEvent fresh = event(1L, "SH600519", "今日公告", LocalDateTime.now().minusHours(1));
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(new PageImpl<>(List.of(fresh)));
        when(newsEventRepository.latestPublishedAt(anyList())).thenReturn(LocalDateTime.now().minusHours(1));

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest("600519", null, null, null));

        assertEquals(1, result.getTotalElements());
        assertEquals("SH600519", result.getContent().get(0).getSymbol());
        verifyNoInteractions(newsClient);
    }

    @Test
    void searchFallsBackToTheUpstreamWhenTheDatabaseHasNothingAndReQueriesAfterwards() {
        NewsEvent stored = event(9L, "SH600519", "库空时兜底抓到的新闻", LocalDateTime.now());
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(Page.empty())
                .thenReturn(new PageImpl<>(List.of(stored)));
        when(newsClient.fetch("600519", null, null, 7, true, true))
                .thenReturn(fetchOk(eventItem("600519", "库空时兜底抓到的新闻",
                        "https://finance.eastmoney.com/a/9.html", "2026-09-14 10:30:00", 2)));
        when(newsEventRepository.findByUrl("https://finance.eastmoney.com/a/9.html")).thenReturn(Optional.empty());
        when(newsEventRepository.save(any(NewsEvent.class))).thenAnswer(invocation -> {
            NewsEvent event = invocation.getArgument(0);
            event.setId(9L);
            return event;
        });
        when(newsStockRelRepository.existsByEventIdAndSymbol(9L, "SH600519")).thenReturn(false);

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest("600519", null, null, null));

        // withBody=true：库里没有这只票的公告时只能现抓，而公告没有正文时 AI 只能回"信息不足"
        verify(newsClient).fetch("600519", null, null, 7, true, true);
        verify(newsEventRepository).save(any(NewsEvent.class));
        assertEquals(1, result.getTotalElements(), "兜底落库后必须再查一次库");
        assertEquals("库空时兜底抓到的新闻", result.getContent().get(0).getTitle());
    }

    @Test
    void searchStillReturnsStoredRowsWhenTheRefreshFails() {
        NewsEvent stale = event(3L, "SH600519", "上周的公告", LocalDateTime.now().minusDays(3));
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(new PageImpl<>(List.of(stale)));
        // 最新一条早于今天 → 需要刷新（否则用户永远看不到今天的新公告）
        when(newsEventRepository.latestPublishedAt(anyList())).thenReturn(LocalDateTime.now().minusDays(3));
        when(newsClient.fetch("600519", null, null, 7, true, true)).thenReturn(degraded("资讯服务暂时不可用"));

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest("600519", null, null, null));

        // "库里有结果但刷新失败" 必须与 "彻底没结果" 分开：库里那几条照常返回，且不抛异常
        assertEquals(1, result.getTotalElements());
        assertEquals("上周的公告", result.getContent().get(0).getTitle());
        verify(newsEventRepository, never()).save(any(NewsEvent.class));
    }

    @Test
    void searchReturnsAnEmptyPageWhenTheUpstreamIsDownAndTheDatabaseIsEmpty() {
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(Page.empty());
        when(newsClient.fetch("600519", null, null, 7, true, true)).thenReturn(degraded("资讯服务暂时不可用"));

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest("600519", null, null, null));

        assertTrue(result.isEmpty());
        verify(newsEventRepository, never()).save(any(NewsEvent.class));
    }

    // ==================== scope=mine ====================

    @Test
    @SuppressWarnings("unchecked")
    void scopeMineOnlyQueriesTheFavouriteSymbolsOfThatUser() {
        User user = User.builder().id(1L).username("alice").build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(favoriteStockRepository.findByUserId(1L)).thenReturn(List.of(
                FavoriteStock.builder().id(11L).stockSymbol("600519").build()));
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(new PageImpl<>(List.of(event(5L, "SH600519", "自选相关", LocalDateTime.now()))));

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest(null, null, null, "mine"));

        ArgumentCaptor<List<String>> symbols = ArgumentCaptor.forClass(List.class);
        verify(newsEventRepository).searchBySymbols(any(), anyList(), anyString(), symbols.capture(), any());
        // 自选里录的是裸代码 600519，库里存的是 SH600519 —— 两种写法都要带上，否则一半查不到
        assertTrue(symbols.getValue().containsAll(List.of("SH600519", "600519")), symbols.getValue().toString());
        assertEquals(1, result.getTotalElements());
        assertEquals("SH600519", result.getContent().get(0).getSymbol());
        verifyNoInteractions(newsClient);
    }

    @Test
    void scopeMineWithNoFavouritesReturnsEmptyInsteadOfScanningTheWholeMarket() {
        User user = User.builder().id(1L).username("alice").build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user));
        when(favoriteStockRepository.findByUserId(1L)).thenReturn(List.of());

        Page<NewsEventResponse> result = newsService.search("alice", searchRequest(null, null, null, "mine"));

        assertTrue(result.isEmpty(), "没有自选时答案是『没有相关事件』，不是『全市场事件』");
        verifyNoInteractions(newsEventRepository, newsClient);
    }

    @Test
    void searchRejectsAnUnknownScopeInsteadOfSilentlyIgnoringIt() {
        NewsSearchRequest request = searchRequest(null, null, null, "all");
        assertThrows(IllegalArgumentException.class, () -> newsService.search("alice", request));
        verifyNoInteractions(newsEventRepository);
    }

    // ==================== analyze 幂等 ====================

    @Test
    void analyzeSkipsEventsThatAlreadyHaveASummaryUnlessForced() {
        NewsEvent analyzed = NewsEvent.builder()
                .id(1L).symbol("SH600519").title("已解读").sourceLevel(2)
                .plainSummary("早就解读过了")
                .direction("中性")
                .publishedAt(LocalDateTime.now())
                .build();
        when(newsEventRepository.findAllById(List.of(1L))).thenReturn(List.of(analyzed));

        List<NewsEventResponse> result = newsService.analyze("alice", List.of(1L), false);

        assertEquals(1, result.size());
        assertTrue(result.get(0).isAnalyzed());
        verifyNoInteractions(newsClient);
    }

    @Test
    void analyzeBackfillsTheStructuredFieldsAndTheRelatedSymbols() {
        NewsEvent event = NewsEvent.builder()
                .id(5L).symbol("SH600519").title("重大合同公告").content("公告正文")
                .sourceLevel(1).publishedAt(LocalDateTime.now())
                .build();
        when(newsEventRepository.findAllById(List.of(5L))).thenReturn(List.of(event));
        when(newsClient.analyze(anyList(), eq("deep"))).thenReturn(analyzeOk(
                analysisRow(true, "重大合同", "利好", 0.82, "high", "拿到大单，对全年营收有实质影响",
                        new String[]{"毛利可能被摊薄"}, new String[]{"产能利用率提升"},
                        new String[]{"SH600519", "SZ000858"})));
        when(newsEventRepository.save(any(NewsEvent.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(newsStockRelRepository.existsByEventIdAndSymbol(5L, "SH600519")).thenReturn(true);
        when(newsStockRelRepository.existsByEventIdAndSymbol(5L, "SZ000858")).thenReturn(false);

        List<NewsEventResponse> result = newsService.analyze("alice", List.of(5L), false);

        NewsEventResponse response = result.get(0);
        assertEquals("重大合同", response.getEventType());
        assertEquals("利好", response.getDirection());
        assertEquals(0.82, response.getConfidence(), 0.0001);
        assertEquals("high", response.getImpactLevel());
        assertEquals(List.of("毛利可能被摊薄"), response.getRisks());
        assertEquals(List.of("产能利用率提升"), response.getOpportunities());
        assertEquals(List.of("SH600519", "SZ000858"), response.getRelatedSymbols());
        assertTrue(response.isAnalyzed());
        // 分析出的关联代码要落进 news_stock_rel：宏观/行业资讯才可能出现在个股时间轴里
        verify(newsStockRelRepository).save(argThat(rel -> "SZ000858".equals(rel.getSymbol())));
    }

    @Test
    void analyzeLeavesTheEventUntouchedWhenTheModelDegrades() {
        NewsEvent event = NewsEvent.builder()
                .id(6L).symbol("SH600519").title("信息不足的公告").sourceLevel(1)
                .publishedAt(LocalDateTime.now())
                .build();
        when(newsEventRepository.findAllById(List.of(6L))).thenReturn(List.of(event));
        when(newsClient.analyze(anyList(), eq("deep"))).thenReturn(analyzeOk(
                analysisRow(false, "其他", null, 0.0, "low", "信息不足，不判断方向",
                        new String[0], new String[0], new String[]{"SH600519"})));

        List<NewsEventResponse> result = newsService.analyze("alice", List.of(6L), false);

        // 降级结果不落库：落库就等于把它标成"已解读"，幂等键会让它以后再也不重试
        verify(newsEventRepository, never()).save(any(NewsEvent.class));
        assertNull(result.get(0).getPlainSummary());
        assertFalse(result.get(0).isAnalyzed());
    }

    @Test
    void analyzeRejectsUnknownEventIds() {
        when(newsEventRepository.findAllById(List.of(99L))).thenReturn(List.of());

        IllegalArgumentException error = assertThrows(IllegalArgumentException.class,
                () -> newsService.analyze("alice", List.of(99L), false));

        assertEquals("新闻不存在", error.getMessage());
        verifyNoInteractions(newsClient);
    }

    // ==================== 个股时间轴 ====================

    @Test
    void timelineMergesOwnAndRelatedEventsInReverseChronologicalOrder() {
        NewsEvent own = event(1L, "SH600519", "自家公告", LocalDateTime.of(2026, 9, 14, 9, 0));
        NewsEvent related = NewsEvent.builder()
                .id(2L).symbol(null).title("白酒行业政策").sourceLevel(2)
                .publishedAt(LocalDateTime.of(2026, 9, 14, 11, 0))
                .build();
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(own));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of(1L, 2L));
        when(newsEventRepository.findAllById(List.of(2L))).thenReturn(List.of(related));

        StockNewsTimelineResponse result = newsService.timeline("alice", "600519", 30);

        assertEquals(List.of(2L, 1L), result.events().stream().map(NewsEventResponse::getId).toList(),
                "宏观/行业资讯（无 symbol）经关联表进来，且时间倒序");
    }

    @Test
    void timelineRequiresASymbol() {
        assertThrows(IllegalArgumentException.class, () -> newsService.timeline("alice", " ", 30));
    }

    // ==================== 手动增量 ====================

    @Test
    void refreshStoresTheDailyIncrementThroughTheSameUpsertPath() {
        when(newsClient.fetch("", "", null, 1, false, false)).thenReturn(fetchOk(
                eventItem("SH600519", "今日新公告", "https://data.eastmoney.com/notices/detail/1.html",
                        "2026-09-14 08:00:00", 1)));
        when(newsEventRepository.findByUrl("https://data.eastmoney.com/notices/detail/1.html"))
                .thenReturn(Optional.empty());
        when(newsEventRepository.save(any(NewsEvent.class))).thenAnswer(invocation -> {
            NewsEvent event = invocation.getArgument(0);
            event.setId(77L);
            return event;
        });
        when(newsStockRelRepository.existsByEventIdAndSymbol(77L, "SH600519")).thenReturn(false);

        String message = newsService.refresh(null);

        assertTrue(message.contains("新入库 1 条"), message);
    }

    @Test
    void refreshRejectsAMalformedDay() {
        assertThrows(IllegalArgumentException.class, () -> newsService.refresh("2026-09-14"));
        verifyNoInteractions(newsClient);
    }

    // ============ 冷却与时间轴兜底（2026-09-19 端到端冒烟实测补充）============
    //
    // 这一组钉住的是冒烟实测出来的两个缺陷：
    // ① 对"今天没有新公告"的股票，needsRefresh 的判据**永远成立** → 每次请求都重新兜底
    //    （实测第二次仍调上游、3.3s；施工图 §N3 的验收"第二次直接命中库"因此不成立）。
    // ② GET /news/events 只读库 → 新库上个股页恒为空（实测 symbol=000001 返回 0 条），
    //    且落库条目都没有解读（实测 11 条全部 analyzed=false）→ 页面渲染成纯标题列表。

    @Test
    void searchDoesNotHitTheUpstreamAgainWithinTheCooldownWindow() {
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(Page.empty());
        when(newsClient.fetch("600519", null, null, 7, true, true)).thenReturn(fetchOk());

        newsService.search("alice", searchRequest("600519", null, null, null));
        newsService.search("alice", searchRequest("600519", null, null, null));

        verify(newsClient, times(1)).fetch("600519", null, null, 7, true, true);
    }

    @Test
    void enrichCooldownIsPerSymbol() {
        when(newsEventRepository.searchBySymbols(any(), anyList(), anyString(), anyList(), any()))
                .thenReturn(Page.empty());
        when(newsClient.fetch(anyString(), any(), any(), eq(7), eq(true), eq(true)))
                .thenReturn(fetchOk());

        newsService.search("alice", searchRequest("600519", null, null, null));
        newsService.search("alice", searchRequest("000001", null, null, null));

        verify(newsClient, times(2)).fetch(anyString(), any(), any(), eq(7), eq(true), eq(true));
    }

    @Test
    void timelineFallsBackToTheUpstreamWhenTheDatabaseHasNothing() {
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of());
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        when(newsClient.fetch("000001", "", null, 30, true, true)).thenReturn(fetchOk(
                eventItem("SZ000001", "平安银行：回购公告", "https://x.com/a", "2026-09-19 09:00:00", 1)));
        when(newsEventRepository.findByUrl("https://x.com/a")).thenReturn(Optional.empty());
        when(newsEventRepository.save(any(NewsEvent.class))).thenAnswer(invocation -> {
            NewsEvent saved = invocation.getArgument(0);
            saved.setId(11L);
            return saved;
        });
        when(newsStockRelRepository.existsByEventIdAndSymbol(11L, "SZ000001")).thenReturn(false);

        newsService.timeline("alice", "000001", 30);

        verify(newsClient, times(1)).fetch("000001", "", null, 30, true, true);
    }

    @Test
    void timelineCooldownPreventsRepeatedWorkOnReload() {
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of());
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        when(newsClient.fetch("000001", "", null, 30, true, true)).thenReturn(fetchOk());

        newsService.timeline("alice", "000001", 30);
        newsService.timeline("alice", "000001", 30);

        verify(newsClient, times(1)).fetch("000001", "", null, 30, true, true);
    }

    @Test
    void timelineBackfillsPendingSummariesWithOneBatchCall() {
        // publishedAt 是今天 → 不触发兜底抓取，只补解读
        NewsEvent fresh = event(1L, "SH600519", "今日回购公告", LocalDate.now().atTime(9, 0));
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(fresh));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        when(newsClient.analyze(anyList(), eq("batch"))).thenReturn(analyzeOk(
                analysisRow(true, "回购", "利好", 0.8, "high", "公司拟回购 10 亿元",
                        new String[]{}, new String[]{}, new String[]{"SH600519"})));
        when(newsEventRepository.save(any(NewsEvent.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        StockNewsTimelineResponse result = newsService.timeline("alice", "600519", 30);

        verify(newsClient, never()).fetch(anyString(), any(), any(), anyInt(), anyBoolean(), anyBoolean());
        verify(newsClient, times(1)).analyze(anyList(), eq("batch"));
        assertTrue(result.events().get(0).isAnalyzed(), "补解读后个股页才有「这条意味着什么」");
        assertEquals("利好", result.events().get(0).getDirection());
        assertFalse(result.analyzing(), "同步执行器下补数据在返回前就结束了，没有在途任务");
        assertEquals(1, result.analyzedCount());
    }

    @Test
    void timelineAnalyzesAtMostTheConfiguredLimit() {
        List<NewsEvent> many = new ArrayList<>();
        // 造**超过**上限的条数（35），这样断言的是"真的被截断"，而不是"恰好全都分析"
        for (int i = 1; i <= 35; i++) {
            many.add(event((long) i, "SH600519", "公告" + i,
                    LocalDate.now().atTime(9, 0).minusMinutes(i)));
        }
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(many);
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        when(newsClient.analyze(anyList(), eq("batch"))).thenReturn(analyzeOk());

        newsService.timeline("alice", "600519", 30);

        // 2026-09-20：30 条不再一次交给 Python（那要串行 6 次模型调用，会撞上
        // NewsClient 的 30 秒读超时 → 整批一起丢），而是 Java 侧按 5 条一批分开发。
        // 钉住的仍然是"上限 30"这件事：把每次调用的条数**加起来**必须是 30。
        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<JsonNode>> captor = ArgumentCaptor.forClass(List.class);
        verify(newsClient, times(6)).analyze(captor.capture(), eq("batch"));
        int total = captor.getAllValues().stream().mapToInt(List::size).sum();
        assertEquals(30, total, "上限 30 条：只为可见条目花模型调用，且不能无限膨胀");
        assertTrue(captor.getAllValues().stream().allMatch(batch -> batch.size() <= 5),
                "每批不超过 Python 的 BATCH_SIZE(5)：一次 HTTP 调用只对应一次模型往返");
    }

    @Test
    void timelineAnalyzesTheNewestEventsFirst() {
        // 分批之后"先补哪一批"变成了可见的行为：先补旧的，用户要等到第 6 批才看到
        // 最近那条新闻的解读 —— 而"最近发生的事"才是他点进来的原因。
        List<NewsEvent> many = new ArrayList<>();
        for (int i = 1; i <= 8; i++) {
            many.add(event((long) i, "SH600519", "公告" + i,
                    LocalDate.now().atTime(9, 0).minusMinutes(i)));
        }
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(many);
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        when(newsClient.analyze(anyList(), eq("batch"))).thenReturn(analyzeOk());

        newsService.timeline("alice", "600519", 30);

        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<JsonNode>> captor = ArgumentCaptor.forClass(List.class);
        verify(newsClient, times(2)).analyze(captor.capture(), eq("batch"));
        String firstTitle = captor.getAllValues().get(0).get(0).path("title").asText();
        assertEquals("公告1", firstTitle, "第 1 批必须是发布时间最新的那几条");
    }

    // ============ 展示用相关度：不解读的条目不该摆给用户看 ============

    @Test
    void timelineHidesMediaThatOnlyMentionsTheStock() {
        // 用户的原话："这么多新闻还是不能用 AI 去解读，那我设置的意义是什么" ——
        // 把不解读的条目继续摆在页面上，等于逼用户自己分辨哪些值得看。
        NewsEvent roundup = mediaAboutStock(1L, "SH600519", "贵州茅台",
                "深沪北百元股数量达217只，科创板股票占46.08%",
                LocalDate.now().atTime(9, 0));
        NewsEvent real = mediaAboutStock(2L, "SH600519", "贵州茅台",
                "贵州茅台被执行158万元？公司回应", LocalDate.now().atTime(8, 0));
        NewsEvent notice = event(3L, "SH600519", "关于回购公司股份的公告", LocalDate.now().atTime(7, 0));

        when(newsEventRepository.findTimeline(anyList(), any(), any()))
                .thenReturn(List.of(roundup, real, notice));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());

        StockNewsTimelineResponse result = newsService.timeline("alice", "600519", 90);

        assertEquals(List.of(2L, 3L), result.events().stream().map(NewsEventResponse::getId).toList(),
                "大盘综述被挡掉，关于这只票的媒体与公告照常返回");
    }

    @Test
    void timelineKeepsAlreadyAnalyzedMediaEvenWithoutAName() {
        // 早期入库的媒体条目没有 name 列值，而展示层要靠它判断相关度 ——
        // 少了 name，一条**已经花过模型调用解读过**的真新闻会被误当成大盘综述挡掉（实测发生过）。
        // 所以"已经有解读"是最可靠的一道依据，优先级高于 name/title 匹配。
        NewsEvent legacy = mediaAboutStock(5L, "SH600519", null,
                "被执行158万元？贵州茅台：系第三方公司内部合同纠纷", LocalDate.now().atTime(9, 0));
        legacy.setPlainSummary("公司澄清，对经营无实质影响。（仅供参考，不构成投资建议）");
        NewsEvent roundup = mediaAboutStock(6L, "SH600519", null, "深沪北百元股数量达217只",
                LocalDate.now().atTime(8, 0));

        when(newsEventRepository.findTimeline(anyList(), any(), any()))
                .thenReturn(List.of(legacy, roundup));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());

        StockNewsTimelineResponse result = newsService.timeline("alice", "600519", 90);

        assertEquals(List.of(5L), result.events().stream().map(NewsEventResponse::getId).toList(),
                "已解读的保留；未解读且判不出相关度的大盘综述挡掉");
    }

    @Test
    void timelineKeepsMacroItemsItCannotJudge() {
        // 宏观/行业资讯经关联表进来时没有 symbol 也没有 name —— 无从判断相关度时**保留**。
        // 因为"我不知道这只票叫什么"不等于"这条新闻与它无关"，
        // 而"真正影响股价但不写代码"的恰恰是政策与行业新闻。
        NewsEvent macro = NewsEvent.builder()
                .id(9L).symbol(null).name(null).title("央行宣布降准 0.5 个百分点").sourceLevel(2)
                .publishedAt(LocalDate.now().atTime(9, 0))
                .build();
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of());
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of(9L));
        when(newsEventRepository.findAllById(List.of(9L))).thenReturn(List.of(macro));

        StockNewsTimelineResponse result = newsService.timeline("alice", "600519", 90);

        assertEquals(List.of(9L), result.events().stream().map(NewsEventResponse::getId).toList());
    }

    // ============ 综合解读：读完这些新闻之后的一段判断 ============

    @Test
    void stockReadReturnsTheSynthesizedParagraph() {
        NewsEvent notice = event(1L, "SH600519", "关于回购公司股份的公告", LocalDate.now().atTime(9, 0));
        notice.setPlainSummary("公司拟回购 10 亿元（仅供参考，不构成投资建议）");
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(notice));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        ObjectNode synthesis = envelope();
        synthesis.withObject("/data").put("ok", true).put("read", "信息面偏中性，没有新变量。");
        when(newsClient.synthesize(eq("SH600519"), any(), any(), anyList())).thenReturn(synthesis);

        String read = newsService.stockRead("alice", "600519", 90);

        assertEquals("信息面偏中性，没有新变量。", read);
        // 第二次必须命中缓存：同一批事件的结论不会变，不该再付一次 LLM 往返
        assertEquals(read, newsService.stockRead("alice", "600519", 90));
        verify(newsClient, times(1)).synthesize(eq("SH600519"), any(), any(), anyList());
    }

    @Test
    void stockReadReturnsNullWhenTheModelCannotConclude() {
        NewsEvent notice = event(1L, "SH600519", "公告", LocalDate.now().atTime(9, 0));
        notice.setPlainSummary("有解读");
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(notice));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        ObjectNode failed = envelope();
        failed.withObject("/data").put("ok", false).put("read", "");
        when(newsClient.synthesize(anyString(), any(), any(), anyList())).thenReturn(failed);

        // 返回 null（而不是空串/占位文案）：前端据此**隐藏整块**，
        // 宁可不显示，也不要用一段空话占住页面最显眼的位置
        assertNull(newsService.stockRead("alice", "600519", 90));
    }

    @Test
    void stockReadSkipsTheModelWhenNothingWasAnalyzedYet() {
        NewsEvent notice = event(1L, "SH600519", "公告", LocalDate.now().atTime(9, 0));
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(notice));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
        // 只由未解读条目喂给模型，只会让它围着"信息不足"打转
        assertNull(newsService.stockRead("alice", "600519", 90));
        verify(newsClient, never()).synthesize(anyString(), any(), any(), anyList());
    }

    @Test
    void timelineDoesNotCallTheModelWhenEverythingIsAlreadyAnalyzed() {
        NewsEvent analyzed = event(1L, "SH600519", "今日公告", LocalDate.now().atTime(9, 0));
        analyzed.setPlainSummary("已经有解读了（幂等：不再花钱）");
        when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(analyzed));
        when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());

        newsService.timeline("alice", "600519", 30);

        verifyNoInteractions(newsClient);
    }

    // ============ 综合解读宁可早给，也不要一条都不给 ============
    //
    // 2026-09-20 真机联调实测出来的缺陷：冷标的（300750，库里 0 条）第一次打开时，
    // 后台要先把上游抓回来（约 6 秒）再做第一批解读（约 2 秒）。
    // 原实现"等 future 结束"= 等全部 30 条读完 → 必然超时，而超时那一刻库里
    // **一条解读都没有** → stockRead 直接返回 null：用户看到一份完整列表，
    // 却**永远**没有"当前怎么看"。
    //
    // 下面这条用例钉住修法：等待期间**一看到有解读就取用**，不再死等全部批次。

    @Test
    void stockReadUsesTheFirstAvailableAnalysisInsteadOfWaitingForEveryBatch() throws Exception {
        NewsEvent analyzed = event(1L, "SH600519", "回购公告", LocalDate.now().atTime(9, 0));
        analyzed.setPlainSummary("公司拟回购 10 亿元");
        NewsEvent pending = event(2L, "SH600519", "刚出的公告", LocalDate.now().atTime(10, 0));

        // 后台任务被闸门卡住（模拟"抓上游 + 后面还有 5 批没跑"）
        CountDownLatch gate = new CountDownLatch(1);
        ExecutorService background = Executors.newSingleThreadExecutor(r -> {
            Thread thread = new Thread(r, "test-news-enrich-slow");
            thread.setDaemon(true);
            return thread;
        });
        Executor gated = command -> background.execute(() -> {
            try {
                gate.await(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });

        NewsService gatedService = new NewsService(
                newsEventRepository, newsStockRelRepository, favoriteStockRepository,
                userRepository, newsClient, stockClient, transactionManager, gated);
        try {
            when(newsEventRepository.findTimeline(anyList(), any(), any()))
                    .thenReturn(List.of(analyzed, pending));
            when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
            ObjectNode synthesis = envelope();
            synthesis.withObject("/data").put("ok", true).put("read", "信息面偏正面，关注回购落地节奏。");
            when(newsClient.synthesize(eq("SH600519"), any(), any(), anyList())).thenReturn(synthesis);

            long startedAt = System.nanoTime();
            String read = gatedService.stockRead("alice", "600519", 90);
            long elapsedMs = (System.nanoTime() - startedAt) / 1_000_000;

            assertEquals("信息面偏正面，关注回购落地节奏。", read,
                    "库里已经有解读时就必须给出结论，不能因为'后面还有批次没跑'而返回 null");
            assertTrue(elapsedMs < 3000,
                    "一看到解读就该走，不该死等满超时（实测 " + elapsedMs + "ms）");
        } finally {
            gate.countDown();
            background.shutdownNow();
        }
    }

    // ============ 首屏不被补数据拖住（2026-09-20 改造的核心不变量）============
    //
    // 用户的原话："本来新闻获取的就慢，用户点进来过了几秒才有新闻内容"。
    // 所以这一条钉住的不是"补得好不好"，而是**接口返回得快不快** ——
    // 它是这次改造唯一不能回退的东西：一旦有人把 `analyzePending` 挪回请求线程，
    // 页面就会退回"先空等几秒"的观感，而所有其它用例都还是绿的。

    @Test
    void timelineReturnsImmediatelyWhileEnrichmentRunsInTheBackground() throws Exception {
        // 一把闸门卡住后台任务：用例只关心"接口有没有被它拖住"，
        // 所以放行之后**刻意不执行**真正的活（那部分由别的用例覆盖）。
        CountDownLatch gate = new CountDownLatch(1);
        ExecutorService background = Executors.newSingleThreadExecutor(r -> {
            Thread thread = new Thread(r, "test-news-enrich");
            thread.setDaemon(true);
            return thread;
        });
        Executor gated = command -> background.execute(() -> {
            try {
                gate.await(5, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        });

        NewsService gatedService = new NewsService(
                newsEventRepository, newsStockRelRepository, favoriteStockRepository,
                userRepository, newsClient, stockClient, transactionManager, gated);
        try {
            NewsEvent fresh = event(1L, "SH600519", "今日公告", LocalDate.now().atTime(9, 0));
            when(newsEventRepository.findTimeline(anyList(), any(), any())).thenReturn(List.of(fresh));
            when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());

            long startedAt = System.nanoTime();
            StockNewsTimelineResponse first = gatedService.timeline("alice", "600519", 30);
            long elapsedMs = (System.nanoTime() - startedAt) / 1_000_000;

            assertTrue(elapsedMs < 1000,
                    "接口必须立刻返回，不能被后台解读拖住（实测 " + elapsedMs + "ms）");
            assertEquals(1, first.total(), "库里的条目照常返回，不能因为解读没做完就空着");
            assertFalse(first.events().get(0).isAnalyzed(), "解读还没跑，如实标记未解读");
            assertTrue(first.analyzing(), "有在途任务时必须如实说，否则前端会停止轮询");
            assertEquals(1, first.pendingCount());

            // 第二次请求：后台仍在途 —— 既不能重复发起，也不能改口说"没在解读"
            assertTrue(gatedService.timeline("alice", "600519", 30).analyzing());
            verifyNoInteractions(newsClient);
        } finally {
            gate.countDown();
            background.shutdownNow();
        }
    }

    @Test
    void stockReadDoesNotCacheAConclusionBuiltFromPartialAnalysis() throws Exception {
        // 后台还在补解读时，"当前怎么看"只能基于当时那几条 —— 结论的依据随后就变了。
        // 缓存它会让用户在列表早已补全的情况下反复看到一段半成品判断
        // （实测的观感是"它明明读过这些新闻，为什么说得这么浅"）。
        CountDownLatch gate = new CountDownLatch(1);
        ExecutorService background = Executors.newSingleThreadExecutor(r -> {
            Thread thread = new Thread(r, "test-news-enrich");
            thread.setDaemon(true);
            return thread;
        });
        // 闸门在用例放行**之后**才真正执行后台的活（与上一个用例不同：那个只需要"卡住"）。
        Executor gated = command -> background.execute(() -> {
            try {
                gate.await(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                return;
            }
            command.run();
        });

        NewsService gatedService = new NewsService(
                newsEventRepository, newsStockRelRepository, favoriteStockRepository,
                userRepository, newsClient, stockClient, transactionManager, gated);
        try {
            NewsEvent analyzed = event(1L, "SH600519", "回购公告", LocalDate.now().atTime(9, 0));
            analyzed.setPlainSummary("公司拟回购 10 亿元");
            NewsEvent pending = event(2L, "SH600519", "刚出的公告", LocalDate.now().atTime(10, 0));
            when(newsEventRepository.findTimeline(anyList(), any(), any()))
                    .thenReturn(List.of(analyzed, pending));
            when(newsStockRelRepository.findRelatedEventIds(anyList(), any())).thenReturn(List.of());
            ObjectNode synthesis = envelope();
            synthesis.withObject("/data").put("ok", true).put("read", "基于部分解读的结论。");
            when(newsClient.synthesize(eq("SH600519"), any(), any(), anyList())).thenReturn(synthesis);

            // ① 后台被闸门卡住 → 这一版结论只读到了"当时已有的那一条解读"。
            //    注意它**不会**为此等满超时：库里已经有解读可用，就先把话说出来。
            assertEquals("基于部分解读的结论。", gatedService.stockRead("alice", "600519", 90));

            // ② 再问一次：**必须重新问模型**。
            //
            //    闸门**始终不放行**，也就是说"后台仍在补解读"这个前提在整条用例里稳定成立 ——
            //    这正是本条要钉的事：数据不完整时给出的结论不许进缓存。
            //    （第一版让闸门在两次调用之间放行，于是"后台跑完没有"变成了一个竞态，
            //      用例时绿时红 —— 那种用例比没有更糟。）
            //    "数据完整 → 允许缓存"由 stockReadReturnsTheSynthesizedParagraph 覆盖。
            assertEquals("基于部分解读的结论。", gatedService.stockRead("alice", "600519", 90));
            verify(newsClient, times(2)).synthesize(eq("SH600519"), any(), any(), anyList());
        } finally {
            gate.countDown();
            background.shutdownNow();
        }
    }

    // ==================== 夹具 ====================

    private NewsSearchRequest searchRequest(String symbol, String keyword, List<Integer> types, String scope) {
        return new NewsSearchRequest(symbol, keyword, types, 7, scope, 0, 20);
    }

    private static NewsEvent event(Long id, String symbol, String title, LocalDateTime publishedAt) {
        return NewsEvent.builder()
                // sourceLevel=1（公告）：公告天然是"关于这只票"的。
                // 媒体(2)现在要过**展示用相关度**（标题里得有股票名），而本助手不设 name，
                // 用它造媒体条目会被正确地过滤掉 —— 那些用例要的是"这只票自己的事件"。
                .id(id).symbol(symbol).title(title).sourceLevel(1)
                .publishedAt(publishedAt)
                .build();
    }

    /** 一条"关于这只票"的媒体条目（标题里带股票名，能过相关度）。 */
    private static NewsEvent mediaAboutStock(Long id, String symbol, String name,
                                             String title, LocalDateTime publishedAt) {
        return NewsEvent.builder()
                .id(id).symbol(symbol).name(name).title(title).sourceLevel(2)
                .publishedAt(publishedAt)
                .build();
    }

    /** 一条 N1 输出形状的原始事件（键名与 Python 侧一字不差）。 */
    private ObjectNode eventItem(String symbol, String title, String url, String publishedAt, int level) {
        ObjectNode node = mapper.createObjectNode();
        node.put("symbol", symbol);
        node.put("name", "贵州茅台");
        node.put("title", title);
        node.put("content", title);
        node.put("url", url);
        node.put("source_level", level);
        node.put("source_name", "东方财富·公告");
        node.put("event_type_raw", "股权激励进展公告");
        node.put("published_at", publishedAt);
        return node;
    }

    private ObjectNode fetchOk(ObjectNode... items) {
        return envelope(items);
    }

    private ObjectNode analyzeOk(ObjectNode... rows) {
        return envelope(rows);
    }

    private ObjectNode envelope(ObjectNode... items) {
        ObjectNode data = mapper.createObjectNode();
        ArrayNode array = data.putArray("items");
        for (ObjectNode item : items) {
            array.add(item);
        }
        ObjectNode response = mapper.createObjectNode();
        response.put("ok", true);
        response.set("data", data);
        return response;
    }

    private ObjectNode degraded(String reason) {
        ObjectNode response = mapper.createObjectNode();
        response.put("ok", false);
        response.put("error", reason);
        return response;
    }

    /** 一条 N2 输出形状的分析结果（spec §6 的字段名一字不改 + 额外布尔 analyzed）。 */
    private ObjectNode analysisRow(boolean analyzed, String eventType, String direction, double confidence,
                                   String impactLevel, String plainSummary, String[] risks,
                                   String[] opportunities, String[] relatedSymbols) {
        ObjectNode row = mapper.createObjectNode();
        row.put("event_type", eventType);
        row.put("direction", direction);
        row.put("confidence", confidence);
        row.put("impact_level", impactLevel);
        row.put("plain_summary", plainSummary);
        row.put("analyzed", analyzed);
        ArrayNode riskArray = row.putArray("risks");
        for (String risk : risks) {
            riskArray.add(risk);
        }
        ArrayNode opportunityArray = row.putArray("opportunities");
        for (String opportunity : opportunities) {
            opportunityArray.add(opportunity);
        }
        ArrayNode relatedArray = row.putArray("related_symbols");
        for (String symbol : relatedSymbols) {
            relatedArray.add(symbol);
        }
        return row;
    }
}

