package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.NewsClient;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.NewsTopic;
import com.happyericsix.stocktracker.entity.NewsTopicRel;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.NewsTopicRelRepository;
import com.happyericsix.stocktracker.repository.NewsTopicRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * 事件线编排（2026-10-09 新闻雷达升级）：定时把库内资讯聚成专题，
 * 再由总编/撰稿两层 LLM 产出"来龙去脉"。
 *
 * <h3>三层各自的可失败性（fail-open 的边界逐层放大）</h3>
 * <ol>
 *   <li><b>聚类</b>（Python N4，纯代码）：挂了本轮不更新专题，已有专题照常展示；</li>
 *   <li><b>总编</b>（1 次 LLM）：挂了专题保留代码标题，候选全部记
 *       {@code editor_llm_unavailable}（下次运行重试）；</li>
 *   <li><b>撰稿</b>（每专题 1 次 LLM）：挂了/预算尽/冷却中按专属原因留痕，
 *       成员列表照常展示 —— "该写而没写"必须能回答"为什么"。</li>
 * </ol>
 *
 * <h3>纪律</h3>
 * 调 Python（聚类/LLM）一律**不在事务里**；事务只包写库（与 NewsService 同约）。
 * 预算按 synthesized_at 当日落库数统计 —— 跨轮次/重启不丢账（与模拟盘
 * metering 同一哲学）。金额不涉及（无 BigDecimal 问题）。
 */
@Service
public class NewsTopicService {

    private static final Logger log = LoggerFactory.getLogger(NewsTopicService.class);
    private static final DateTimeFormatter PUBLISHED_AT = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss");

    /** 超过这个小时数没有新报道的事件线置为 closed（列表默认只看 open + 近窗口） */
    static final int TOPIC_IDLE_HOURS = 72;
    /** 单轮总览发给总编的事件线上限（Python 端点同值硬限，这里先截断省流量） */
    private static final int EDITOR_MAX_CLUSTERS = 30;

    private final NewsEventRepository newsEventRepository;
    private final NewsTopicRepository newsTopicRepository;
    private final NewsTopicRelRepository newsTopicRelRepository;
    private final NewsClient newsClient;
    private final ObjectMapper mapper;
    private final TransactionTemplate transactionTemplate;

    @Value("${news.topics.enabled:true}")
    private boolean topicsEnabled;
    @Value("${news.topics.window-days:7}")
    private int windowDays;
    @Value("${news.topics.max-events:500}")
    private int maxEvents;
    @Value("${news.editor.enabled:true}")
    private boolean editorEnabled;
    @Value("${news.editor.daily-budget:30}")
    private int dailyBudget;
    @Value("${news.editor.cooldown-minutes:90}")
    private long cooldownMinutes;

    private final AtomicBoolean running = new AtomicBoolean(false);

