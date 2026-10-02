package com.happyericsix.stocktracker.service;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.client.NewsClient;
import com.happyericsix.stocktracker.dto.NewsEventResponse;
import com.happyericsix.stocktracker.dto.NewsSearchRequest;
import com.happyericsix.stocktracker.dto.StockNewsTimelineResponse;
import com.happyericsix.stocktracker.dto.StockQuoteResponse;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsStockRel;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.NewsStockRelRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Executor;

/**
 * 资讯服务（plan N3）：落库 + 搜索 + 分析回填。
 *
 * <h3>三条不能违反的规矩</h3>
 * <ol>
 *   <li><b>调 Python 的长阻塞方法不包在事务里</b>（plan §0.4 约定 7，
 *       {@code StrategyService.runBacktest} 的先例）：{@code fetch} 是 4 个外部 HTTP 请求
 *       （全市场公告上千行），{@code analyze} 是 LLM 往返。所有写库动作都走
 *       {@link TransactionTemplate} 的**短事务**（"先查 → 调 Python → 短事务落库"三步），
 *       否则一次搜索会占住数据库连接几十秒。</li>
 *   <li><b>库优先 + 实时兜底</b>：{@code stock_news_em} 只给 10 条（实测），
 *       "某只票最近 30 天的新闻"不可能现拉 —— 历史必须靠落库累积。</li>
 *   <li><b>只增不改已分析内容</b>：重抓同一篇文章不许覆盖已经生成的解读
 *       （LLM 有成本、有随机性，覆盖等于让用户看到的内容莫名其妙地变）。</li>
 * </ol>
 *
 * <p>Service **不返回 {@code Result}、不把错误翻译成 HTTP**：参数不合法抛
 * {@link IllegalArgumentException}（由 {@code GlobalExceptionHandler} 翻成 400），
 * 上游故障则降级成"库里有什么给什么"。
 */
@Service
public class NewsService {

    private static final Logger log = LoggerFactory.getLogger(NewsService.class);

    private static final String SCOPE_MINE = "mine";

    /** 1公告 2媒体 3研报 4舆情（与 Python 的 LEVEL_* 一致）。 */
    private static final List<Integer> ALL_LEVELS = List.of(1, 2, 3, 4);

    private static final int DEFAULT_SEARCH_DAYS = 7;
    /**
     * 个股事件时间轴的默认窗口。
     *
     * <p>从 30 天放宽到 90 天是**用户反馈驱动的**：实测 600519 在 30 天窗口下只有
     * 11 条（公告 0 / 媒体 9 / 研报 2），用户看到的直接反应是"公告、研报一个都没有"。
     * 放宽到 90 天后是 24 条（公告 8 / 媒体 3 / 研报 13），而且公告全部带正文。
     * 个股页要的是"这只票的背景"，不是"最近一周的资讯流"。
     */
    private static final int DEFAULT_TIMELINE_DAYS = 90;
    private static final int DEFAULT_PAGE_SIZE = 20;
    private static final int MAX_PAGE_SIZE = 100;
    /** 与 Python 端点一致的上限：days 超过它没有意义（公告只存当日，研报窗口也远小于一年）。 */
    private static final int MAX_DAYS = 365;
    /**
     * deep 模式单次上限，**与 Python 的 {@code NEWS_ANALYZE_MAX_DEEP} 对齐**：
     * 超过它 Python 直接回 400，所以这里必须先自己决定用哪个模式。
     */
    private static final int DEEP_MODE_MAX = 20;
    /**
     * 个股时间轴一次最多给多少条。
     *
     * <p>个股页只是"背景信息"，不是信息流：30 天窗口内一只票通常十几条，
     * 但热门票遇到研报密集期会有上百条 —— 不设上限会让一次页面加载拖着几百条 JSON。
     */
    private static final int MAX_TIMELINE = 200;

    private static final DateTimeFormatter PUBLISHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");
    private static final DateTimeFormatter PUBLISHED_AT_MINUTE = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm");
    private static final DateTimeFormatter PUBLISHED_AT_DAY_ONLY = DateTimeFormatter.ofPattern("yyyy-MM-dd");
    private static final DateTimeFormatter DAY_PARAM = DateTimeFormatter.ofPattern("yyyyMMdd");

    /**
     * 同一标的的"补数据"冷却窗口（兜底抓取 + 补解读共用）。
     *
     * <h3>为什么必须有这一步（端到端冒烟实测出来的缺陷）</h3>
     * {@link #needsRefresh} 的判据是"库里该标的最新一条早于今天"。而对**今天没有新公告的
     * 股票**，这个条件**永远成立** —— 实测：连续两次 {@code POST /news/search {600519}}
     * 都打了上游（Python 访问日志里两条 {@code POST /api/v1/news/fetch}），
     * 耗时 3.7s / 3.3s，施工图 §N3 的验收"第二次直接命中库（未调 Python）"因此不成立。
     *
     * <p>换判据（比如"库里没有才兜底"）会让用户**永远看不到今天的新公告**，
     * 那正是 N3 要解决的问题。所以正确的修法是**保留判据 + 加冷却**：
     * 5 分钟内同一只票只补一次，之后直接读库。用户要更新时点一次页面刷新即可能落在新窗口内，
     * 而重复请求的成本被限死在"每票每 5 分钟一次"。
     */
    private static final Duration ENRICH_COOLDOWN = Duration.ofMinutes(5);

    /**
     * 个股页自动补解读的条数上限。
     *
     * <p>为什么要自动补：{@code GET /news/events} 只读库，而落库的条目一开始都没有解读。
     * 实测：11 条事件里只有 2 条有解读，用户看到的直接反应是
     * "这么多新闻还是不能用 AI 解读，那我设置的意义是什么"。
     *
     * <p>从 5 条提到 30 条同样是被这条反馈驱动的：只补 5 条时，排在前面的媒体条目会霸占
     * 全部额度，日期更早但**更该被解读**的公告与研报（它们排在列表后面）永远轮不到。
     * 一次 {@code batch} 调用覆盖 5 条（Python 侧 {@code BATCH_SIZE = 5}），
     * 所以 30 条 = 最多 6 次调用，且受**每标的 5 分钟冷却**约束、落库后幂等，
     * 重复打开一只票不会再花钱。
     */
    private static final int TIMELINE_ANALYZE_LIMIT = 30;

    /**
     * 一次 HTTP 调用里送几条去解读。
     *
     * <p>与 Python 的 {@code news_understanding.BATCH_SIZE = 5} 对齐：一批就是**一次**
     * LLM 往返，所以单次调用有界的时长（几秒），不会撞上 {@code NewsClient} 的 30 秒读超时。
     * 两处不一致不会报错，只会让"一批"变成"两次模型调用"或"一次调用里塞两批" —— 都是慢，不是错。
     */
    private static final int ANALYZE_BATCH_SIZE = 5;

    /**
     * 综合解读最多等后台补数据多久。
     *
     * <p>10 秒是量出来的：冷标的上"抓上游 + 第一批解读"实测约 8 秒
     * （300750 真机联调：0 条 → 6 秒后 46 条 → 再 2 秒后 5 条有解读）。
     * 但等待**不是**固定等满 —— {@link #awaitUsableAnalysis} 一看到库里有解读就走，
     * 所以这个数是"最坏情况的上限"，不是"每次都等这么久"。
     *
     * <p>超时**不是失败**：用已有解读照常生成；一条都没有时返回 null（整块不显示），
     * 由前端在解读陆续到位后再问一次。
     */
    private static final Duration ENRICH_WAIT_FOR_READ = Duration.ofSeconds(10);

    /**
     * 等综合解读时，多久看一眼"库里有没有解读了"。
     *
     * <p>400ms：后台一批解读要 2~6 秒，看太勤只是白查库；看太慢会让用户多等一整个间隔。
     * 一次查询是带索引的库读，10 秒里最多 25 次。
     */
    private static final long ANALYSIS_READY_POLL_MS = 400L;

