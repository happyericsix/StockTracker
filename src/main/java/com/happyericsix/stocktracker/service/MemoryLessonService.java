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
            String symptom = clip(item.getSymptom().trim(), MAX_SYMPTOM_CHARS);

            MemoryLesson saved = lessonRepo.save(MemoryLesson.builder()
                    .userId(request.getUserId())
                    .sessionKey(request.getSessionKey())
                    .taskType(taskType)
                    .symptom(symptom)
                    .context(toJson(item.getContext()))
                    .attempts(toJson(item.getAttempts()))
                    .resolution(clip(item.getResolution(), 1000))
                    .reusableRule(clip(item.getReusableRule(), 500))
                    .evidenceEventIds(toJsonIds(request.getEvidenceEventIds()))
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

    // ==================== 输出 ====================

    private Map<String, Object> toMap(MemoryLesson lesson) {
        long occurrences = lessonRepo.countByUserIdAndTaskTypeAndSymptom(
                lesson.getUserId(), lesson.getTaskType(), lesson.getSymptom());
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", lesson.getId());
        item.put("taskType", lesson.getTaskType());
        item.put("symptom", lesson.getSymptom());
        item.put("resolution", lesson.getResolution());
        item.put("reusableRule", lesson.getReusableRule());
        item.put("status", lesson.getStatus());
        item.put("occurrences", occurrences);
        item.put("confidence", lesson.getConfidence());
        item.put("trust", lesson.getTrust());
        item.put("recordedAt", lesson.getRecordedAt() == null ? null : lesson.getRecordedAt().toString());
        item.put("promotionSuggested", occurrences >= PROMOTION_HINT_OCCURRENCES);
        return item;
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