    public NewsTopicService(NewsEventRepository newsEventRepository,
                            NewsTopicRepository newsTopicRepository,
                            NewsTopicRelRepository newsTopicRelRepository,
                            NewsClient newsClient,
                            ObjectMapper mapper,
                            PlatformTransactionManager transactionManager) {
        this.newsEventRepository = newsEventRepository;
        this.newsTopicRepository = newsTopicRepository;
        this.newsTopicRelRepository = newsTopicRelRepository;
        this.newsClient = newsClient;
        this.mapper = mapper;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    // ==================== 定时入口 ====================

    /** 一轮完整的"聚类 → 落库 → 总编 → 撰稿"。并发重入直接跳过。返回一句话摘要（给心跳）。 */
    public String runOnce() {
        if (!topicsEnabled) {
            return "功能未启用，跳过";
        }
        if (!running.compareAndSet(false, true)) {
            log.info("事件线任务上一轮还没跑完，本轮跳过");
            return "上一轮还没跑完，本轮跳过";
        }
        try {
            return runInternal();
        } catch (Exception e) {
            // 定时任务绝不让异常冒出去（冒出去也只是被调度器吞掉+一条无堆栈日志）
            log.warn("事件线任务失败（已有专题不受影响）：{}", e.toString());
            return "失败：" + e;
        } finally {
            running.set(false);
        }
    }

    private String runInternal() {
        // 1. 读库（无事务）：近窗口内有发布时间的条目。无时间的条目聚类也用不上
        //    （Python 侧同样的口径），不搬进来白占 500 的名额。
        LocalDateTime since = LocalDateTime.now().minusDays(Math.max(1, windowDays));
        List<NewsEvent> events = newsEventRepository
                .findByPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                        since, PageRequest.of(0, Math.max(2, maxEvents)));
        if (events.size() < 2) {
            log.debug("事件线任务：近 {} 天只有 {} 条可聚资讯，跳过", windowDays, events.size());
            closeIdleTopics();
            return "近 " + windowDays + " 天只有 " + events.size() + " 条资讯，跳过";
        }

        // 2. 聚类（Python 纯代码，无事务）
        JsonNode response = newsClient.cluster(events.stream().map(this::toClusterItem).toList());
        if (!NewsClient.isOk(response)) {
            log.warn("事件线聚类失败：{}（已有专题不受影响）", NewsClient.errorOf(response));
            return "聚类失败：" + NewsClient.errorOf(response);
        }
        JsonNode clustersNode = response.path("data").path("clusters");
        if (!clustersNode.isArray() || clustersNode.isEmpty()) {
            log.info("事件线聚类：{} 条资讯没有形成事件线", events.size());
            closeIdleTopics();
            return events.size() + " 条资讯没有形成事件线";
        }

        // 3. 匹配 + upsert（事务：只有写库）
        List<Candidate> candidates = matchAndUpsert(clustersNode);
        closeIdleTopics();

        // 4. 总编 + 撰稿（LLM，无事务；写结果各自短事务）
        if (!candidates.isEmpty()) {
            runEditorAndWriters(clustersNode, candidates);
        }
        String summary = String.format("%d 条资讯 → %d 条事件线（本轮撰稿候选 %d）",
                events.size(), clustersNode.size(), candidates.size());
        log.info("事件线任务完成：{}", summary);
        return summary;
    }

    // ==================== 聚类落库（事务） ====================

    /** 一条事件线 + 它对应的库内专题。clusterIndex 是它在总编输入里的 1-based 下标。 */
    record Candidate(NewsTopic topic, JsonNode cluster, boolean changed, int addedCount,
                      int clusterIndex) {
    }

    /**
     * 把 Python 的聚类映射回库内专题：
     * 按成员事件 id 反查（{@code news_topic_rel.event_id} 有索引），共享成员最多的
     * 已有专题吸纳本簇；没有共享的建新专题。同一事件后续报道因此会落进同一条线。
     */
    private List<Candidate> matchAndUpsert(JsonNode clustersNode) {
        List<JsonNode> clusters = new ArrayList<>();
        clustersNode.forEach(clusters::add);
        List<Candidate> applied = transactionTemplate.execute(status -> {
            List<Candidate> result = new ArrayList<>();
            int clusterIndex = 0;
            for (JsonNode cluster : clusters) {
                clusterIndex++;
                List<Long> memberIds = memberIdsOf(cluster);
                if (memberIds.isEmpty()) {
                    continue;
                }
                NewsTopic target = findTopicBySharedMembers(memberIds);
                boolean changed;
                int added;
                if (target == null) {
                    target = NewsTopic.builder()
                            .title(text(cluster, "title"))
                            .symbols(writeJson(stringList(cluster.get("symbols"))))
                            .sources(writeJson(stringList(cluster.get("sources"))))
                            .firstEventAt(parseTime(cluster.path("first_at").asText("")))
                            .lastEventAt(parseTime(cluster.path("last_at").asText("")))
                            .eventCount(memberIds.size())
                            .sourceCount(cluster.path("source_count").asInt(1))
                            .status(NewsTopicContract.STATUS_OPEN)
                            .titleOrigin("code")
                            .build();
                    target = newsTopicRepository.save(target);
                    linkMembers(target.getId(), memberIds);
                    changed = true;
                    added = memberIds.size();
                } else {
                    int before = countLinked(target.getId());
                    linkMembers(target.getId(), memberIds);
                    int linkedNow = countLinked(target.getId());
                    added = linkedNow - before;
                    changed = added > 0;
                    // code 标题跟随最新代表条目；editor 标题只能被总编改（见 NewsTopic 类注释）
                    if ("code".equals(target.getTitleOrigin())) {
                        target.setTitle(text(cluster, "title"));
                    }
                    target.setSymbols(mergeJsonArrays(target.getSymbols(), stringList(cluster.get("symbols"))));
                    target.setSources(mergeJsonArrays(target.getSources(), stringList(cluster.get("sources"))));
                    LocalDateTime last = parseTime(cluster.path("last_at").asText(""));
                    if (last != null && (target.getLastEventAt() == null || last.isAfter(target.getLastEventAt()))) {
                        target.setLastEventAt(last);
                    }
                    LocalDateTime first = parseTime(cluster.path("first_at").asText(""));
                    if (first != null && (target.getFirstEventAt() == null || first.isBefore(target.getFirstEventAt()))) {
                        target.setFirstEventAt(first);
                    }
                    target.setEventCount(linkedNow);
                    target.setSourceCount(Math.max(target.getSourceCount() == null ? 0 : target.getSourceCount(),
                            cluster.path("source_count").asInt(1)));
                    if (changed) {
                        // 有新报道进来 = 这条线又活了
                        target.setStatus(NewsTopicContract.STATUS_OPEN);
                    }
                    target = newsTopicRepository.save(target);
                }
                result.add(new Candidate(target, cluster, changed || isBlank(target.getSummary()),
                        added, clusterIndex));
            }
            return result;
        });
        return applied == null ? List.of() : applied;
    }