    /**
     * 每个标的最近一次补数据的时间。用 Caffeine 而不是裸 Map：它有大小上限与过期清理，
     * 不会因为"用户搜过几千只票"而无限增长。
     */
    private final Cache<String, Instant> lastEnrichAt = Caffeine.newBuilder()
            .expireAfterWrite(ENRICH_COOLDOWN)
            .maximumSize(500)
            .build();

    private final NewsEventRepository newsEventRepository;
    private final NewsStockRelRepository newsStockRelRepository;
    private final FavoriteStockRepository favoriteStockRepository;
    private final UserRepository userRepository;
    private final NewsClient newsClient;
    /** 只为综合解读取一次最新行情（判断"信息面与股价表现是否一致"），不参与资讯落库。 */
    private final AkshareStockClient stockClient;
    private final TransactionTemplate transactionTemplate;
    /**
     * 后台补数据（兜底抓取 + 分批解读）用的执行器。
     *
     * <p>与 {@code priceRefreshExecutor} 分开：那个是"限 8 并发保护 akshare"的取数池，
     * 这里跑的是 LLM 往返（秒级、且要写库），混在一个池里会让"刷自选股价"和
     * "补资讯解读"互相排队 —— 实测的观感就是页面开了半天两个都没好。
     */
    private final Executor newsEnrichmentExecutor;

    /**
     * 自建 mapper 而不是注入 Spring 的那一个：本类只做"字符串 ↔ 字符串数组"这种
     * 与全局序列化配置无关的转换（{@code PaperTradingService} 也是这么做的），
     * 顺便让纯单测不必为了构造 service 去造一个 Spring 上下文。
     */
    private final ObjectMapper mapper = new ObjectMapper();

    public NewsService(NewsEventRepository newsEventRepository,
                       NewsStockRelRepository newsStockRelRepository,
                       FavoriteStockRepository favoriteStockRepository,
                       UserRepository userRepository,
                       NewsClient newsClient,
                       AkshareStockClient stockClient,
                       PlatformTransactionManager transactionManager,
                       @Qualifier("newsEnrichmentExecutor") Executor newsEnrichmentExecutor) {
        this.newsEventRepository = newsEventRepository;
        this.newsStockRelRepository = newsStockRelRepository;
        this.favoriteStockRepository = favoriteStockRepository;
        this.userRepository = userRepository;
        this.newsClient = newsClient;
        this.stockClient = stockClient;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.newsEnrichmentExecutor = newsEnrichmentExecutor;
    }

    // ==================== 搜索 ====================

    /**
     * 搜索：库优先 → 不足则实时兜底 → 再查一次。
     *
     * <p>兜底只在**指定了标的**时发生（这正是"用户在看某只票"的场景）：
     * 无标的的关键词/宏观搜索如果也兜底，等于每敲一次搜索就把当日 469 条全市场公告
     * 拉一次并落库，而且没有"这只票最新一条是什么时候"这种收敛判据可用。
     */
    public Page<NewsEventResponse> search(String username, NewsSearchRequest request) {
        int days = resolveDays(request.getDays(), DEFAULT_SEARCH_DAYS);
        Pageable pageable = pageable(request.getPage(), request.getSize());
        String keyword = blankToNull(request.getKeyword());
        List<Integer> types = resolveTypes(request.getTypes());
        List<Integer> levels = types == null ? ALL_LEVELS : types;
        String symbol = blankToNull(request.getSymbol());

        List<String> symbolFilter = resolveSymbolFilter(username, symbol, request.getScope());
        if (symbolFilter != null && symbolFilter.isEmpty()) {
            // 自选里一只票都没有（或与请求的标的没有交集）：答案是"没有相关事件"，
            // 而不是"全市场事件"—— 所以这里**不能**退化成不带过滤的查询。
            return Page.empty(pageable);
        }

        LocalDateTime since = windowStart(days);
        Page<NewsEvent> hit = queryEvents(since, levels, keyword, symbolFilter, pageable);

        if (symbol != null && needsRefresh(hit, symbolFilter) && tryAcquireEnrich(symbol)) {
            // 指定标的 → onlyRelevant=true：大盘综述不该进这只票的搜索结果里
            fetchInto(symbol, keyword, types, days, true);
            hit = queryEvents(since, levels, keyword, symbolFilter, pageable);
        }

        return hit.map(event -> NewsEventResponse.from(event, mapper));
    }

    // ==================== 实时兜底 ====================

    /**
     * 抓上游并落库。失败只记日志，**绝不抛**。
     *
     * <p>这正是 plan §0.4 约定 6 要的"能同时区分『库里有结果但刷新失败』与『彻底没结果』"：
     * 前者照常返回库里那几条，后者才让调用方看到空。
     *
     * <p>{@code withBody=true}：东财公告只给标题、没有正文，不补正文时 N2 对公告只能回
     * "信息不足，不判断方向"（真实链路实测：不补 0/6 判出方向，补了 6/8）。
     */
    private void fetchInto(String symbol, String keyword, List<Integer> types, int days,
                           boolean onlyRelevant) {
        JsonNode fetched = newsClient.fetch(symbol, keyword, types, days, true, onlyRelevant);
        if (NewsClient.isOk(fetched)) {
            List<JsonNode> items = itemsOf(fetched);
            log.debug("资讯兜底：symbol={} 抓取 {} 条，新增 {} 条", symbol, items.size(), upsert(items));
            return;
        }
        log.warn("资讯实时兜底失败（symbol={}）：{}，改为只返回库内已有条目",
                symbol, NewsClient.errorOf(fetched));
    }

    /**
     * 冷却窗口内同一标的只补一次。**先占位再干活** —— 并发的重复请求不该各自打一次上游。
     *
     * @return 本次获准补数据时为 true
     */
    private boolean tryAcquireEnrich(String symbol) {
        String key = canonicalSymbol(symbol);
        if (key.isEmpty()) {
            return false;
        }
        if (lastEnrichAt.getIfPresent(key) != null) {
            return false;
        }
        lastEnrichAt.put(key, Instant.now());
        return true;
    }

    // ==================== 个股时间轴 ====================

    /**
     * 个股事件时间轴（时间倒序）。
     *
     * <p>两条来源合并：① 这条资讯本身就属于这只票；② 资讯**关联**这只票
     * （宏观/行业资讯经 AI 的 {@code related_symbols} 落到 {@code news_stock_rel}）。
     * 只看 ① 的话，真正影响股价的政策与行业新闻会一条都看不到 —— 它们不写股票代码。
     *
     * <h3>2026-09-20 改造：从"同步做完再返回"改成"先给库里的，其余丢后台"</h3>
     * 用户的原话是*"本来新闻获取的就慢，用户点进来过了几秒才有新闻内容"*。
     * 原实现会在返回**之前**同步完成"兜底抓取 + 最多 6 次批量 LLM 解读"，
     * 所以那几秒里前端只能显示一行"资讯加载中…"，一个字的正文都没有。
     *
     * <p>更糟的是它**并不总是能成功**：30 条要 6 次串行 LLM 调用（Python 侧
     * {@code BATCH_SIZE = 5}）而 {@code NewsClient} 的读超时只有 30 秒 ——
     * 冷标的上整批解读会因为超时被整体丢弃（{@code analyzeAndPersist} 记一条 warning 就返回 0），
     * 用户白等一场且什么都看不到。
     *
     * <p>现在这个方法只做三件事：读库、判断"要不要补"、把补的活交给
     * {@link #scheduleEnrichment}。批大小也从"30 条一次"改成 {@link #ANALYZE_BATCH_SIZE}
     * 条一次 —— 单次调用有界，且每批落库后**立刻对下一次轮询可见**。
     */
    public StockNewsTimelineResponse timeline(String username, String symbol, int days) {
        if (symbol == null || symbol.isBlank()) {
            throw new IllegalArgumentException("symbol 不能为空");
        }
        int window = resolveDays(days, DEFAULT_TIMELINE_DAYS);
        List<NewsEvent> events = loadTimelineEvents(symbolVariants(symbol), windowStart(window));

        // 一次授权覆盖"抓 + 补解读"：同一只票每 5 分钟最多一次兜底抓取 **和** 一批补解读。
        // 不把补解读也算进去的话，每次页面加载都会再补一批（实测第二次仍花 1.9s 并再调一次
        // 模型）—— 条数有上限但调用次数没有，反复刷新就是反复花钱。
        int pending = Math.min(pendingCount(events), TIMELINE_ANALYZE_LIMIT);
        boolean scheduled = false;
        if ((needsTimelineRefresh(events) || pending > 0) && tryAcquireEnrich(symbol)) {
            scheduleEnrichment(symbol, window);
            scheduled = true;
        }

        // analyzing 取自"当前是否有在途任务"，而不是"这次有没有发起"：
        // 并发请求里只有一个能拿到冷却授权，其余几个如果按"我没发起"来回答，
        // 就会告诉前端"没有在解读"→ 前端停止轮询 → 用户看到一份永远补不全的列表。
        boolean analyzing = enrichmentsInFlight.contains(canonicalSymbol(symbol));
        if (scheduled && !analyzing) {
            // 发起了、但已经结束了（执行器是同步的，或任务恰好在上面这几毫秒里跑完）。
            // 重新读一次，免得把刚刚补好的解读挡在这一版响应之外 ——
            // 那会让用户看到"列表里明明有解读了，接口却说没有"。
            events = loadTimelineEvents(symbolVariants(symbol), windowStart(window));
        }

        List<NewsEventResponse> responses = sortedResponses(events);
        int analyzed = (int) responses.stream().filter(NewsEventResponse::isAnalyzed).count();
        log.debug("User {} 读取 {} 的资讯时间轴：{} 天窗口，{} 条（已解读 {}，在解读={}）",
                username, symbol, window, responses.size(), analyzed, analyzing);
        return new StockNewsTimelineResponse(responses, analyzing, analyzed, responses.size(), pending);
    }

