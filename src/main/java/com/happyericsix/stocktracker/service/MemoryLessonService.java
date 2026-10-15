package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryLessonRequest;
import com.happyericsix.stocktracker.dto.MemoryLessonsRequest;
import com.happyericsix.stocktracker.entity.MemoryLesson;
import com.happyericsix.stocktracker.repository.MemoryLessonRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 经验记忆（L3）的写入与检索。
 *
 * <h3>去重靠"键"而不是"去重逻辑"</h3>
 * 键 = (taskType, symptom)。同类问题再次出现时<b>追加一行</b>：
 * 行数就是"遇到过几次"，最新一行就是"当前有效经验"。这样既满足只追加，
 * 又天然得到复发计数——而复发次数正是"值不值得升格成 skill"的判据
 * （≥3 次可以提示人去审核）。
 *
 * <h3>注入时永远只是参考</h3>
 * 返回结果里带 {@code status} 与 {@code occurrences}，渲染层会把"未审核"标出来。
 * 系统<b>不会</b>把经验当成指令执行，也不会自动把它变成可执行规则。
 */
@Service
public class MemoryLessonService {

    private static final Logger log = LoggerFactory.getLogger(MemoryLessonService.class);

    /** 建议升格为 skill 的复发次数阈值（超过就在返回里提示，让人去审核） */
    static final int PROMOTION_HINT_OCCURRENCES = 3;
    static final int MAX_LESSONS_PER_BATCH = 10;
    static final int MAX_SYMPTOM_CHARS = 200;
    static final double MIN_CONFIDENCE = 0.3;

    private static final Pattern ASCII_TOKEN = Pattern.compile("[a-zA-Z0-9_]{2,}");

    /** 任务类型的中文标签（见 MemoryDisplayLabels：展示语义统一由 Java 出） */
    private static final Map<String, String> TASK_TYPE_LABELS = Map.of(
            "generate_strategy", "生成策略",
            "backtest", "回测",
            "quote_lookup", "查行情",
            "analysis", "技术分析",
            "screening", "选股",
            "news_lookup", "新闻公告",
            "general", "通用");

    private final MemoryLessonRepository lessonRepo;
    private final ObjectMapper mapper;

    public MemoryLessonService(MemoryLessonRepository lessonRepo, ObjectMapper mapper) {
        this.lessonRepo = lessonRepo;
        this.mapper = mapper;
    }

    // ==================== 写入 ====================

    public record LessonWriteResult(Long id, String taskType, String symptom, String action,
                                    long occurrences) {
    }

    @Transactional
    public List<LessonWriteResult> saveLessons(MemoryLessonsRequest request) {
        List<LessonWriteResult> results = new ArrayList<>();
        if (request == null || request.getUserId() == null || request.getLessons() == null) {
            return results;
        }
        List<MemoryLessonRequest> incoming = request.getLessons();
        if (incoming.size() > MAX_LESSONS_PER_BATCH) {
            incoming = incoming.subList(0, MAX_LESSONS_PER_BATCH);
        }

        for (MemoryLessonRequest item : incoming) {
            if (!isUsable(item)) {
                continue;
            }
            String taskType = item.getTaskType().trim().toLowerCase(Locale.ROOT);
            String symptom = normalizeSymptomForStorage(item.getSymptom());

            MemoryLesson saved = lessonRepo.save(MemoryLesson.builder()
                    .userId(request.getUserId())
                    .sessionKey(request.getSessionKey())
                    .taskType(taskType)
                    .symptom(symptom)
                    // 原文与键分离（记忆白盒）：symptom 是去标点小写的规范化键（计数/匹配用），
                    // symptomText 保留 LLM 原句给用户读。历史行为 null 时前端回退 symptom。
                    .symptomText(clip(item.getSymptom(), 200))
                    .scope(scopeOf(request.getSessionKey()))
                    .context(toJson(item.getContext()))
                    .attempts(toJson(item.getAttempts()))
                    .resolution(clip(item.getResolution(), 1000))
                    .reusableRule(clip(item.getReusableRule(), 500))
                    // 句级证据优先（LLM 引用的行号已在 Python 侧映射成具体事件 id），
                    // 没有就退回请求级的整段会话 id —— 来源宁可粗，不能断
                    .evidenceEventIds(toJsonIds(!isEmpty(item.getEvidenceEventIds())
                            ? item.getEvidenceEventIds() : request.getEvidenceEventIds()))
                    // 一律 pending：经验要经过人审核才会变成"可信做法"，
                    // 否则被污染的一条经验会持续影响后面所有对话（轨迹投毒）
                    .status(MemoryLesson.STATUS_PENDING)
                    .confidence(item.getConfidence() == null ? 0.6 : item.getConfidence())
                    .provenance(item.getProvenance() == null ? "model" : item.getProvenance())
                    .trust(item.getTrust() == null ? "medium" : item.getTrust())
                    .recordedAt(LocalDateTime.now(ZoneId.of("Asia/Shanghai")))
                    .build());

            long occurrences = lessonRepo.countByUserIdAndTaskTypeAndSymptom(
                    request.getUserId(), taskType, symptom);
            results.add(new LessonWriteResult(saved.getId(), taskType, symptom, "created", occurrences));
            if (occurrences >= PROMOTION_HINT_OCCURRENCES) {
                // 只提示，不自动升格：经验→skill 必须过人这一关
                log.info("经验复发 {} 次，建议人工审核是否升格为 skill: user={} {} | {}",
                        occurrences, request.getUserId(), taskType, symptom);
            }
        }
        return results;
    }