    /** 共享成员最多的已有专题；一个都不共享返回 null（建新线）。 */
    private NewsTopic findTopicBySharedMembers(List<Long> memberIds) {
        Map<Long, Integer> shared = new HashMap<>();
        for (NewsTopicRel rel : newsTopicRelRepository.findByEventIdIn(memberIds)) {
            shared.merge(rel.getTopicId(), 1, Integer::sum);
        }
        if (shared.isEmpty()) {
            return null;
        }
        Long bestId = shared.entrySet().stream()
                .max(Map.Entry.<Long, Integer>comparingByValue().thenComparing(Map.Entry::getKey))
                .map(Map.Entry::getKey).orElse(null);
        return bestId == null ? null : newsTopicRepository.findById(bestId).orElse(null);
    }

    private void linkMembers(Long topicId, List<Long> eventIds) {
        // 幂等：已有关系不重复插（唯一索引兜底，这里先查免得靠异常流控）
        Set<Long> existing = new HashSet<>();
        for (NewsTopicRel rel : newsTopicRelRepository.findByEventIdIn(eventIds)) {
            if (Objects.equals(rel.getTopicId(), topicId)) {
                existing.add(rel.getEventId());
            }
        }
        for (Long eventId : eventIds) {
            if (eventId == null || existing.contains(eventId)) {
                continue;
            }
            newsTopicRelRepository.save(NewsTopicRel.builder()
                    .topicId(topicId).eventId(eventId).build());
        }
    }

    private int countLinked(Long topicId) {
        return newsTopicRelRepository.findByTopicIdOrderByEventId(topicId).size();
    }

    private void closeIdleTopics() {
        Integer closed = transactionTemplate.execute(status ->
                newsTopicRepository.closeIdleTopics(NewsTopicContract.STATUS_CLOSED,
                        NewsTopicContract.STATUS_OPEN,
                        LocalDateTime.now().minusHours(TOPIC_IDLE_HOURS)));
        if (closed != null && closed > 0) {
            log.info("事件线：{} 条超过 {} 小时无新报道，置为 closed", closed, TOPIC_IDLE_HOURS);
        }
    }

    // ==================== 总编 + 撰稿（LLM，无事务） ====================