    /** 还没有解读、且这一轮会去补的条数（受每轮上限约束）。 */
    private int pendingCount(List<NewsEvent> events) {
        return (int) events.stream().filter(event -> isBlank(event.getPlainSummary())).count();
    }

    // ==================== 后台补数据（抓 + 分批解读） ====================

    /**
     * 同一标的**在途**的补数据任务（只用来回答"现在还有没有在跑"）。
     *
     * <p>用 {@code add} **先占位、再提交**，而不是先提交再记录：否则在"执行器同步执行"
     * 的场合（用例里就是）会让任务先跑完、再从集合里删掉，然后才被加进去 ——
     * 留下一个永不消失的"在途"标记，于是**这只票再也不会被补数据**。
     */
    private final Set<String> enrichmentsInFlight = ConcurrentHashMap.newKeySet();

    /**
     * 把"兜底抓取 + 分批解读"丢到后台；同一标的只允许一个在途任务。
     *
     * <p>没有返回值：调用方要的不是"等它"，而是"知道它在不在跑"
     * （{@link #timeline} 用它回答 {@code analyzing}，{@link #awaitUsableAnalysis} 用它决定等不等）。
     */
    private void scheduleEnrichment(String symbol, int window) {
        String key = canonicalSymbol(symbol);
        if (!enrichmentsInFlight.add(key)) {
            return;
        }
        try {
            newsEnrichmentExecutor.execute(() -> {
                try {
                    enrich(symbol, window);
                } catch (Exception e) {
                    // 后台线程抛出去只会变成一条无人处理的堆栈；这里必须自己收干净，
                    // 并且**一定要摘掉在途标记** —— 否则这只票在本次进程里永远不再补
                    log.warn("后台资讯补数据失败（symbol={}）：{}", symbol, e.toString());
                } finally {
                    enrichmentsInFlight.remove(key);
                }
            });
        } catch (RuntimeException e) {
            // 线程池拒绝（队列满 / 已关闭）：照样要摘掉占位
            enrichmentsInFlight.remove(key);
            throw e;
        }
    }

    /**
     * 后台任务本体：先补抓（若库过期），再**分批**补解读。
     *
     * <p>每批独立落库，所以轮询方能在几秒内看到第一批的结论 —— 而不是等 30 条全部做完
     * 才有任何变化。顺序上先补最新的一批（见 {@link #newestFirst()}）：
     * "最新发生的事"才是用户真正想知道的那一条。
     */
    private void enrich(String symbol, int window) {
        long started = System.currentTimeMillis();
        List<String> variants = symbolVariants(symbol);
        LocalDateTime since = windowStart(window);

        List<NewsEvent> events = loadTimelineEvents(variants, since);
        if (needsTimelineRefresh(events)) {
            fetchInto(symbol, "", null, window, true);
            events = loadTimelineEvents(variants, since);
        }

        int changed = analyzePending(events);
        log.info("后台资讯补数据完成：symbol={} 回填 {} 条，耗时 {}ms",
                symbol, changed, System.currentTimeMillis() - started);
    }

    /**
     * 等"后台已经有**可用的解读**"，或后台任务结束，或超时。
     *
     * <h3>为什么不是简单地等 future 结束</h3>
     * 2026-09-20 真机联调实测暴露的缺陷：冷标的（300750，库里 0 条）第一次打开时，
     * 后台要先把上游抓回来（实测约 6 秒）再做第一批解读（约 2 秒）。而"等 future 结束"
     * 的语义是"等全部 30 条读完"—— 它注定超时，超时那一刻库里**一条解读都没有**，
     * 于是综合解读直接返回 {@code null}：用户看到的是一份完整列表 + 一句结论都没有。
     *
     * <p>而综合解读真正需要的只是"**读没读过**"，不是"读完了没有"：
     * 5 条读过 vs 30 条读过的差别，远小于"有结论 vs 没结论"。
     * 所以改成边等边看库里有没有解读 —— 一有就立刻拿去合成，剩下的解读留给下一次
     * （这一轮的结论因此标记为"基于部分数据"，不写缓存）。
     *
     * @return 后台**真的全部结束**了返回 true；"先凑合给结论"或超时返回 false
     *         （调用方据此不做缓存）
     */
    private boolean awaitUsableAnalysis(String key, List<String> variants, LocalDateTime since,
                                        Duration timeout) {
        long deadline = System.nanoTime() + timeout.toNanos();
        while (enrichmentsInFlight.contains(key)) {
            if (hasAnyAnalysis(loadTimelineEvents(variants, since))) {
                log.info("综合解读先取用已就绪的解读（symbol={}），不等后台跑完全部批次", key);
                return false;
            }
            long remainingMs = (deadline - System.nanoTime()) / 1_000_000L;
            if (remainingMs <= 0) {
                log.info("综合解读等待后台补数据超过 {} 秒（symbol={}），先用已有解读生成",
                        timeout.toSeconds(), key);
                return false;
            }
            try {
                Thread.sleep(Math.min(remainingMs, ANALYSIS_READY_POLL_MS));
            } catch (InterruptedException e) {
                // 中断不是"等到了"，也不是"失败了"：按"没等到完整的"处理，把中断标志还回去
                Thread.currentThread().interrupt();
                return false;
            }
        }
        return true;
    }

    /** 库里是否已经有任意一条解读（综合解读的最低可用条件）。 */
    private static boolean hasAnyAnalysis(List<NewsEvent> events) {
        return events.stream().anyMatch(event -> !isBlank(event.getPlainSummary()));
    }

    /** 时间倒序（无时间的排最后）+ 上限截断 + 转响应。 */
    private List<NewsEventResponse> sortedResponses(List<NewsEvent> events) {
        return events.stream()
                .sorted(newestFirst())
                .limit(MAX_TIMELINE)
                .map(event -> NewsEventResponse.from(event, mapper))
                .toList();
    }

    /** 最新在前。补解读的**顺序**也用它 —— 让用户先看到"最近发生的事"被解读出来。 */
    private static Comparator<NewsEvent> newestFirst() {
        return Comparator
                .comparing(NewsEvent::getPublishedAt,
                        Comparator.nullsLast(Comparator.<LocalDateTime>reverseOrder()))
                .thenComparing(NewsEvent::getId,
                        Comparator.nullsLast(Comparator.<Long>reverseOrder()));
    }