    private boolean isUsable(MemoryLessonRequest item) {
        if (item == null) {
            return false;
        }
        if (isBlank(item.getTaskType()) || isBlank(item.getSymptom())) {
            return false;
        }
        // 没有"怎么解决的"就不算经验，只是一条抱怨
        if (isBlank(item.getResolution()) && isBlank(item.getReusableRule())) {
            return false;
        }
        return item.getConfidence() == null || item.getConfidence() >= MIN_CONFIDENCE;
    }

    private static boolean isEmpty(List<?> list) {
        return list == null || list.isEmpty();
    }

    /**
     * 记忆分区推导（2026-10-15）：从 sessionKey 前缀判断这条经验属于哪个域。
     * {@code weekly-reflection:strategy:N} → {@code strategy:N}（该策略独享，
     * 委员会回读只取本策略 scope，多策略不串味）；其余一律 chat。
     * sessionKey 是写入链路自带的，不需要调用方多传一个参数 —— 少一个能传错的参数。
     */
    static String scopeOf(String sessionKey) {
        if (sessionKey != null && sessionKey.startsWith("weekly-reflection:strategy:")) {
            String rest = sessionKey.substring("weekly-reflection:strategy:".length()).trim();
            if (!rest.isEmpty() && rest.chars().allMatch(Character::isDigit)) {
                return MemoryLesson.strategyScope(Long.parseLong(rest));
            }
        }
        return MemoryLesson.SCOPE_CHAT;
    }

    /** 审核：pending → active（只有人/回归测试能调这个接口）。 */
    @Transactional
    public boolean activate(Long userId, Long lessonId) {
        return updateStatus(userId, lessonId, MemoryLesson.STATUS_ACTIVE);
    }

    @Transactional
    public boolean retire(Long userId, Long lessonId) {
        return updateStatus(userId, lessonId, MemoryLesson.STATUS_RETIRED);
    }

    private boolean updateStatus(Long userId, Long lessonId, String status) {
        return lessonRepo.findById(lessonId)
                .filter(lesson -> userId == null || userId.equals(lesson.getUserId()))
                .map(lesson -> {
                    lesson.setStatus(status);
                    lessonRepo.save(lesson);
                    return true;
                }).orElse(false);
    }

    // ==================== 检索 ====================

    /**
     * 按当前话题挑出可能用得上的经验。
     *
     * <p>只返回"每个键的最新一条"，并按 <b>active 优先、复发次数、相关性、新鲜度</b> 排序。
     * 复发次数参与排序是有意的：同一个坑踩过三次，比只踩过一次更值得提醒。
     */
    public List<Map<String, Object>> searchLessons(Long userId, String query, String taskType, int limit) {
        return searchLessons(userId, query, taskType, null, limit);
    }

    /**
     * 按当前话题挑出可能用得上的经验（scope 版，2026-10-15 记忆分区）。
     *
     * <p>scope 非空时只返回该域的经验 —— 委员会回读传 {@code strategy:N}，
     * 于是"策略 A 的委员会"永远看不到"策略 B 的教训"（多模拟盘不串味）；
     * scope=null 表示不过滤（记忆页全量展示用）。
     */
    public List<Map<String, Object>> searchLessons(Long userId, String query, String taskType,
                                                   String scope, int limit) {
        if (userId == null) {
            return List.of();
        }
        int max = limit <= 0 ? 3 : Math.min(limit, 20);

        Map<String, MemoryLesson> latestByKey = new LinkedHashMap<>();
        for (MemoryLesson lesson : lessonRepo.findByUserIdOrderByIdDesc(userId)) {
            if (MemoryLesson.STATUS_RETIRED.equals(lesson.getStatus())) {
                continue;
            }
            if (taskType != null && !taskType.isBlank()
                    && !taskType.trim().equalsIgnoreCase(lesson.getTaskType())) {
                continue;
            }
            if (scope != null && !scope.isBlank() && !scope.equals(lessonScope(lesson))) {
                continue;
            }
            latestByKey.putIfAbsent(lesson.lessonKey(), lesson);
        }

        LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
        List<MemoryLesson> candidates = new ArrayList<>(latestByKey.values());
        candidates.sort((a, b) -> Double.compare(score(b, query, now), score(a, query, now)));
        if (candidates.size() > max) {
            candidates = candidates.subList(0, max);
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryLesson lesson : candidates) {
            out.add(toMap(lesson));
        }
        return out;
    }

