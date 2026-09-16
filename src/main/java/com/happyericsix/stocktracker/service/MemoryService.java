package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.MemoryClient;
import com.happyericsix.stocktracker.dto.MemoryEpisodeRequest;
import com.happyericsix.stocktracker.entity.MemoryEpisode;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.repository.MemoryEpisodeRepository;
import com.happyericsix.stocktracker.repository.MemoryEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 记忆系统 M0：账本 + 前情提要。
 *
 * <h3>这一层负责什么</h3>
 * <ol>
 *   <li><b>写入</b>：把每一轮对话、每一次策略生成追加进 {@code memory_events}（只追加）；</li>
 *   <li><b>上下文组装</b>：把"今天几号 + 最近几段对话的前情提要"交给 Python agent 注入；</li>
 *   <li><b>触发巩固</b>：检测到用户跨天（新会话）时，异步让 Python 把上一段对话总结成摘要。</li>
 * </ol>
 *
 * <h3>为什么由 Java 触发巩固，而不是 Python 自己做</h3>
 * 数据在 Java 这边，只有它知道"上一段对话是哪段、有没有总结过"。
 * Python 只负责它最擅长的事——调 LLM 生成摘要。这符合"agent 不碰数据层"的约束：
 * Python 全程通过内部接口读写，没有任何数据库凭据。
 *
 * <h3>失败必须是无害的</h3>
 * 记忆是增强不是关键路径：巩固失败只记日志，绝不影响用户那条消息的回复
 * （见 {@link #ensurePreviousSessionConsolidated} 的 try/catch）。
 */
@Service
public class MemoryService {

    private static final Logger log = LoggerFactory.getLogger(MemoryService.class);

    private static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");
    private static final DateTimeFormatter SESSION_DATE = DateTimeFormatter.ofPattern("yyyy-MM-dd");

    /** 单条事件内容的字符上限：账本不是粘贴板，超长内容截断并标注 */
    static final int MAX_CONTENT_CHARS = 4000;
    /** 注入给模型的"前情提要"条数上限 */
    static final int RECAP_LIMIT = 3;
    /** 一次检查多少个历史会话是否需要总结 */
    static final int PENDING_SCAN = 3;
    /** 少于这个事件数的会话不值得总结（比如用户只发了一句就再没回来） */
    static final int MIN_EVENTS_FOR_RECAP = 2;
    /** 兜底摘录：单条内容的字符上限 / 最多带几条 */
    static final int DIGEST_CONTENT_CHARS = 160;
    static final int DIGEST_MAX_EVENTS = 6;
    /** 一次注入多少条事实（少而准，别把上下文塞满） */
    static final int FACT_LIMIT = 8;
    /** 一次注入多少条经验 */
    static final int LESSON_LIMIT = 3;
    /** 向量索引语料最多给多少条摘要 */
    static final int INDEX_EPISODE_LIMIT = 50;
    /** 向量索引语料最多给多少条事实 */
    static final int INDEX_FACT_LIMIT = 200;
    /** 会话进行中每积累多少条事件就再巩固一次（稳定阶段的间隔） */
    static final int CONSOLIDATE_EVERY_EVENTS = 8;
    /** 预热阶段的前两个触发点：第一轮问答结束就有记忆，之后逐步放缓 */
    static final int FIRST_CONSOLIDATE_AT = 2;
    static final int SECOND_CONSOLIDATE_AT = 4;
    /** 两次巩固之间的冷却，防止异步还没写完就被重复触发 */
    static final long CONSOLIDATE_COOLDOWN_MS = 60_000L;
    static final int MAX_COOLDOWN_ENTRIES = 512;

    private final MemoryEventRepository eventRepo;
    private final MemoryEpisodeRepository episodeRepo;
    private final MemoryClient memoryClient;
    private final MemoryFactService factService;
    private final MemoryLessonService lessonService;
    private final ObjectMapper mapper;

    /** 会话 → 上次巩固时间（只用于节流，见 maybeConsolidateCurrentSession） */
    private final Map<String, Long> lastConsolidationAt = new java.util.concurrent.ConcurrentHashMap<>();

    public MemoryService(MemoryEventRepository eventRepo, MemoryEpisodeRepository episodeRepo,
                         MemoryClient memoryClient, MemoryFactService factService,
                         MemoryLessonService lessonService, ObjectMapper mapper) {
        this.eventRepo = eventRepo;
        this.episodeRepo = episodeRepo;
        this.memoryClient = memoryClient;
        this.factService = factService;
        this.lessonService = lessonService;
        this.mapper = mapper;
    }

    // ==================== 会话键 ====================

    /**
     * 会话键 = {@code userId:yyyy-MM-dd}（Asia/Shanghai）。
     *
     * M0 有意用"一天 = 一段对话"这种轻量口径：前情提要天然按天切分，
     * 不需要额外引入会话生命周期管理（谁开、谁关、空闲多久算结束）。
     * 将来要精细到"一次连续对话"时，只改这一个方法。
     */
    public String currentSessionKey(Long userId) {
        return sessionKeyFor(userId, LocalDate.now(ZONE));
    }

    public String sessionKeyFor(Long userId, LocalDate date) {
        return userId + ":" + date.format(SESSION_DATE);
    }

    public String today() {
        return LocalDate.now(ZONE).format(SESSION_DATE);
    }

    // ==================== 写入（只追加） ====================

    /**
     * 追加一条事件。这是账本<b>唯一的写入口</b>，且只做 INSERT。
     *
     * 修复历史数据不在本次范围：本方法不做 UPDATE/DELETE，也不提供这样的方法。
     */
    @Transactional
    public MemoryEvent append(MemoryEvent event) {
        if (event.getUserId() == null || event.getSessionKey() == null || event.getContent() == null) {
            log.warn("跳过不完整的账本事件: user={} session={}", event.getUserId(), event.getSessionKey());
            return null;
        }
        event.setContent(truncate(event.getContent()));
        if (event.getKind() == null) {
            event.setKind("system");
        }
        if (event.getOccurredAt() == null) {
            event.setOccurredAt(LocalDateTime.now(ZONE));
        }
        return eventRepo.save(event);
    }

    /** 聊天事件的便捷写法：补上 provenance / trust 语义。 */
    @Transactional
    public MemoryEvent appendChat(Long userId, String sessionKey, String kind, String role,
                                  String content, String symbol) {
        boolean fromUser = "chat_user".equals(kind);
        return append(MemoryEvent.builder()
                .userId(userId)
                .sessionKey(sessionKey)
                .kind(kind)
                .role(role)
                .content(content)
                .symbol(symbol)
                .occurredAt(LocalDateTime.now(ZONE))
                .timeZone(ZONE.getId())
                .provenance(fromUser ? "user" : "model")
                // 用户亲口说的可信度最高；模型自己产出的内容只算中等，
                // 将来注入时要区分对待（模型产出的东西不能被当成用户的指令）。
                .trust(fromUser ? "high" : "medium")
                .build());
    }

    public List<MemoryEvent> listEvents(Long userId, String sessionKey) {
        if (userId == null || sessionKey == null || sessionKey.isBlank()) {
            return List.of();
        }
        return eventRepo.findByUserIdAndSessionKeyOrderByIdAsc(userId, sessionKey);
    }

    // ==================== 读取：组装上下文 ====================

    /**
     * 组装 agent 需要的记忆上下文。
     *
     * 注意这里<b>不返回</b>当前会话的原始消息：那些已经通过 history 参数给了模型，
     * 再塞一遍只会让同一句话在上下文里出现两次（历史故障里踩过同类问题）。
     * 这一层只补 history 给不了的东西——之前几段对话的摘要、与本次话题相关的事实、
     * 以及"今天是几号"。
     */
    public Map<String, Object> buildContext(Long userId, String sessionKey) {
        return buildContext(userId, sessionKey, null, null, FACT_LIMIT);
    }

    public Map<String, Object> buildContext(Long userId, String sessionKey, String query, String symbol) {
        return buildContext(userId, sessionKey, query, symbol, FACT_LIMIT);
    }

    /**
     * @param query     用户当前这句话，用于挑出<b>相关</b>的事实与经验（M1/M2 是关键词+类型打分）
     * @param symbol    当前话题的标的，标的完全匹配的事实优先
     * @param factLimit 返回多少条事实候选。Python 侧还会做一层语义融合（RRF），
     *                  所以它要多拿一些候选（30 左右）再自己收敛到 8 条注入
     */
    public Map<String, Object> buildContext(Long userId, String sessionKey, String query,
                                            String symbol, int factLimit) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("today", today());
        context.put("timeZone", ZONE.getId() + " (UTC+8)");

        // 长期画像：always-on 的稳定身份信息（确定性派生，见 MemoryFactService.persona）
        context.put("persona", userId == null ? List.of() : factService.persona(userId));

        // 事实层：少而准。宁可给 5 条真正相关的，也不要 30 条泛泛的偏好把上下文挤满。
        context.put("facts", userId == null ? List.of()
                : factService.searchFacts(userId, query, symbol, null, factLimit));

        // 经验层：这类问题以前是怎么解决的（永远只是参考，见 MemoryLessonService）
        context.put("lessons", userId == null ? List.of()
                : lessonService.searchLessons(userId, query, null, LESSON_LIMIT));

        List<Map<String, Object>> recaps = new ArrayList<>();
        if (userId != null) {
            for (MemoryEpisode episode : episodeRepo.findByUserIdOrderByCreatedAtDesc(
                    userId, PageRequest.of(0, RECAP_LIMIT))) {
                // 当前会话的摘要不注入（它还没结束，摘要也没意义）
                if (episode.getSessionKey() != null && episode.getSessionKey().equals(sessionKey)) {
                    continue;
                }
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("sessionKey", episode.getSessionKey());
                item.put("version", episode.getVersion());
                item.put("endedAt", episode.getEndedAt());
                item.put("summary", episode.getSummary());
                item.put("keyPoints", readJsonList(episode.getKeyPoints()));
                item.put("openQuestions", readJsonList(episode.getOpenQuestions()));
                recaps.add(item);
            }
        }
        context.put("recaps", recaps);

        List<String> pending = userId == null ? List.of() : pendingSessions(userId, sessionKey);
        context.put("pendingSessions", pending);
        // 兜底：最新那段"还没总结完"的会话给一份**原文摘录**。
        // 为什么需要：摘要是跨天时异步生成的，用户隔几天回来问"上次那个策略"，
        // 第一条消息时摘要极可能还没生成好 —— 只靠摘要就等于第一条消息必然失忆。
        // 原文摘录不花 LLM 调用，先兜住第一条，等摘要生成好了下一轮自然替换。
        if (!pending.isEmpty()) {
            context.put("pendingDigest", digest(userId, pending.get(0)));
        }
        return context;
    }

    /**
     * 向量索引语料：一次把该用户全部可检索内容取全，供 Python 侧建本地语义索引。
     *
     * <p>为什么不塞进 {@code /context}：Python 侧有 TTL 缓存，几分钟才拉一次，
     * 而 /context 是每轮对话都要调的 —— 把几十条摘要挂在每轮请求上纯属浪费。
     */
    public Map<String, Object> indexCorpus(Long userId) {
        Map<String, Object> corpus = new LinkedHashMap<>();
        corpus.put("facts", userId == null ? List.of()
                : factService.searchFacts(userId, null, null, null, INDEX_FACT_LIMIT));
        corpus.put("lessons", userId == null ? List.of() : lessonService.listForUser(userId, 50));

        List<Map<String, Object>> episodes = new ArrayList<>();
        if (userId != null) {
            // 同一个会话可能有多个摘要版本，只把最新版放进索引（否则同一段对话会重复命中）
            java.util.Set<String> seenSessions = new java.util.LinkedHashSet<>();
            for (MemoryEpisode episode : episodeRepo.findByUserIdOrderByCreatedAtDesc(
                    userId, PageRequest.of(0, INDEX_EPISODE_LIMIT))) {
                if (episode.getSessionKey() != null && !seenSessions.add(episode.getSessionKey())) {
                    continue;
                }
                Map<String, Object> item = new LinkedHashMap<>();
                item.put("id", episode.getId());
                item.put("sessionKey", episode.getSessionKey());
                item.put("version", episode.getVersion());
                item.put("summary", episode.getSummary());
                item.put("keyPoints", readJsonList(episode.getKeyPoints()));
                item.put("endedAt", episode.getEndedAt() == null ? null : episode.getEndedAt().toString());
                episodes.add(item);
            }
        }
        corpus.put("episodes", episodes);
        return corpus;
    }

    /**
     * 记忆概览：给"记忆"页顶部用的几个数字。
     *
     * <p>为什么值得单独做一个：用户和运营都需要一个"到底记没记住"的入口。
     * 尤其是 {@code lessons.pending}（多少条经验还没经人确认）——
     * 它直接对应"这些只是参考、还没被认可"，让用户知道系统没有偷偷把推测当事实用。
     */
    public Map<String, Object> overview(Long userId) {
        Map<String, Object> overview = new LinkedHashMap<>();
        if (userId == null) {
            return overview;
        }
        overview.put("facts", factService.stats(userId));
        overview.put("lessons", lessonService.statusCounts(userId));
        overview.put("episodes", episodeRepo.countByUserId(userId));
        overview.put("lastConsolidatedAt", episodeRepo
                .findByUserIdOrderByCreatedAtDesc(userId, PageRequest.of(0, 1)).stream()
                .findFirst()
                .map(episode -> episode.getCreatedAt() == null ? null : episode.getCreatedAt().toString())
                .orElse(null));
        return overview;
    }

    /** 批量追加账本事件（工具调用结果就是走这条路入账的，一次请求写完一轮）。 */
    @Transactional
    public int appendEvents(List<MemoryEvent> events) {
        int saved = 0;
        for (MemoryEvent event : events) {
            if (append(event) != null) {
                saved++;
            }
        }
        return saved;
    }

    /** 会话最后几条事件的原文摘录（给"摘要还没生成"时兜底用）。 */
    List<Map<String, Object>> digest(Long userId, String sessionKey) {
        List<MemoryEvent> events = listEvents(userId, sessionKey);
        if (events.isEmpty()) {
            return List.of();
        }
        int from = Math.max(0, events.size() - DIGEST_MAX_EVENTS);
        List<Map<String, Object>> items = new ArrayList<>();
        for (MemoryEvent event : events.subList(from, events.size())) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("role", event.getRole() == null ? "system" : event.getRole());
            item.put("occurredAt", event.getOccurredAt() == null ? null : event.getOccurredAt().toString());
            item.put("content", clip(event.getContent(), DIGEST_CONTENT_CHARS));
            items.add(item);
        }
        return items;
    }

    /**
     * 会话进行中的节奏巩固：达到触发点就把当前会话重新总结一次（含事实抽取）。
     *
     * <p><b>触发点用"绝对阈值"而不是"取模"</b>，这一点很关键：
     * 事件是"用户消息先落账 → 助手回复后落账"，而本方法是在处理用户消息时调用的，
     * 所以这里看到的计数<b>永远是奇数</b>（1、3、5、7…）。
     * 写成 {@code count % 8 == 0} 或 {@code count == 2} 之类的判定在生产里<b>永远不会命中</b>
     * ——单元测试里手写偶数计数反而会"通过"，这正是这类 bug 最难发现的地方。
     *
     * <p>阈值随"已有摘要版本数"递增，前两次排得密：
     * <pre>
     *   version 0 → 累计 2 条即巩固（第一轮问答结束就有记忆）
     *   version 1 → 累计 4 条
     *   version ≥2 → 8 × version 条（即稳定后大约每 8 条一次）
     * </pre>
     * 递增思路取自 TencentDB Agent Memory 的 {@code pipeline.enableWarmup} 生产默认值。
     *
     * <p>冷却窗口解决"异步重复触发"：巩固要几秒才写完摘要，这期间用户又发消息的话，
     * 版本号还没变、阈值仍满足，会重复烧一次 LLM（重复本身无害：摘要按版本追加、
     * 事实同键同值去重，但没必要）。
     */
    @Async
    public void maybeConsolidateCurrentSession(Long userId, String sessionKey) {
        if (userId == null || sessionKey == null) {
            return;
        }
        try {
            long count = eventRepo.countByUserIdAndSessionKey(userId, sessionKey);
            if (count <= 0) {
                return;
            }
            int version = episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(userId, sessionKey)
                    .map(MemoryEpisode::getVersion).orElse(0);
            long threshold = version == 0 ? FIRST_CONSOLIDATE_AT
                    : version == 1 ? SECOND_CONSOLIDATE_AT
                    : (long) CONSOLIDATE_EVERY_EVENTS * version;
            if (count < threshold) {
                return;
            }

            long now = System.currentTimeMillis();
            Long last = lastConsolidationAt.get(sessionKey);
            if (last != null && now - last < CONSOLIDATE_COOLDOWN_MS) {
                return;
            }
            if (lastConsolidationAt.size() > MAX_COOLDOWN_ENTRIES) {
                // 粗粒度清理即可：这张表只用于节流，丢了最多多一次 LLM 调用
                lastConsolidationAt.clear();
            }
            lastConsolidationAt.put(sessionKey, now);

            log.info("mid-session consolidation user={} session={} events={} version={}",
                    userId, sessionKey, count, version);
            memoryClient.consolidate(userId, sessionKey);
        } catch (Exception e) {
            log.warn("会话中途巩固失败 user={} session={}: {}", userId, sessionKey, e.getMessage());
        }
    }

    /** 有账本、但还没有摘要的历史会话（最多 PENDING_SCAN 个） */
    List<String> pendingSessions(Long userId, String sessionKey) {
        List<String> pending = new ArrayList<>();
        for (String candidate : eventRepo.findRecentOtherSessionKeys(
                userId, sessionKey == null ? "" : sessionKey, PageRequest.of(0, PENDING_SCAN))) {
            if (episodeRepo.existsByUserIdAndSessionKey(userId, candidate)) {
                continue;
            }
            if (eventRepo.countByUserIdAndSessionKey(userId, candidate) < MIN_EVENTS_FOR_RECAP) {
                continue;
            }
            pending.add(candidate);
        }
        return pending;
    }

    // ==================== 巩固（异步，失败无害） ====================

    /**
     * 如果上一段对话还没总结，就让 Python 补一份前情提要。
     *
     * <p>调用方必须通过 Spring 代理调用（从别的 Bean 注入进来），否则 {@code @Async} 不生效
     * —— 同类内部自调用会退化成同步执行，把 LLM 的几十秒加进用户等待里。
     *
     * <p>整个方法是 try/catch 到底的：巩固失败只记日志。
     * 记忆是增强功能，不能因为它挂了就让用户收不到回复。
     */
    @Async
    public void ensurePreviousSessionConsolidated(Long userId, String sessionKey) {
        if (userId == null || sessionKey == null) {
            return;
        }
        List<String> pending;
        try {
            pending = pendingSessions(userId, sessionKey);
        } catch (Exception e) {
            log.warn("记忆巩固检查失败 user={} session={}: {}", userId, sessionKey, e.getMessage());
            return;
        }

        for (String candidate : pending) {
            try {
                log.info("consolidating memory session {} for user {}", candidate, userId);
                memoryClient.consolidate(userId, candidate);
            } catch (Exception e) {
                // 一个会话失败就停：连着失败多半是 Python 侧或模型不可用，
                // 继续打只会浪费配额。下一条用户消息会再触发一次。
                log.warn("记忆巩固失败 user={} session={}: {}", userId, candidate, e.getMessage());
                break;
            }
        }
    }

    // ==================== 摘要落库（版本化） ====================

    /** 保存摘要，版本号 = 现有最大版本 + 1（不覆盖旧版本）。返回新版本号。 */
    @Transactional
    public int saveEpisode(MemoryEpisodeRequest request) {
        int version = episodeRepo.findFirstByUserIdAndSessionKeyOrderByVersionDesc(
                request.getUserId(), request.getSessionKey()).map(e -> e.getVersion() + 1).orElse(1);

        MemoryEpisode episode = MemoryEpisode.builder()
                .userId(request.getUserId())
                .sessionKey(request.getSessionKey())
                .version(version)
                .startedAt(request.getStartedAt())
                .endedAt(request.getEndedAt())
                .summary(truncate(request.getSummary()))
                .keyPoints(toJson(request.getKeyPoints()))
                .openQuestions(toJson(request.getOpenQuestions()))
                .entities(toJson(request.getEntities()))
                .sourceEventIds(toJson(request.getSourceEventIds()))
                .model(request.getModel())
                .build();
        episodeRepo.save(episode);
        log.info("saved memory episode user={} session={} v{}", request.getUserId(), request.getSessionKey(), version);
        return version;
    }

    // ==================== 工具方法 ====================

    private String truncate(String text) {
        if (text == null || text.length() <= MAX_CONTENT_CHARS) {
            return text;
        }
        return text.substring(0, MAX_CONTENT_CHARS) + "…（已截断）";
    }

    private String clip(String text, int limit) {
        if (text == null) {
            return null;
        }
        return text.length() <= limit ? text : text.substring(0, limit) + "…";
    }

    private String toJson(Object value) {
        if (value == null) {
            return null;
        }
        try {
            return mapper.writeValueAsString(value);
        } catch (Exception e) {
            // 摘要的附属字段序列化失败不该让整条摘要丢掉落库
            log.warn("记忆字段序列化失败: {}", e.getMessage());
            return null;
        }
    }

    private List<String> readJsonList(String json) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            return mapper.readValue(json, mapper.getTypeFactory()
                    .constructCollectionType(List.class, String.class));
        } catch (Exception e) {
            return List.of();
        }
    }
}