    /**
     * 给还没有解读的条目补解读（**分批、有界、幂等**），返回本次实际回填条数。
     *
     * <p>为什么用 {@code batch} 而不是 {@code deep}：这是页面渲染路径上的自动补充，
     * 5 条一批只要 1 次模型调用（Python 侧 {@code BATCH_SIZE = 5}）；而 {@code deep}
     * 是一条一次调用，同样 5 条要 5 次。用户想要 risks/opportunities 时点的是显式深读
     * （{@code POST /news/analyze}）。
     *
     * <p><b>为什么在 Java 侧再分一次批</b>（而不是把 30 条交给 Python 自己分批）：
     * 那样一次 HTTP 调用里要串行做完 6 次 LLM 往返，而 {@code NewsClient} 的读超时是
     * 30 秒 —— 实测的后果是超时后**整批一起丢**，用户等满 30 秒却一条解读都没拿到。
     * 分批之后单次调用有界，失败只影响当前这一批，且前几批的成果已经落库可见。
     */
    private int analyzePending(List<NewsEvent> events) {
        List<NewsEvent> pending = events.stream()
                .filter(event -> isBlank(event.getPlainSummary()))
                .sorted(newestFirst())
                .limit(TIMELINE_ANALYZE_LIMIT)
                .toList();
        if (pending.isEmpty()) {
            return 0;
        }
        int changed = 0;
        for (int start = 0; start < pending.size(); start += ANALYZE_BATCH_SIZE) {
            List<NewsEvent> batch = pending.subList(start, Math.min(start + ANALYZE_BATCH_SIZE, pending.size()));
            changed += analyzeAndPersist(batch, "batch");
        }
        return changed;
    }

    /** 时间轴的两条来源合并：自有 ∪ 关联（关联表让宏观/行业资讯能进个股页）。 */
    private List<NewsEvent> loadTimelineEvents(List<String> variants, LocalDateTime since) {
        List<NewsEvent> events = new ArrayList<>(
                newsEventRepository.findTimeline(variants, since, PageRequest.of(0, MAX_TIMELINE)));
        Set<Long> loaded = new LinkedHashSet<>();
        events.forEach(event -> loaded.add(event.getId()));

        List<Long> relatedIds = newsStockRelRepository.findRelatedEventIds(variants, since).stream()
                .filter(Objects::nonNull)
                .filter(id -> !loaded.contains(id))
                .distinct()
                .toList();
        if (!relatedIds.isEmpty()) {
            events.addAll(newsEventRepository.findAllById(relatedIds));
        }
        return events.stream().filter(this::isAboutTheStock).toList();
    }

    /**
     * 展示用相关度：**「提及」不等于「关于」**。
     *
     * <h3>为什么要在这里再判一次（Python 侧已有 `is_about_the_stock`）</h3>
     * Python 侧那次决定的是"**要不要为它花模型调用**"，而这一次决定的是"**要不要给用户看**"。
     * 用户的原始反馈是："这么多新闻还是不能用 AI 去解读，那我设置的意义是什么" ——
     * 把不解读的条目继续摆在页面上，等于逼用户自己去分辨哪些值得看。
     *
     * <p>实测依据：`stock_news_em` 是关键词匹配，600519 名下混着
     * 「深沪北百元股数量达217只」这类大盘综述（它的正文里就列着 600519，所以靠正文判不出来）。
     *
     * <p>规则与 Python 侧保持一致：**只看标题**里有没有股票名（名字拿不到时看代码；
     * 两者都拿不到时保留 —— 那正是"宏观/行业资讯经关联表进来"的正常情况，
     * 不能因为"我不知道这只票叫什么"就把真正影响股价的政策新闻丢掉）。
     */
    private boolean isAboutTheStock(NewsEvent event) {
        if (event.getSourceLevel() == null || event.getSourceLevel() != 2) {
            return true;      // 公告(1)/研报(3) 天然是"关于这只票"的，舆情(4)本阶段无数据源
        }
        // 已经有解读 = Python 的影响预筛已经判过它值得花一次模型调用
        // （对媒体而言，预筛的判据就是相关度）。这是最可靠的一道依据，
        // 也让它不依赖 `name` 这类可能缺失的展示字段 —— 早期入库的媒体条目就没有 name。
        if (event.getPlainSummary() != null && !event.getPlainSummary().isBlank()) {
            return true;
        }
        String title = event.getTitle() == null ? "" : event.getTitle();
        String name = event.getName() == null ? "" : event.getName().trim();
        if (!name.isBlank()) {
            return title.contains(name);
        }
        String code = bareCode(canonicalSymbol(event.getSymbol()));
        if (!code.isBlank() && !code.equalsIgnoreCase(event.getSymbol())) {
            return title.contains(code);
        }
        return true;          // 无从判断 → 保留（宁可多一条，也不要静默丢掉宏观/行业资讯）
    }

    /** 时间轴的兜底判据：空，或最新一条早于今天（与 {@link #needsRefresh} 同一套语义）。 */
    private boolean needsTimelineRefresh(List<NewsEvent> events) {
        if (events.isEmpty()) {
            return true;
        }
        LocalDateTime latest = events.stream()
                .map(NewsEvent::getPublishedAt)
                .filter(Objects::nonNull)
                .max(Comparator.naturalOrder())
                .orElse(null);
        // 全是"源站没给时间"的条目：无从判断新旧，不刷新（理由同 needsRefresh）
        return latest != null && latest.isBefore(CnTime.today().atStartOfDay());
    }

    // ==================== 分析（幂等） ====================

    /**
     * 结构化抽取并回填（spec §6 的字段）。
     *
     * <p><b>幂等</b>：已经有 {@code plainSummary} 且非 {@code force} 的条目直接跳过，
     * 一次 LLM 调用都不花。前端"深度解读"按钮点第二次不该再付一次钱。
     *
     * <p>模式选择：{@code deep} 一条一次、额外出 risks/opportunities（用户点开某条时才跑），
     * 但**单次上限 20 条**（Python 侧硬限制，超了返回 400）；超过 20 条时退回
     * {@code batch}（5 条一批），此时只出方向/强度/一句话，不出的风险机会留空。
     */
    public List<NewsEventResponse> analyze(String username, List<Long> eventIds, boolean force) {
        List<Long> ids = eventIds == null ? List.of()
                : eventIds.stream().filter(Objects::nonNull).distinct().toList();
        if (ids.isEmpty()) {
            throw new IllegalArgumentException("eventIds 不能为空");
        }

        List<NewsEvent> found = newsEventRepository.findAllById(ids);
        if (found.isEmpty()) {
            throw new IllegalArgumentException("新闻不存在");
        }
        List<NewsEvent> pending = found.stream()
                .filter(event -> force || isBlank(event.getPlainSummary()))
                .toList();

        if (!pending.isEmpty()) {
            String mode = pending.size() <= DEEP_MODE_MAX ? "deep" : "batch";
            int applied = analyzeAndPersist(pending, mode);
            log.info("User {} 请求解读 {} 条资讯（mode={}），回填 {} 条",
                    username, pending.size(), mode, applied);
        }

        return toResponses(found, ids);
    }

    /**
     * 调模型并把结果回填（短事务）。返回实际回填条数；上游失败只记日志、**绝不抛**。
     *
     * <p>降级策略：上游不可用时**一个字都不写**，调用方仍然把库里已有的原样返回 ——
     * "AI 暂时不可用"不该让整页资讯消失。
     */
    private int analyzeAndPersist(List<NewsEvent> pending, String mode) {
        JsonNode response = newsClient.analyze(
                pending.stream().map(this::toUpstreamItem).toList(), mode);
        if (!NewsClient.isOk(response)) {
            log.warn("资讯解读失败（{} 条，mode={}）：{}",
                    pending.size(), mode, NewsClient.errorOf(response));
            return 0;
        }
        return applyAnalysis(pending, response.path("data").path("items"));
    }