    double score(MemoryLesson lesson, String query, LocalDateTime now) {
        double score = 0;
        if (MemoryLesson.STATUS_ACTIVE.equals(lesson.getStatus())) {
            // 人工确认过的经验优先于没人看过的
            score += 3;
        }
        long occurrences = lessonRepo.countByUserIdAndTaskTypeAndSymptom(
                lesson.getUserId(), lesson.getTaskType(), lesson.getSymptom());
        score += Math.min(occurrences, 5) * 0.6;

        if (query != null && !query.isBlank()) {
            String lowered = query.toLowerCase(Locale.ROOT);
            String haystack = ((lesson.getTaskType() == null ? "" : lesson.getTaskType()) + " "
                    + (lesson.getSymptom() == null ? "" : lesson.getSymptom()) + " "
                    + (lesson.getSymptomText() == null ? "" : lesson.getSymptomText()) + " "
                    + (lesson.getReusableRule() == null ? "" : lesson.getReusableRule()) + " "
                    + (lesson.getResolution() == null ? "" : lesson.getResolution()))
                    .toLowerCase(Locale.ROOT);
            Matcher matcher = ASCII_TOKEN.matcher(lowered);
            int hits = 0;
            while (matcher.find() && hits < 6) {
                if (haystack.contains(matcher.group())) {
                    score += 1;
                    hits++;
                }
            }
            for (String gram : chineseBigrams(lowered, 8)) {
                if (haystack.contains(gram)) {
                    score += 0.5;
                }
            }
        }

        if (lesson.getRecordedAt() != null) {
            long days = Math.abs(ChronoUnit.DAYS.between(lesson.getRecordedAt(), now));
            score += Math.max(0, 1.0 - (days / 180.0));
        }
        return score;
    }

    /** 实体的 scope（历史行可能为 null，视作 chat —— 与迁移的 DEFAULT 一致）。 */
    private static String lessonScope(MemoryLesson lesson) {
        return lesson.getScope() == null ? MemoryLesson.SCOPE_CHAT : lesson.getScope();
    }

    // ==================== 输出 ====================

    private Map<String, Object> toMap(MemoryLesson lesson) {
        long occurrences = lessonRepo.countByUserIdAndTaskTypeAndSymptom(
                lesson.getUserId(), lesson.getTaskType(), lesson.getSymptom());
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", lesson.getId());
        item.put("taskType", lesson.getTaskType());
        // 中文任务类型（"回测""查行情"）：给前端做分类展示，没有它用户看到的是英文键
        item.put("taskTypeLabel",
                TASK_TYPE_LABELS.getOrDefault(lesson.getTaskType(), lesson.getTaskType()));
        // 症状原文优先（记忆白盒）：symptom 是规范化键，不是给人读的句子
        item.put("symptom", lesson.getSymptomText() != null && !lesson.getSymptomText().isBlank()
                ? lesson.getSymptomText() : lesson.getSymptom());
        item.put("symptomRaw", lesson.getSymptomText());
        item.put("context", fromJson(lesson.getContext()));
        item.put("attempts", fromJson(lesson.getAttempts()));
        item.put("resolution", lesson.getResolution());
        item.put("reusableRule", lesson.getReusableRule());
        item.put("status", lesson.getStatus());
        item.put("scope", lessonScope(lesson));
        item.put("occurrences", occurrences);
        item.put("confidence", lesson.getConfidence());
        item.put("trust", lesson.getTrust());
        item.put("recordedAt", lesson.getRecordedAt() == null ? null : lesson.getRecordedAt().toString());
        // 句级来源（记忆白盒）：前端"查看来源"按钮按这些 id 拉原始对话
        item.put("evidenceEventIds", fromJson(lesson.getEvidenceEventIds()));
        item.put("promotionSuggested", occurrences >= PROMOTION_HINT_OCCURRENCES);
        return item;
    }