    private void runEditorAndWriters(JsonNode clustersNode, List<Candidate> candidates) {
        if (!editorEnabled) {
            // 总编层被关掉（运维开关）：候选只做"没撰稿"的诚实留痕，不悄悄跳过
            candidates.stream()
                    .filter(c -> isBlank(c.topic().getSummary()))
                    .forEach(c -> markSkip(c.topic(), NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE));
            return;
        }

        List<JsonNode> clustersToSend = new ArrayList<>();
        clustersNode.forEach(clustersToSend::add);
        if (clustersToSend.size() > EDITOR_MAX_CLUSTERS) {
            clustersToSend = clustersToSend.subList(0, EDITOR_MAX_CLUSTERS);
        }
        JsonNode response = newsClient.topicEditor(clustersToSend);
        Map<Integer, JsonNode> editorRows = new HashMap<>();
        boolean editorOk = NewsClient.isOk(response)
                && response.path("data").path("ok").asBoolean(false);
        if (editorOk) {
            for (JsonNode row : response.path("data").path("topics")) {
                if (row.isObject()) {
                    editorRows.put(row.path("index").asInt(0), row);
                }
            }
        } else {
            log.info("总编不可用（{}）：本轮候选记 editor_llm_unavailable，下次运行重试",
                    NewsClient.errorOf(response));
        }

        // 候选排序：总编圈了 worth_deep 的优先，其次成员多（信息量大）的
        List<Candidate> ordered = new ArrayList<>(candidates);
        ordered.sort(Comparator
                .comparing((Candidate c) -> !worthDeep(editorRows, c))
                .thenComparing(c -> -c.topic().getEventCount()));

        int written = 0;
        long budgetUsed = newsTopicRepository.countBySynthesizedAtGreaterThanEqual(
                LocalDateTime.now().truncatedTo(ChronoUnit.DAYS));
        for (Candidate candidate : ordered) {
            NewsTopic topic = candidate.topic();
            JsonNode editorRow = editorRows.get(candidate.clusterIndex());

            if (candidate.clusterIndex() > clustersToSend.size()) {
                continue;   // 超出总编视野的线本轮不评也不写，留到下一轮（不乱扣原因）
            }
            if (editorOk && editorRow != null) {
                applyEditorTitle(topic, editorRow);
            }
            if (!isBlank(topic.getSummary()) && !candidate.changed()) {
                continue;               // 已撰稿且无新成员：本轮无事可做，不留痕（避免刷 skipReason）
            }
            if (!editorOk) {
                markSkip(topic, NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE);
                continue;
            }
            if (editorRow != null && !editorRow.path("worth_deep").asBoolean(false)
                    && isBlank(topic.getSummary())) {
                // 总编明说不值得撰稿：留痕给用户看理由（editorReason 已随标题落下）
                markSkip(topic, NewsTopicContract.SKIP_EDITOR_LOW_PRIORITY);
                continue;
            }
            if (budgetUsed >= dailyBudget) {
                markSkip(topic, NewsTopicContract.SKIP_EDITOR_BUDGET_EXCEEDED);
                continue;
            }
            if (topic.getSynthesizedAt() != null
                    && topic.getSynthesizedAt().isAfter(LocalDateTime.now().minusMinutes(cooldownMinutes))) {
                // 有新成员但距上次撰稿太近：老结论先顶着，冷却过了再重写
                markSkip(topic, NewsTopicContract.SKIP_EDITOR_COOLDOWN);
                continue;
            }
            if (writeOne(topic)) {
                written++;
                budgetUsed++;
            }
        }
        log.info("总编{}：本轮撰稿 {}/{}（预算 {}/{}）",
                editorOk ? "成功" : "不可用", written, candidates.size(), budgetUsed, dailyBudget);
    }

    /** 撰稿一条事件线（LLM 在事务外，写库短事务）。失败按 editor_llm_unavailable 留痕。 */
    private boolean writeOne(NewsTopic topic) {
        List<NewsEvent> members = loadMembers(topic.getId());
        if (members.isEmpty()) {
            markSkip(topic, NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE);
            return false;
        }
        JsonNode response = newsClient.topicWriter(topic.getTitle(),
                members.stream().map(this::toWriterItem).toList());
        JsonNode data = NewsClient.isOk(response) ? response.path("data") : null;
        boolean ok = data != null && data.path("ok").asBoolean(false)
                && !data.path("story").asText("").isBlank();
        if (!ok) {
            log.info("撰稿失败（topic={} {}）：{}", topic.getId(), topic.getTitle(),
                    NewsClient.errorOf(response));
            markSkip(topic, NewsTopicContract.SKIP_EDITOR_LLM_UNAVAILABLE);
            return false;
        }
        transactionTemplate.executeWithoutResult(status -> {
            topic.setDigest(blankToNull(data.path("digest").asText("")));
            topic.setSummary(data.path("story").asText(""));
            topic.setOutlook(blankToNull(data.path("outlook").asText("")));
            String direction = blankToNull(data.path("direction").asText(""));
            topic.setDirection(direction != null && NewsTopicContract.DIRECTIONS.contains(direction)
                    ? direction : null);
            topic.setConfidence(data.path("confidence").isNumber()
                    ? data.path("confidence").asDouble() : null);
            topic.setSymbols(mergeJsonArrays(topic.getSymbols(), stringList(data.get("affected_symbols"))));
            topic.setSkipReason(null);
            topic.setSynthesizedAt(LocalDateTime.now());
            newsTopicRepository.save(topic);
        });
        log.info("撰稿完成：topic={} {}（{} 条成员）", topic.getId(), topic.getTitle(), members.size());
        return true;
    }