    /**
     * 综合解读缓存：同一只票在冷却窗口内复用同一段判断。
     *
     * <p>为什么必须缓存：个股页每次打开都合成一次，等于每次刷新都付一次 LLM 往返
     * （实测单次 3~6 秒）。而"这批新闻合起来怎么看"在**事件集合没变**时结论也不该变，
     * 所以按标的缓存 + 冷却窗口过期是语义正确的，不只是省钱。
     */
    private final Cache<String, String> stockReadCache = Caffeine.newBuilder()
            .expireAfterWrite(ENRICH_COOLDOWN)
            .maximumSize(500)
            .build();

    /**
     * 个股"当前怎么看"：读完这批事件之后的一段连贯判断。
     *
     * <p><b>为什么必须有这一层</b>：用户的原话是*"你要去阅读实时的新闻去更新你的想法，
     * 而不是一个新闻一个想法，这样是没有任何意义的"*。逐条方向标签把"综合"的责任
     * 推回给了用户 —— 那是这个功能存在的理由，不能丢。
     *
     * <p>返回 {@code null} 表示拿不到（模型不可用 / 没有事件 / 输出无法解析）。
     * **调用方据此隐藏整块**，而不是显示一段空话。
     */
    public String stockRead(String username, String symbol, int days) {
        if (symbol == null || symbol.isBlank()) {
            throw new IllegalArgumentException("symbol 不能为空");
        }
        String key = canonicalSymbol(symbol);
        String cached = stockReadCache.getIfPresent(key);
        if (cached != null) {
            return cached.isBlank() ? null : cached;
        }

        int window = resolveDays(days, DEFAULT_TIMELINE_DAYS);
        List<String> variants = symbolVariants(symbol);
        LocalDateTime since = windowStart(window);
        StockNewsTimelineResponse timeline = timeline(username, symbol, window);

        // 时间轴现在把解读交给后台了，所以这里要**等一下**：结论的质量取决于
        // "这批新闻到底读没读过"，而一条都没读过时只能返回 null（等于整块不显示）。
        // 等的策略与上限见 awaitUsableAnalysis / ENRICH_WAIT_FOR_READ。
        boolean complete = awaitUsableAnalysis(key, variants, since, ENRICH_WAIT_FOR_READ);
        // 无条件重读一次：等待期间后台可能又落了一批解读，这一读就是"用上它们"的动作。
        // （不会再触发新的补数据 —— 冷却窗口还占着，所以这只是一次带索引的库查询，
        //   相对于一次 3~6 秒的模型往返可以忽略。）
        timeline = timeline(username, symbol, window);

        // 只把**有解读**的条目喂进去：没有解读的条目对综合没有贡献，
        // 反而会让模型围着"信息不足"打转。
        List<NewsEventResponse> analyzed = timeline.events().stream()
                .filter(NewsEventResponse::isAnalyzed)
                .limit(SYNTHESIS_INPUT_LIMIT)
                .toList();
        if (analyzed.isEmpty()) {
            return null;
        }

        String name = timeline.events().stream()
                .map(NewsEventResponse::getName)
                .filter(value -> value != null && !value.isBlank())
                .findFirst()
                .orElse("");
        JsonNode response = newsClient.synthesize(key, name, quoteOf(key),
                analyzed.stream().map(this::toSynthesisItem).toList());
        if (!NewsClient.isOk(response)) {
            log.warn("综合解读失败（symbol={}）：{}", symbol, NewsClient.errorOf(response));
            return null;
        }
        JsonNode data = response.path("data");
        if (!data.path("ok").asBoolean(false)) {
            return null;
        }
        String read = data.path("read").asText("").trim();
        if (read.isBlank()) {
            return null;
        }
        // ⚠️ 数据不完整时**不写缓存**：这段结论只读到了当时那几条解读，而后面几批
        // 落库之后结论的依据已经变了。缓存 5 分钟会让用户在列表早已补全的情况下，
        // 反复看到一段基于半批数据的判断 —— 那比"再花一次模型调用"更不值得。
        if (complete) {
            // 空串也要缓存：否则"模型这次给不出结论"会让每次刷新都重试一遍
            stockReadCache.put(key, read);
        } else {
            log.info("综合解读基于部分解读生成（symbol={}），本次不缓存，下一轮会用全量重算", symbol);
        }
        return read;
    }

    /** 综合解读最多喂多少条（再多对结论没有边际贡献，只是在烧 token）。 */
    private static final int SYNTHESIS_INPUT_LIMIT = 25;

    /** 最新行情（喂给综合解读，用于判断"信息面与股价表现是否一致"）；取不到就返回空 Map。 */
    private Map<String, Object> quoteOf(String symbol) {
        try {
            StockQuoteResponse.GlobalQuote quote = stockClient.getStockQuote(symbol).globalQuote();
            if (quote == null) {
                return Map.of();
            }
            Map<String, Object> result = new HashMap<>();
            result.put("price", quote.price());
            result.put("changePercent", quote.changePercent());
            return result;
        } catch (Exception e) {
            // 行情取不到不该影响资讯结论：综合解读本来就把"涨跌未知"当作合法输入
            log.debug("综合解读取行情失败（不影响结论）：{}", e.getMessage());
            return Map.of();
        }
    }

    /** 事件 → 综合解读输入（只带结论字段，不重贴原文，避免模型退化成逐条复述）。 */
    private JsonNode toSynthesisItem(NewsEventResponse event) {
        ObjectNode node = mapper.createObjectNode();
        node.put("published_at", event.getPublishedAt() == null
                ? "" : event.getPublishedAt().format(PUBLISHED_AT));
        node.put("source_level", event.getSourceLevel() == null ? 2 : event.getSourceLevel());
        node.put("title", event.getTitle() == null ? "" : event.getTitle());
        node.put("event_type", event.getEventType() == null ? "" : event.getEventType());
        node.put("direction", event.getDirection() == null ? "" : event.getDirection());
        node.put("impact_level", event.getImpactLevel() == null ? "" : event.getImpactLevel());
        node.put("plain_summary", event.getPlainSummary() == null ? "" : event.getPlainSummary());
        return node;
    }

    // ==================== 手动增量 ====================

    /**
     * 手动触发当日增量（运维/调试用）。
     *
     * <p>⚠️ 契约限制：Python 的 {@code /api/v1/news/fetch} **没有 day 参数**，
     * 全市场公告源只支持"当日"。所以这里的 {@code day} 只能表达**回看窗口**
     * （"把从 day 到今天的都补一遍"），不能取历史某一天的快照。
     * 要真正回补历史，得先给 Python 端点加 day 参数（本次不动 Python）。
     */
    /**
     * 取资讯原文正文（懒加载：首次阅读时抓，之后一直用库里的）。
     *
     * <p>返回 {@code body} 或抛出带人话原因的异常（控制器转 4xx/5xx 由全局处理，
     * 前端拿到 message 软降级为"摘要 + 原文链接"）。
     *
     * <p>抓取失败也写 {@code bodyFetchedAt}：一条永远抓不到的链接（JS 渲染页/
     * 白名单外）不该被每个访客反复请求 —— 但已经抓到正文的条目永远直接命中库。
     */
    public String getOrFetchBody(Long eventId) {
        NewsEvent event = newsEventRepository.findById(eventId)
                .orElseThrow(() -> new IllegalArgumentException("资讯不存在：" + eventId));
        if (event.getBody() != null && !event.getBody().isBlank()) {
            return event.getBody();
        }
        if (event.getUrl() == null || event.getUrl().isBlank()) {
            throw new IllegalStateException("这条资讯没有原文链接（公告请等正文补抓）");
        }
        JsonNode response = newsClient.fetchArticleBody(event.getUrl());
        if (NewsClient.isOk(response)) {
            String body = response.path("data").path("body").asText("");
            if (!body.isBlank()) {
                event.setBody(body);
                event.setBodyFetchedAt(LocalDateTime.now());
                newsEventRepository.save(event);
                return body;
            }
        }
        event.setBodyFetchedAt(LocalDateTime.now());
        newsEventRepository.save(event);
        throw new IllegalStateException(NewsClient.errorOf(response));
    }