    /** JSON 文本（List/Map 序列化产物）→ 对象；读不懂返回 null（不假装）。 */
    private Object fromJson(String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return mapper.readValue(json, Object.class);
        } catch (Exception e) {
            return null;
        }
    }

    public List<Map<String, Object>> listForUser(Long userId, int limit) {
        if (userId == null) {
            return List.of();
        }
        int max = limit <= 0 ? 50 : Math.min(limit, 200);
        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryLesson lesson : lessonRepo.findByUserIdOrderByIdDesc(userId)) {
            if (out.size() >= max) {
                break;
            }
            out.add(toMap(lesson));
        }
        return out;
    }

    /**
     * 按状态计数，用于"记忆"页的概览。
     *
     * <p>"待确认"这个数字是<b>要显眼</b>的：它代表有多少条经验还没经过人审。
     * 未确认的经验只是参考，用户看到了才知道该不该确认或停用。
     */
    public Map<String, Object> statusCounts(Long userId) {
        Map<String, Object> counts = new LinkedHashMap<>();
        int pending = 0;
        int active = 0;
        int retired = 0;
        if (userId != null) {
            for (MemoryLesson lesson : lessonRepo.findByUserIdOrderByIdDesc(userId)) {
                switch (lesson.getStatus() == null ? MemoryLesson.STATUS_PENDING : lesson.getStatus()) {
                    case MemoryLesson.STATUS_ACTIVE -> active++;
                    case MemoryLesson.STATUS_RETIRED -> retired++;
                    default -> pending++;
                }
            }
        }
        counts.put("pending", pending);
        counts.put("active", active);
        counts.put("retired", retired);
        counts.put("total", pending + active + retired);
        return counts;
    }

    /** 供前端"记忆管理"用：这条经验出现过几次、每次的原始记录。 */
    public List<Map<String, Object>> history(Long userId, String taskType, String symptom) {
        if (userId == null || isBlank(taskType) || isBlank(symptom)) {
            return List.of();
        }
        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryLesson lesson : lessonRepo.findByKey(userId, taskType.trim(), symptom.trim())) {
            out.add(toMap(lesson));
        }
        return out;
    }

    // ==================== 工具方法 ====================

    /**
     * 症状的规范化键：去空白与标点 + 小写。计数与去重都按它来。
     *
     * <p>为什么必须有：symptom 由 LLM 生成，同一现象每次的标点/空白几乎必然不同
     * （"回测，数据不足。" vs "回测数据不足"），而 occurrences 是
     * {@code countByUserIdAndTaskTypeAndSymptom} 的**精确**匹配——不收敛的话
     * "复发 3 次建议升格"这个机制永远触发不了。
     *
     * <p>边界（有意为之）：只做字符级收敛，"回测时提示数据不足"与"回测提示数据不足"
     * 这类措辞差异仍算不同症状——再进一步就要做语义相似度，误合并两个不同问题的
     * 代价比漏计数更高。存量未规范化的行会在下一次同症状写入时开一条新键，
     * 渐进收敛即可，不值得停机迁移。
     */
    static String normalizeSymptomKey(String symptom) {
        if (symptom == null) {
            return "";
        }
        return symptom
                .replaceAll("[\\s\\p{Punct}\\p{IsPunctuation}]+", "")
                .toLowerCase(Locale.ROOT);
    }

    private String normalizeSymptomForStorage(String raw) {
        String clipped = clip(raw == null ? "" : raw.trim(), MAX_SYMPTOM_CHARS);
        String normalized = normalizeSymptomKey(clipped);
        // 全是标点的症状规范化后为空：保留原文，交给 isUsable 的非空校验兜底
        return normalized.isEmpty() ? clipped : normalized;
    }

    private List<String> chineseBigrams(String text, int limit) {
        List<String> grams = new ArrayList<>();
        for (int i = 0; i + 1 < text.length() && grams.size() < limit; i++) {
            char a = text.charAt(i);
            char b = text.charAt(i + 1);
            if (a >= 0x4E00 && a <= 0x9FFF && b >= 0x4E00 && b <= 0x9FFF) {
                grams.add(text.substring(i, i + 2));
            }
        }
        return grams;
    }

    private String toJson(Object value) {
        if (value == null) {
            return null;
        }
        if (value instanceof String text) {
            return text;
        }
        try {
            return mapper.writeValueAsString(value);
        } catch (Exception e) {
            log.warn("经验字段序列化失败: {}", e.getMessage());
            return null;
        }
    }

    private String toJsonIds(List<Long> ids) {
        return toJson(ids);
    }

    private String clip(String text, int limit) {
        if (text == null) {
            return null;
        }
        return text.length() <= limit ? text : text.substring(0, limit) + "…";
    }

    private boolean isBlank(String text) {
        return text == null || text.isBlank();
    }
}