    /** 总编标题只在这里落库（upsert 的 code 覆写规则见 NewsTopic 类注释）。 */
    private void applyEditorTitle(NewsTopic topic, JsonNode row) {
        String title = blankToNull(row.path("title").asText(""));
        if (title == null || title.equals(topic.getTitle())) {
            return;
        }
        transactionTemplate.executeWithoutResult(status -> {
            topic.setTitle(title);
            topic.setTitleOrigin("editor");
            topic.setEditorReason(blankToNull(row.path("reason").asText("")));
            newsTopicRepository.save(topic);
        });
    }

    private void markSkip(NewsTopic topic, String reason) {
        if (Objects.equals(topic.getSkipReason(), reason)) {
            return;     // 同原因重复留痕只刷 updated_at，没有信息增量
        }
        transactionTemplate.executeWithoutResult(status -> {
            topic.setSkipReason(reason);
            newsTopicRepository.save(topic);
        });
    }

    // ==================== 查询（前端 API） ====================

    /**
     * 雷达页事件线列表。
     *
     * <p><b>默认只返回已撰稿（summary 非空）的线</b>（2026-10-09 用户实测反馈
     * "整页专题没人看"后的修正）：未撰稿的线大半是总编判定"信息量不足"的程序性
     * 披露 —— 编辑都说没有增量了，就不该占用户页面的位置；它们的成员报道
     * 仍逐条躺在下面的信息流里，信息不丢。{@code includeUnsynthesized} 供调试。
     */
    public List<NewsTopic> listTopics(int days, String symbol, boolean includeClosed,
                                      boolean includeUnsynthesized) {
        LocalDateTime since = LocalDateTime.now().minusDays(Math.max(1, days));
        List<NewsTopic> topics = newsTopicRepository.findActiveSince(
                since, PageRequest.of(0, 100));
        String needle = symbol == null || symbol.isBlank()
                ? null : "\"" + canonicalSymbol(symbol) + "\"";
        List<NewsTopic> filtered = new ArrayList<>();
        for (NewsTopic topic : topics) {
            if (!includeUnsynthesized && isBlank(topic.getSummary())) {
                continue;
            }
            if (!includeClosed && !NewsTopicContract.STATUS_OPEN.equals(topic.getStatus())) {
                continue;
            }
            if (needle != null && (topic.getSymbols() == null || !topic.getSymbols().contains(needle))) {
                continue;
            }
            filtered.add(topic);
        }
        return filtered;
    }

    /** 专题成员（时间倒序），供详情页/撰稿输入共用。 */
    public List<NewsEvent> loadMembers(Long topicId) {
        List<Long> eventIds = newsTopicRelRepository.findByTopicIdOrderByEventId(topicId)
                .stream().map(NewsTopicRel::getEventId).filter(Objects::nonNull).toList();
        if (eventIds.isEmpty()) {
            return List.of();
        }
        return newsEventRepository.findAllById(eventIds).stream()
                .sorted(Comparator.comparing(NewsEvent::getPublishedAt,
                        Comparator.nullsLast(Comparator.reverseOrder())))
                .toList();
    }

    public NewsTopic topic(Long id) {
        return newsTopicRepository.findById(id).orElse(null);
    }

    // ==================== JSON 组装 ====================