    public String refresh(String day) {
        int window = refreshWindowDays(day);
        // 手动增量是运维动作，不做相关度过滤（要的是"当天全市场都进来"）
        JsonNode response = newsClient.fetch("", "", null, window, false, false);
        if (!NewsClient.isOk(response)) {
            String error = NewsClient.errorOf(response);
            log.warn("资讯增量刷新失败（窗口 {} 天）：{}", window, error);
            return "增量刷新失败：" + error;
        }
        List<JsonNode> items = itemsOf(response);
        int added = upsert(items);
        log.info("资讯增量刷新：窗口 {} 天，抓取 {} 条，新入库 {} 条", window, items.size(), added);
        return "增量刷新完成：抓取 " + items.size() + " 条，新入库 " + added + " 条（窗口 " + window + " 天）";
    }

    // ==================== 落库 ====================

    /**
     * 落库并返回**新增**条数（已存在的按去重键跳过）。
     *
     * <p>去重键两级（spec §7）：{@code url} 优先；公告没有 url 时用
     * {@code (symbol, title, published_at)} 三元组。url 会先做归一化
     * （去 tracking 参数），否则"同一篇文章从 App 分享带 ?cxapp_link=true"
     * 会绕过唯一索引，把同一篇存成两条。
     *
     * <p><b>只增不改</b>：命中去重键时一个字都不写回事件本身 ——
     * 已生成的 {@code plainSummary}/{@code risks} 不能被一次重抓抹掉。
     */
    public int upsert(List<JsonNode> items) {
        if (items == null || items.isEmpty()) {
            return 0;
        }
        Integer added = transactionTemplate.execute(status -> saveNewEvents(items));
        return added == null ? 0 : added;
    }

    /** 短事务：只有写库，没有 HTTP。 */
    private int saveNewEvents(List<JsonNode> items) {
        int added = 0;
        for (JsonNode item : items) {
            if (item == null || !item.isObject()) {
                continue;
            }
            String title = clip(text(item, "title"), 255);
            if (title.isBlank()) {
                // title 是 NOT NULL：没有标题的条目既搜不到也展示不了，直接丢弃并留痕
                log.warn("丢弃一条没有标题的资讯（url={}）", text(item, "url"));
                continue;
            }
            String symbol = canonicalSymbol(text(item, "symbol"));
            String url = clip(normalizeUrl(text(item, "url")), 512);
            LocalDateTime publishedAt = parsePublishedAt(text(item, "published_at"));

            NewsEvent existing = findExisting(url, symbol, title, publishedAt);
            if (existing != null) {
                // 「只增不改」管的是**分析结论**（plainSummary/risks/opportunities），
                // 不是缺失的展示字段。这里补一次 `name`：早期入库的媒体条目没有股票名，
                // 而**展示层要靠它判断"这条是不是关于这只票"** —— 少了它，
                // 一条真正关于该股的新闻会被当成大盘综述挡掉（实测发生过）。
                String incomingName = clip(text(item, "name"), 64);
                if (isBlank(existing.getName()) && !incomingName.isBlank()) {
                    existing.setName(incomingName);
                    newsEventRepository.save(existing);
                }
                // 关联行补一次（幂等）：同一条资讯被媒体转述后再来，也要能在这只票的时间轴里被找到
                ensureRel(existing);
                continue;
            }

            NewsEvent event = NewsEvent.builder()
                    .symbol(symbol)
                    .name(clip(text(item, "name"), 64))
                    .title(title)
                    .content(text(item, "content"))
                    .url(url.isBlank() ? null : url)
                    .sourceLevel(sourceLevelOf(item))
                    .sourceName(clip(text(item, "source_name"), 64))
                    .eventTypeRaw(clip(text(item, "event_type_raw"), 64))
                    .publishedAt(publishedAt)
                    // 文件夹初值：关键词规则打标（解读管线之后可用模型的 category 覆写）
                    .category(NewsCategory.classify(symbol, title, text(item, "content")))
                    .build();
            NewsEvent saved = newsEventRepository.save(event);
            ensureRel(saved);
            added++;
        }
        return added;
    }

    /** 去重命中：url 优先，url 缺失时用 (symbol, title, published_at)。 */
    private NewsEvent findExisting(String url, String symbol, String title, LocalDateTime publishedAt) {
        if (!url.isBlank()) {
            return newsEventRepository.findByUrl(url).orElse(null);
        }
        // 三元组的比较放在 Java 里做：JPQL 的 `published_at = NULL` 永远不成立，
        // 而"源站没给时间"恰恰是最需要兜底键的情况（详见 repository 注释）。
        return newsEventRepository.findByTitle(title).stream()
                .filter(event -> Objects.equals(event.getSymbol(), symbol))
                .filter(event -> Objects.equals(event.getPublishedAt(), publishedAt))
                .findFirst()
                .orElse(null);
    }

    /** 分析结果回填（短事务：只有写库，没有 LLM）。 */
    private int applyAnalysis(List<NewsEvent> pending, JsonNode analyzedItems) {
        if (pending.isEmpty() || analyzedItems == null || !analyzedItems.isArray()) {
            return 0;
        }
        Integer applied = transactionTemplate.execute(status -> {
            int count = 0;
            // 顺序契约：Python 保证"输出与输入同序同长"（analyze_events 的 docstring），
            // 所以第 i 条结果对应的就是第 i 个待分析事件。
            int size = Math.min(pending.size(), analyzedItems.size());
            for (int i = 0; i < size; i++) {
                JsonNode row = analyzedItems.get(i);
                if (row == null || !row.isObject()) {
                    continue;
                }
                NewsEvent event = pending.get(i);
                // 可信度（信源/措辞/交叉印证）对**所有**条目生效，包括未解读的：
                // "模型没配上"不该连带把"这条是传闻"的警示也藏起来。
                // 只写可信度三列，不碰 plainSummary —— 补解读的幂等键判的是
                // plainSummary 是否有值，所以这些行以后仍会被正常补解读。
                if (row.path("credibility").isNumber()) {
                    event.setCredibility(row.path("credibility").asInt());
                    event.setCredibilityGrade(blankToNull(text(row, "credibility_grade")));
                    event.setCredibilityDetail(credibilityDetail(row));
                    newsEventRepository.save(event);
                }
                if (!row.path("analyzed").asBoolean(false)) {
                    // 降级的条目（模型没配 / 该条没轮到 / 解析失败）**不落分析字段**：
                    // 落库就等于把它标成"已解读"，幂等键会让它以后再也不重试；
                    // 而"信息不足，不判断方向"这句话前端在 direction 为空时本来就会显示，
                    // 所以不落库并不丢信息。已解读的内容也不会被降级结果覆盖。
                    continue;
                }
                event.setEventType(blankToNull(text(row, "event_type")));
                // 模型分类优先覆写规则初值：错分成本只是一条进错文件夹，
                // 但必须过封闭枚举的门 —— 模型自由发挥的类别会造出空文件夹
                String modelCategory = blankToNull(text(row, "category"));
                if (NewsCategory.isValid(modelCategory)) {
                    event.setCategory(modelCategory);
                }
                event.setDirection(blankToNull(text(row, "direction")));
                event.setConfidence(row.path("confidence").isNumber() ? row.path("confidence").asDouble() : null);
                event.setImpactLevel(blankToNull(text(row, "impact_level")));
                event.setPlainSummary(text(row, "plain_summary"));
                List<String> related = stringList(row.get("related_symbols"));
                event.setRelatedSymbols(writeJson(related));
                event.setRisks(writeJson(stringList(row.get("risks"))));
                event.setOpportunities(writeJson(stringList(row.get("opportunities"))));
                newsEventRepository.save(event);
                saveRelatedRels(event, related);
                count++;
            }
            return count;
        });
        return applied == null ? 0 : applied;
    }

    // ==================== 内部工具 ====================

    private Page<NewsEvent> queryEvents(LocalDateTime since, List<Integer> levels, String keyword,
                                        List<String> symbols, Pageable pageable) {
        String pattern = keywordPattern(keyword);
        return symbols == null
                ? newsEventRepository.searchAll(since, levels, pattern, pageable)
                : newsEventRepository.searchBySymbols(since, levels, pattern, symbols, pageable);
    }

    /**
     * 是否需要实时兜底。
     *
     * <p>判据（plan §N3）：结果为空，或该标的在库里的最新一条早于今天。
     * 只判"结果为空"是不够的 —— 库里有上周的 10 条时，用户最想看的今天这条新公告
     * 会永远不出现（这正是 {@code stock_news_em} 只给 10 条带来的真实困境）。
     */
    private boolean needsRefresh(Page<NewsEvent> hit, List<String> symbols) {
        if (hit.isEmpty()) {
            return true;
        }
        LocalDateTime latest = newsEventRepository.latestPublishedAt(symbols);
        if (latest == null) {
            // 库里全是"源站没给时间"的条目：无从判断新旧。这时**不刷新**，
            // 否则每一次搜索都要打一次上游，而这几条罕见条目不该决定刷新节奏。
            return false;
        }
        return latest.isBefore(CnTime.today().atStartOfDay());
    }

    /**
     * 搜索的标的过滤集合。
     *
     * <p>{@code scope=mine} 时用 {@code favorite_stocks}（既有方法
     * {@code findByUserId}，不另写 SQL）取用户自选，再折算成 news_events 里可能出现的
     * 代码写法。**新闻仍是全局事实表**：这里只是在"看哪些"上加过滤，
     * 不会给事件加 user_id（那样同一条公告会被 N 个用户存 N 份、去重键失效）。
     *
     * <p>两者同时给出时取**交集**（"我的自选里的这只票"）：语义最直白，
     * 不会出现"scope=mine 却返回了别人的票"。
     */
    private List<String> resolveSymbolFilter(String username, String symbol, String scope) {
        String normalizedScope = blankToNull(scope);
        if (normalizedScope != null && !SCOPE_MINE.equalsIgnoreCase(normalizedScope)) {
            throw new IllegalArgumentException("scope 只能是 mine");
        }
        boolean mine = normalizedScope != null;

        Set<String> filter = null;
        if (mine) {
            filter = new LinkedHashSet<>(favoriteSymbolVariants(username));
        }
        if (symbol != null) {
            List<String> variants = symbolVariants(symbol);
            if (filter == null) {
                return variants;
            }
            filter.retainAll(variants);
        }
        return filter == null ? null : List.copyOf(filter);
    }

    /** 用户自选股在 news_events 里可能出现的代码写法。 */
    private List<String> favoriteSymbolVariants(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        Set<String> variants = new LinkedHashSet<>();
        for (FavoriteStock favorite : favoriteStockRepository.findByUserId(user.getId())) {
            variants.addAll(symbolVariants(favorite.getStockSymbol()));
        }
        return List.copyOf(variants);
    }

    /**
     * 一个代码在库里的**所有**可能写法。
     *
     * <p>库里存的是 Python 归一化后的 {@code SH600519}，而自选里可能是用户手录的
     * {@code 600519} 或 {@code sh600519}（{@code ThsSyncService.bareCode} 就是为这种
     * 双写法存在的）。只匹配一种写法会静默漏掉一半数据，所以两种都带上。
     */
    private static List<String> symbolVariants(String symbol) {
        String canonical = canonicalSymbol(symbol);
        if (canonical.isEmpty()) {
            return List.of();
        }
        String bare = bareCode(canonical);
        return bare.isEmpty() || bare.equals(canonical) ? List.of(canonical) : List.of(canonical, bare);
    }

    /**
     * 归一化成库里存的那种形态：大写 + 市场前缀（{@code SH600519}）。
     *
     * <p>规则与 Python 的 {@code akshare_client.normalize_symbol} + {@code _out_symbol}
     * 完全一致（两边各自实现一套"代码长什么样"就是历史包袱的开始）。
     * 无法识别的输入原样大写返回，交给查询去匹配（宁可查不到，也不要猜错市场）。
     */
    private static String canonicalSymbol(String symbol) {
        String text = symbol == null ? "" : symbol.trim().toUpperCase(Locale.ROOT);
        if (text.isEmpty()) {
            return "";
        }
        for (String prefix : new String[]{"SH", "SZ", "BJ", "HK", "US"}) {
            if (text.startsWith(prefix) && text.length() > prefix.length()) {
                return text;
            }
        }
        if (text.length() == 6 && text.chars().allMatch(Character::isDigit)) {
            if (text.startsWith("6")) {
                return "SH" + text;
            }
            char head = text.charAt(0);
            if (head == '0' || head == '2' || head == '3') {
                return "SZ" + text;
            }
            return "BJ" + text;
        }
        return text;
    }

    /** 去掉市场前缀（与 {@code ThsSyncService.bareCode} 同一套语义）。 */
    private static String bareCode(String symbol) {
        String text = symbol == null ? "" : symbol.trim().toUpperCase(Locale.ROOT);
        for (String prefix : new String[]{"SH", "SZ", "BJ", "HK", "US"}) {
            if (text.startsWith(prefix) && text.length() > prefix.length()) {
                return text.substring(prefix.length());
            }
        }
        return text;
    }

    /**
     * URL 归一化（去重主键的一部分，与 Python 的 {@code normalize_url} 同一套规则）。
     *
     * <p>实测：财新链接从 App 分享带 {@code ?cxapp_link=true}、从网页点开不带。
     * 不归一化的话，唯一索引挡不住同一篇文章 —— "越用库越全"会变成"越用库越脏"。
     */
    private static String normalizeUrl(String url) {
        String raw = url == null ? "" : url.trim();
        if (raw.isEmpty()) {
            return "";
        }
        try {
            URI uri = URI.create(raw);
            String scheme = uri.getScheme();
            String host = uri.getHost();
            if (scheme == null || host == null) {
                return stripTrailingSlash(raw).toLowerCase(Locale.ROOT);
            }
            String path = stripTrailingSlash(uri.getPath() == null ? "" : uri.getPath());
            String authority = host.toLowerCase(Locale.ROOT) + (uri.getPort() > 0 ? ":" + uri.getPort() : "");
            return scheme.toLowerCase(Locale.ROOT) + "://" + authority + path;
        } catch (Exception e) {
            // 脏数据（不是合法 URL）只做去空白 + 去尾斜杠，绝不让它把整次落库带崩
            return stripTrailingSlash(raw).toLowerCase(Locale.ROOT);
        }
    }

    private static String stripTrailingSlash(String value) {
        String text = value == null ? "" : value;
        while (text.endsWith("/")) {
            text = text.substring(0, text.length() - 1);
        }
        return text;
    }

    /**
     * 发布时间的解析。
     *
     * <p>Python 侧已统一成 {@code "YYYY-MM-DD HH:MM:SS"（纯日期补 00:00:00）}，
     * 这里仍兼容纯日期与分钟精度，因为"源站改一次格式就把整批资讯丢掉"是不划算的。
     * 解析不出来（空串 / {@code "nan"} / 脏值）返回 null —— 条目照常入库，
     * 只是在时间轴上排最后（与 Python 的 {@code _in_window} / {@code _sort_key} 一致）。
     */
    private static LocalDateTime parsePublishedAt(String value) {
        String text = value == null ? "" : value.trim();
        if (text.isEmpty() || "nan".equalsIgnoreCase(text)) {
            return null;
        }
        for (DateTimeFormatter formatter : new DateTimeFormatter[]{PUBLISHED_AT, PUBLISHED_AT_MINUTE, PUBLISHED_AT_DAY_ONLY}) {
            try {
                return LocalDateTime.parse(text, formatter);
            } catch (DateTimeParseException ignored) {
                // 试下一个形态
            }
        }
        return null;
    }

    private static int sourceLevelOf(JsonNode item) {
        int level = item.path("source_level").asInt(0);
        return ALL_LEVELS.contains(level) ? level : 2;
    }

    /** 落库时补一条"事件 ↔ 代码"关联（幂等：已有则该票不再重复插）。 */
    private void ensureRel(NewsEvent event) {
        if (event == null || event.getId() == null || isBlank(event.getSymbol())) {
            return;
        }
        saveRel(event.getId(), event.getSymbol(), event.getPublishedAt());
    }