    /** 聚类输入：库行 → Python N4 需要的最小字段集（id 随成员透传回来）。 */
    private JsonNode toClusterItem(NewsEvent event) {
        ObjectNode node = mapper.createObjectNode();
        node.put("id", event.getId());
        node.put("symbol", event.getSymbol() == null ? "" : event.getSymbol());
        node.put("name", event.getName() == null ? "" : event.getName());
        node.put("title", event.getTitle() == null ? "" : event.getTitle());
        node.put("published_at", event.getPublishedAt() == null
                ? "" : event.getPublishedAt().format(PUBLISHED_AT));
        node.put("source_level", event.getSourceLevel() == null ? 2 : event.getSourceLevel());
        node.put("source_name", event.getSourceName() == null ? "" : event.getSourceName());
        node.put("url", event.getUrl() == null ? "" : event.getUrl());
        if (event.getDirection() == null) {
            node.putNull("direction");
        } else {
            node.put("direction", event.getDirection());
        }
        return node;
    }

    /** 撰稿输入：只喂结论字段（Python 侧同款纪律 —— 不重贴原文正文）。 */
    private JsonNode toWriterItem(NewsEvent event) {
        ObjectNode node = mapper.createObjectNode();
        node.put("published_at", event.getPublishedAt() == null
                ? "" : event.getPublishedAt().format(PUBLISHED_AT));
        node.put("source_level", event.getSourceLevel() == null ? 2 : event.getSourceLevel());
        node.put("source_name", event.getSourceName() == null ? "" : event.getSourceName());
        if (event.getDirection() == null) {
            node.putNull("direction");
        } else {
            node.put("direction", event.getDirection());
        }
        node.put("title", event.getTitle() == null ? "" : event.getTitle());
        node.put("plain_summary", event.getPlainSummary() == null ? "" : event.getPlainSummary());
        return node;
    }

    private List<Long> memberIdsOf(JsonNode cluster) {
        List<Long> ids = new ArrayList<>();
        for (JsonNode member : cluster.path("members")) {
            if (member.path("id").canConvertToLong()) {
                ids.add(member.path("id").asLong());
            }
        }
        return ids;
    }

    private JsonNode editorRowFor(Map<Integer, JsonNode> rows, Candidate candidate) {
        return rows.get(candidate.clusterIndex());
    }

    private boolean worthDeep(Map<Integer, JsonNode> rows, Candidate candidate) {
        JsonNode row = editorRowFor(rows, candidate);
        return row == null || row.path("worth_deep").asBoolean(false);
    }

    private LocalDateTime parseTime(String text) {
        if (text == null || text.isBlank()) {
            return null;
        }
        try {
            return LocalDateTime.parse(text, PUBLISHED_AT);
        } catch (DateTimeParseException e) {
            return null;
        }
    }

    private String canonicalSymbol(String symbol) {
        String text = symbol == null ? "" : symbol.trim().toUpperCase();
        return text.startsWith("SH") || text.startsWith("SZ") || text.startsWith("BJ")
                ? text : (text.matches("\\d{6}") ? guessMarketPrefix(text) + text : text);
    }

    /** 6 开头沪、3/0 开头深、8/4 北交所 —— 只用于列表过滤的宽松匹配（不落库）。 */
    private String guessMarketPrefix(String code) {
        if (code.startsWith("6")) {
            return "SH";
        }
        if (code.startsWith("8") || code.startsWith("4")) {
            return "BJ";
        }
        return "SZ";
    }

    private List<String> stringList(JsonNode node) {
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
            log.warn("事件线字段序列化失败，按空处理：{}", e.getMessage());
            return null;
        }
    }

    /** JSON 数组字符串合并去重（保序：先旧后新）。 */
    private String mergeJsonArrays(String existingJson, List<String> incoming) {
        Set<String> merged = new java.util.LinkedHashSet<>(stringList(readTree(existingJson)));
        merged.addAll(incoming);
        return writeJson(new ArrayList<>(merged));
    }

    private JsonNode readTree(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            return null;
        }
    }

    private static String text(JsonNode node, String field) {
        return node.path(field).asText("").trim();
    }

    private static String blankToNull(String value) {
        return value == null || value.isBlank() ? null : value.trim();
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