    /** 把 AI 分析出的 {@code related_symbols} 落到关联表（一条宏观资讯关联多只票）。 */
    private void saveRelatedRels(NewsEvent event, List<String> relatedSymbols) {
        if (event == null || event.getId() == null || relatedSymbols == null) {
            return;
        }
        for (String raw : relatedSymbols) {
            String symbol = canonicalSymbol(raw);
            if (!symbol.isEmpty()) {
                saveRel(event.getId(), symbol, event.getPublishedAt());
            }
        }
    }

    private void saveRel(Long eventId, String symbol, LocalDateTime publishedAt) {
        if (newsStockRelRepository.existsByEventIdAndSymbol(eventId, symbol)) {
            return;
        }
        newsStockRelRepository.save(NewsStockRel.builder()
                .eventId(eventId)
                .symbol(symbol)
                .publishedAt(publishedAt)
                .build());
    }

    /**
     * 实体 → Python 端点的输入结构（键名与 N1 的输出一致，spec §6 的输入侧）。
     *
     * <p>必须原样回传：{@code news_understanding} 用 {@code item["symbol"]} 兜底
     * {@code related_symbols}，也依赖标题/正文来判断信息是否充足。
     */
    private JsonNode toUpstreamItem(NewsEvent event) {
        ObjectNode node = mapper.createObjectNode();
        node.put("symbol", event.getSymbol() == null ? "" : event.getSymbol());
        node.put("name", event.getName() == null ? "" : event.getName());
        node.put("title", event.getTitle() == null ? "" : event.getTitle());
        node.put("content", event.getContent() == null ? "" : event.getContent());
        node.put("url", event.getUrl() == null ? "" : event.getUrl());
        node.put("source_level", event.getSourceLevel() == null ? 2 : event.getSourceLevel());
        node.put("source_name", event.getSourceName() == null ? "" : event.getSourceName());
        node.put("event_type_raw", event.getEventTypeRaw() == null ? "" : event.getEventTypeRaw());
        node.put("published_at", event.getPublishedAt() == null ? "" : event.getPublishedAt().format(PUBLISHED_AT));
        return node;
    }

    private List<NewsEventResponse> toResponses(List<NewsEvent> events, List<Long> requestedOrder) {
        List<NewsEventResponse> responses = new ArrayList<>();
        for (Long id : requestedOrder) {
            events.stream()
                    .filter(event -> Objects.equals(event.getId(), id))
                    .findFirst()
                    .ifPresent(event -> responses.add(NewsEventResponse.from(event, mapper)));
        }
        return responses;
    }

    private Pageable pageable(Integer page, Integer size) {
        int pageNumber = page == null ? 0 : page;
        if (pageNumber < 0) {
            throw new IllegalArgumentException("page 不能为负");
        }
        int pageSize = size == null ? DEFAULT_PAGE_SIZE : size;
        if (pageSize < 1 || pageSize > MAX_PAGE_SIZE) {
            throw new IllegalArgumentException("size 必须在 1~" + MAX_PAGE_SIZE + " 之间");
        }
        return PageRequest.of(pageNumber, pageSize,
                Sort.by(Sort.Direction.DESC, "publishedAt").and(Sort.by(Sort.Direction.DESC, "id")));
    }

    /** days 缺省取默认值；非正数直接 400（静默改成 1 会让调用方以为查了 30 天）。 */
    private static int resolveDays(Integer days, int fallback) {
        int value = days == null ? fallback : days;
        if (value < 1) {
            throw new IllegalArgumentException("days 必须大于 0");
        }
        // 上限按 Python 端点的口径截断（那边是 max(1, min(days, 365))），
        // 一年以外的资讯窗口对用户没有意义
        return Math.min(value, MAX_DAYS);
    }

    private static List<Integer> resolveTypes(List<Integer> types) {
        if (types == null || types.isEmpty()) {
            return null;
        }
        List<Integer> distinct = types.stream().filter(Objects::nonNull).distinct().sorted().toList();
        if (distinct.isEmpty()) {
            return null;
        }
        if (!ALL_LEVELS.containsAll(distinct)) {
            // 与 Python 端点一致：未知类型必须报错，静默忽略会返回"空结果"，
            // 被误解成"这只票没有资讯"
            throw new IllegalArgumentException("未知的资讯类型 " + distinct + "（1公告 2媒体 3研报 4舆情）");
        }
        return distinct;
    }

    /** {@code day}（YYYYMMDD）→ 回看窗口天数；空 = 只补今天。 */
    private static int refreshWindowDays(String day) {
        String text = day == null ? "" : day.trim();
        if (text.isEmpty()) {
            return 1;
        }
        LocalDate target;
        try {
            target = LocalDate.parse(text, DAY_PARAM);
        } catch (DateTimeParseException e) {
            throw new IllegalArgumentException("day 必须是 YYYYMMDD（如 20260914）");
        }
        LocalDate today = CnTime.today();
        if (target.isAfter(today)) {
            throw new IllegalArgumentException("day 不能是未来的日期");
        }
        return (int) Math.min(ChronoUnit.DAYS.between(target, today) + 1, MAX_DAYS);
    }

    private static LocalDateTime windowStart(int days) {
        return CnTime.now().minusDays(days);
    }

    /**
     * 关键词 → LIKE 模式。空关键词退化成 {@code "%"}，避免在 JPQL 里写
     * {@code :keyword IS NULL} 这种对参数类型敏感的写法。
     *
     * <p>刻意不转义 {@code %} / {@code _}：这是一个"搜关键词"的入口，不是精确匹配 DSL,
     * 而转义需要 JPQL 的 {@code ESCAPE} 子句（各数据库行为不一致）。
     */
    private static String keywordPattern(String keyword) {
        return keyword == null || keyword.isBlank()
                ? "%"
                : "%" + keyword.trim().toLowerCase(Locale.ROOT) + "%";
    }

    private static List<JsonNode> itemsOf(JsonNode response) {
        JsonNode items = response.path("data").path("items");
        if (!items.isArray()) {
            return List.of();
        }
        List<JsonNode> list = new ArrayList<>();
        items.forEach(list::add);
        return list;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        if (value == null || value.isNull()) {
            return "";
        }
        return value.asText("").trim();
    }

    private static List<String> stringList(JsonNode node) {
        if (node == null || !node.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        for (JsonNode element : node) {
            if (element != null && !element.isNull()) {
                String text = element.asText("").trim();
                if (!text.isEmpty()) {
                    values.add(text);
                }
            }
        }
        return values;
    }

    private String writeJson(List<String> values) {
        if (values == null || values.isEmpty()) {
            return null;
        }
        try {
            return mapper.writeValueAsString(values);
        } catch (Exception e) {
            log.warn("资讯字段序列化失败，按空处理：{}", e.getMessage());
            return null;
        }
    }

    /**
     * 从 Python 分析行里摘出可信度明细（分数之外的"为什么"），
     * 序列化成 JSON 落库。只挑前端真正要展示的字段——
     * components 给 hover 明细，reasons 给人话解释，三个 flag 给徽章。
     */
    private String credibilityDetail(JsonNode row) {
        try {
            java.util.Map<String, Object> detail = new java.util.LinkedHashMap<>();
            JsonNode components = row.get("credibility_components");
            if (components != null && components.isObject()) {
                detail.put("components", mapper.readValue(
                        components.toString(),
                        new tools.jackson.core.type.TypeReference<java.util.Map<String, Object>>() {}));
            }
            detail.put("reasons", stringList(row.get("credibility_reasons")));
            detail.put("rumor_flag", row.path("rumor_flag").asBoolean(false));
            detail.put("sensational_flag", row.path("sensational_flag").asBoolean(false));
            detail.put("corroborated", row.path("corroborated").asBoolean(false));
            return mapper.writeValueAsString(detail);
        } catch (Exception e) {
            log.warn("可信度明细序列化失败，按空处理：{}", e.getMessage());
            return null;
        }
    }

    private static String clip(String value, int max) {
        if (value == null) {
            return "";
        }
        return value.length() <= max ? value : value.substring(0, max);
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }

    private static String blankToNull(String value) {
        return isBlank(value) ? null : value.trim();
    }
}
