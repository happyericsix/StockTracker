package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.MemoryFactsRequest;
import com.happyericsix.stocktracker.entity.MemoryFact;
import com.happyericsix.stocktracker.repository.MemoryFactRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * 语义事实（L2）的写入与检索。
 *
 * <h3>改口是怎么生效的（M1 的核心）</h3>
 * 用户先说"止损 8%"，后说"我改主意了，止损改成 5%"。抽取器会给出同一把键
 * （{@code user|stop_loss_pct}）上的两个不同值，于是：
 * <pre>
 *   fact#41  8%  supersedesId=null    ← 被 #57 取代 → 不再"当前有效"
 *   fact#57  5%  supersedesId=41      ← 当前有效
 * </pre>
 * 判定完全由 {@code saveFacts} 在这里做：<b>同键 + 值不同 = 取代</b>。
 * 之所以放在 Java 侧而不是让 Python 判断，是因为"谁取代谁"是数据一致性问题，
 * 必须由持有数据的一端在一个事务里定下来（agent 不碰数据层，见 InternalMemoryController）。
 *
 * <h3>两条防污染规则</h3>
 * <ul>
 *   <li>置信度低于 {@link #MIN_CONFIDENCE} 的事实不入库——模型瞎猜的东西不该变成"记忆"；</li>
 *   <li>同键同值不重复写入，避免每天巩固一次就把同一句话堆成一串重复事实。</li>
 * </ul>
 */
@Service
public class MemoryFactService {

    private static final Logger log = LoggerFactory.getLogger(MemoryFactService.class);

    static final double MIN_CONFIDENCE = 0.3;
    static final int MAX_FACTS_PER_BATCH = 20;
    static final int MAX_SUBJECT_CHARS = 128;
    static final int MAX_VALUE_CHARS = 512;
    /** 撤回记录的类型标记：它本身是一条事实行，但永远不进注入/检索结果 */
    public static final String FACT_TYPE_RETRACTION = "retraction";
    /** 长期画像取几条（稳定身份类事实，always-on 注入） */
    static final int PERSONA_LIMIT = 6;
    /** 进画像的类型：这些是"关于用户本身"的，观察类不算 */
    private static final List<String> PERSONA_TYPES = List.of("preference", "constraint", "goal", "holding");
    private static final double PERSONA_MIN_CONFIDENCE = 0.7;

    /** 值得优先注入上下文的事实类型（越靠前越"关于用户本身"） */
    private static final List<String> TYPE_PRIORITY =
            List.of("preference", "constraint", "holding", "goal", "decision", "observation");
    private static final Set<String> ALLOWED_TYPES = new LinkedHashSet<>(TYPE_PRIORITY);

    private static final Pattern ASCII_TOKEN = Pattern.compile("[a-zA-Z0-9_]{2,}");

    private final MemoryFactRepository factRepo;

    public MemoryFactService(MemoryFactRepository factRepo) {
        this.factRepo = factRepo;
    }

    // ==================== 写入：同键即取代 ====================

    /** 写入结果：每条事实落库后是新建、取代了旧的、还是被跳过。 */
    public record FactWriteResult(Long id, String subject, String predicate, String value,
                                  String action, Long supersededId) {
    }

    @Transactional
    public List<FactWriteResult> saveFacts(MemoryFactsRequest request) {
        List<FactWriteResult> results = new ArrayList<>();
        if (request == null || request.getUserId() == null || request.getFacts() == null) {
            return results;
        }

        List<MemoryFactRequest> incoming = request.getFacts();
        if (incoming.size() > MAX_FACTS_PER_BATCH) {
            log.warn("事实批次过大，只取前 {} 条 (user={})", MAX_FACTS_PER_BATCH, request.getUserId());
            incoming = incoming.subList(0, MAX_FACTS_PER_BATCH);
        }

        for (MemoryFactRequest item : incoming) {
            if (!isUsable(item)) {
                continue;
            }
            String subject = item.getSubject().trim();
            String predicate = item.getPredicate().trim();
            String value = item.getObject().trim();

            List<MemoryFact> active = factRepo.findActiveByKey(request.getUserId(), subject, predicate);

            MemoryFact same = active.stream()
                    .filter(f -> value.equalsIgnoreCase(f.getFactValue() == null ? "" : f.getFactValue().trim()))
                    .findFirst().orElse(null);
            if (same != null) {
                // 同键同值：什么都不做。这一步是防止"每天巩固一次"把同一句话堆成 N 条重复事实。
                results.add(new FactWriteResult(same.getId(), subject, predicate, value, "unchanged", null));
                continue;
            }

            Long supersedesId = active.isEmpty() ? null : active.get(0).getId();
            MemoryFact saved = factRepo.save(MemoryFact.builder()
                    .userId(request.getUserId())
                    .sessionKey(request.getSessionKey())
                    .subject(subject)
                    .predicate(predicate)
                    .factValue(value)
                    .factType(normalizeType(item.getFactType()))
                    .confidence(item.getConfidence() == null ? 0.6 : item.getConfidence())
                    .supersedesId(supersedesId)
                    .eventTime(item.getEventTime())
                    .rawTimePhrase(item.getRawTimePhrase())
                    .dataAsOf(item.getDataAsOf())
                    .recordedAt(LocalDateTime.now(ZoneId.of("Asia/Shanghai")))
                    .provenance(item.getProvenance() == null ? "model" : item.getProvenance())
                    .trust(item.getTrust() == null ? "medium" : item.getTrust())
                    .confirmed(Boolean.TRUE.equals(item.getConfirmed()))
                    .sourceEventIds(toJsonIds(request.getSourceEventIds()))
                    .build());

            results.add(new FactWriteResult(saved.getId(), subject, predicate, value,
                    supersedesId == null ? "created" : "superseded", supersedesId));
        }
        return results;
    }

    // ==================== 写入：客观事实（system 来源） ====================

    /**
     * 写入一批**客观事实**：由代码产生、可复算的观测（回测指标、模拟盘快照…）。
     *
     * <p>为什么单开一个入口，而不是让调用方自己填 {@code provenance}：
     * 这些字段（provenance/trust/confirmed/confidence）表达的是同一件事 ——
     * "这不是谁的主张，是可复算的观测"。让调用方各自填，迟早有人漏一个，
     * 而漏掉 {@code confirmed} 的那条事实在注入时会被标注成"未经确认"，
     * 与用户随口一说的东西混在一起，恰好废掉这条通道的全部意义。
     *
     * <p>它与 {@link #saveFacts} 的差别**只在默认值**：谁取代谁仍然由
     * {@link #saveFacts} 在同一个事务里判定 —— 这条通道刻意不另建一套一致性逻辑，
     * 否则"同一把键"这件事就会有两个真相源。
     *
     * <p>没有 {@code sessionKey} 就不写：一条查不出"哪来的"的事实比没有更糟。
     */
    @Transactional
    public List<FactWriteResult> recordObjective(Long userId, String sessionKey,
                                                 List<MemoryFactRequest> facts) {
        if (userId == null || facts == null || facts.isEmpty()) {
            return List.of();
        }
        if (isBlank(sessionKey)) {
            log.debug("缺少 sessionKey，跳过 {} 条客观事实 (user={})", facts.size(), userId);
            return List.of();
        }

        List<MemoryFactRequest> stamped = new ArrayList<>();
        for (MemoryFactRequest item : facts) {
            if (item == null) {
                continue;
            }
            item.setProvenance(ObjectiveFactKeys.PROVENANCE_SYSTEM);
            item.setTrust(ObjectiveFactKeys.TRUST_HIGH);
            item.setConfirmed(true);
            if (item.getConfidence() == null) {
                item.setConfidence(ObjectiveFactKeys.CONFIDENCE_CERTAIN);
            }
            if (isBlank(item.getFactType())) {
                item.setFactType(ObjectiveFactKeys.FACT_TYPE_OBSERVATION);
            }
            stamped.add(item);
        }
        if (stamped.isEmpty()) {
            return List.of();
        }

        MemoryFactsRequest request = new MemoryFactsRequest();
        request.setUserId(userId);
        request.setSessionKey(sessionKey);
        request.setFacts(stamped);
        List<FactWriteResult> results = saveFacts(request);
        long changed = results.stream()
                .filter(r -> !"unchanged".equals(r.action()))
                .count();
        if (changed > 0) {
            // 只在真的变了的时候记一行：这条日志是"策略表现什么时候变的"的第一现场
            log.info("客观事实入账 user={} 条数={} 变更={}", userId, results.size(), changed);
        }
        return results;
    }

    private boolean isUsable(MemoryFactRequest item) {
        if (item == null) {
            return false;
        }
        if (isBlank(item.getSubject()) || isBlank(item.getPredicate()) || isBlank(item.getObject())) {
            return false;
        }
        if (item.getConfidence() != null && item.getConfidence() < MIN_CONFIDENCE) {
            return false;
        }
        return item.getSubject().trim().length() <= MAX_SUBJECT_CHARS
                && item.getObject().trim().length() <= MAX_VALUE_CHARS;
    }

    private String normalizeType(String type) {
        if (type == null) {
            return "observation";
        }
        String normalized = type.trim().toLowerCase(Locale.ROOT);
        return ALLOWED_TYPES.contains(normalized) ? normalized : "observation";
    }

    private String toJsonIds(List<Long> ids) {
        if (ids == null || ids.isEmpty()) {
            return null;
        }
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < ids.size(); i++) {
            if (i > 0) {
                sb.append(',');
            }
            sb.append(ids.get(i));
        }
        return sb.append(']').toString();
    }

    // ==================== 读取：当前有效的事实 ====================

    /**
     * 检索当前有效的事实（M1 是关键词 + 类型 + 标的过滤，不做向量）。
     *
     * <p>为什么 M1 先不做向量：这个场景里最关键的匹配本来就是<b>精确</b>的
     * （标的代码、指标名、策略名），语义相似只在"换个说法的同义表述"上才需要。
     * 先把结构、时间、取代链做对，向量放 M2 加在同一处即可。
     */
    public List<Map<String, Object>> searchFacts(Long userId, String query, String symbol,
                                                 String factType, int limit) {
        if (userId == null) {
            return List.of();
        }
        int max = limit <= 0 ? 8 : Math.min(limit, 30);

        List<MemoryFact> candidates = new ArrayList<>();
        for (MemoryFact fact : factRepo.findActiveByUserId(userId)) {
            // 撤回记录不参与检索：它只是"某条事实作废了"的标记
            if (FACT_TYPE_RETRACTION.equals(fact.getFactType())) {
                continue;
            }
            if (factType != null && !factType.isBlank()
                    && !factType.trim().equalsIgnoreCase(fact.getFactType())) {
                continue;
            }
            if (symbol != null && !symbol.isBlank() && !matchesSymbol(fact, symbol)) {
                continue;
            }
            candidates.add(fact);
        }

        LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
        candidates.sort((a, b) -> Double.compare(score(b, query, symbol, now), score(a, query, symbol, now)));
        if (candidates.size() > max) {
            candidates = candidates.subList(0, max);
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryFact fact : candidates) {
            out.add(toMap(fact, now));
        }
        return out;
    }

    /** 完整历史链（"这个设置以前是什么"） */
    public List<Map<String, Object>> factHistory(Long userId, String subject, String predicate, int limit) {
        if (userId == null || isBlank(subject) || isBlank(predicate)) {
            return List.of();
        }
        int max = limit <= 0 ? 10 : Math.min(limit, 50);
        LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryFact fact : factRepo.findHistoryByKey(userId, subject.trim(), predicate.trim(),
                PageRequest.of(0, max))) {
            out.add(toMap(fact, now));
        }
        return out;
    }

    /**
     * 某个 subject 下若干谓词的**当前有效值**（一次查询拿一组，供服务侧拼装用）。
     *
     * <h3>为什么是"当前有效"而不是"最新一行"</h3>
     * 这两件事只有在"没人取代过"时才相同。事实通道的设计是"改口 = 追加 + 指向旧的"，
     * 所以"最新一行"可能是已经作废的那条 —— 按它渲染出来的报告会引用一个**曾经**的结论。
     * 判断当前有效只有一处口径：{@code findActiveByKey}（没有任何行指向它）。
     *
     * <p>返回值里没有的键 = 没有这条事实。**刻意不返回 null 占位**：
     * "没有记录"和"记录为空"是两件事，调用方必须分开处理（报告里一个说"未验证"，
     * 另一个说"验证过，值是空"）。
     */
    public Map<String, String> activeFactValues(Long userId, String subject, List<String> predicates) {
        Map<String, String> values = new LinkedHashMap<>();
        if (userId == null || isBlank(subject) || predicates == null || predicates.isEmpty()) {
            return values;
        }
        for (String predicate : predicates) {
            if (isBlank(predicate)) {
                continue;
            }
            List<MemoryFact> active = factRepo.findActiveByKey(userId, subject.trim(), predicate.trim());
            if (active.isEmpty()) {
                continue;
            }
            // 正常状态最多一条；真出现多条时取 recordedAt 最新的那条（并留下痕迹，不静默）
            MemoryFact latest = active.get(0);
            for (MemoryFact candidate : active) {
                LocalDateTime a = candidate.getRecordedAt();
                LocalDateTime b = latest.getRecordedAt();
                if (a != null && (b == null || a.isAfter(b))) {
                    latest = candidate;
                }
            }
            if (active.size() > 1) {
                log.warn("事实键上有多条当前有效记录 subject={} predicate={} count={}",
                        subject, predicate, active.size());
            }
            values.put(predicate.trim(), latest.getFactValue());
        }
        return values;
    }

    private boolean matchesSymbol(MemoryFact fact, String symbol) {
        String target = symbol.trim().toLowerCase(Locale.ROOT);
        String subject = fact.getSubject() == null ? "" : fact.getSubject().toLowerCase(Locale.ROOT);
        String value = fact.getFactValue() == null ? "" : fact.getFactValue().toLowerCase(Locale.ROOT);
        return subject.equals(target) || subject.contains(target) || value.contains(target);
    }

    // ==================== 撤回（不删除，走取代链） ====================

    /**
     * 撤回一条事实（用户说"忘掉这个"/"这条不对"时用）。
     *
     * <p>实现上不 DELETE，而是追加一条 {@code factType=retraction} 的事实行并指向被撤回者：
     * 被撤回的那条因此不再"当前有效"（复用既有取代链，不需要任何新机制），
     * 而历史链完整保留 —— 用户以后问"我之前不是说过成本 1500 吗"，
     * 系统能回答"那条已在 9 月 16 日撤回"。
     *
     * <p>注意这只是<b>逻辑撤回</b>。用户行使删除权（要求数据物理消失）需要另一条
     * 硬删除通道，那属于合规动作，不与这里混在一起（M3）。
     */
    @Transactional
    public boolean retract(Long userId, Long factId) {
        if (factId == null) {
            return false;
        }
        return factRepo.findById(factId)
                .filter(fact -> userId == null || userId.equals(fact.getUserId()))
                .map(target -> {
                    factRepo.save(MemoryFact.builder()
                            .userId(target.getUserId())
                            .sessionKey(target.getSessionKey())
                            .subject(target.getSubject())
                            .predicate(target.getPredicate())
                            .factValue("已撤回")
                            .factType(FACT_TYPE_RETRACTION)
                            .supersedesId(target.getId())
                            .confidence(1.0)
                            .recordedAt(LocalDateTime.now(ZoneId.of("Asia/Shanghai")))
                            .provenance("user")
                            .trust("high")
                            .confirmed(true)
                            .build());
                    log.info("事实已撤回 user={} factId={} key={}", target.getUserId(), factId, target.factKey());
                    return true;
                }).orElse(false);
    }

    // ==================== 长期画像（确定性派生，不用 LLM） ====================

    /**
     * 长期画像：从当前有效事实里挑出最"关于用户本身"的几条。
     *
     * <p>为什么不用 LLM 再蒸馏一层：画像的价值在于<b>稳定与可解释</b>。
     * 每多一次模型生成就多一次幻觉机会，而且用户问"你为什么认为我偏好稳健"时
     * 必须能指回具体那条事实。所以这里是确定性的挑选与排序：零成本、可追溯。
     *
     * <p>只有"偏好/约束/目标/持仓"四类进画像，且要求用户确认过或置信度够高 ——
     * 观察类的推测不该被当成"你是谁"。
     */
    public List<Map<String, Object>> persona(Long userId) {
        if (userId == null) {
            return List.of();
        }
        LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
        List<MemoryFact> picked = new ArrayList<>();
        for (MemoryFact fact : factRepo.findActiveByUserId(userId)) {
            if (!PERSONA_TYPES.contains(fact.getFactType())) {
                continue;
            }
            boolean trustworthy = Boolean.TRUE.equals(fact.getConfirmed())
                    || (fact.getConfidence() != null && fact.getConfidence() >= PERSONA_MIN_CONFIDENCE);
            if (trustworthy) {
                picked.add(fact);
            }
        }
        picked.sort((a, b) -> {
            int byType = Integer.compare(PERSONA_TYPES.indexOf(a.getFactType()),
                    PERSONA_TYPES.indexOf(b.getFactType()));
            return byType != 0 ? byType : b.getId().compareTo(a.getId());
        });
        if (picked.size() > PERSONA_LIMIT) {
            picked = picked.subList(0, PERSONA_LIMIT);
        }

        List<Map<String, Object>> out = new ArrayList<>();
        for (MemoryFact fact : picked) {
            out.add(toMap(fact, now));
        }
        return out;
    }

    // ==================== 相关性打分 ====================

    double score(MemoryFact fact, String query, String symbol, LocalDateTime now) {
        double score = 0;

        if (symbol != null && !symbol.isBlank()) {
            String target = symbol.trim().toLowerCase(Locale.ROOT);
            String subject = fact.getSubject() == null ? "" : fact.getSubject().toLowerCase(Locale.ROOT);
            if (subject.equals(target)) {
                score += 4;          // 就是这只标的的事实，最相关
            } else if (subject.contains(target)) {
                score += 2;
            }
        }

        if (query != null && !query.isBlank()) {
            String haystack = ((fact.getSubject() == null ? "" : fact.getSubject()) + " "
                    + (fact.getPredicate() == null ? "" : fact.getPredicate()) + " "
                    + (fact.getFactValue() == null ? "" : fact.getFactValue()) + " "
                    + (fact.getFactType() == null ? "" : fact.getFactType())).toLowerCase(Locale.ROOT);
            String lowered = query.toLowerCase(Locale.ROOT);

            // 英文/数字词：整词命中
            Matcher matcher = ASCII_TOKEN.matcher(lowered);
            int hits = 0;
            while (matcher.find() && hits < 6) {
                if (haystack.contains(matcher.group())) {
                    score += 1;
                    hits++;
                }
            }

            // 中文：没有分词依赖，用 2-gram 近似（"把止损改成5%" ↔ "stop_loss_pct" 靠谓词中文别名覆盖）
            for (String gram : chineseBigrams(lowered, 8)) {
                if (haystack.contains(gram)) {
                    score += 0.5;
                }
            }

            // 事实的值直接出现在用户这句话里，基本可以确定就是这件事
            String value = fact.getFactValue() == null ? "" : fact.getFactValue().trim();
            if (value.length() >= 2 && lowered.contains(value.toLowerCase(Locale.ROOT))) {
                score += 2;
            }
        }

        // 越"关于用户本身"的事实越值得注入
        int priorityIndex = TYPE_PRIORITY.indexOf(fact.getFactType());
        if (priorityIndex >= 0) {
            score += (TYPE_PRIORITY.size() - priorityIndex) * 0.3;
        }
        if (Boolean.TRUE.equals(fact.getConfirmed())) {
            score += 1;   // 用户确认过的，优先于模型推断的
        }
        if ("high".equals(fact.getTrust())) {
            score += 0.5;
        }
        score += recencyBonus(fact.getRecordedAt(), now);
        return score;
    }

    /**
     * 新鲜度加成（0~1，弱信号）。
     * 刻意做得弱：事实不是"越新越对"，而是"越新越可能是用户当下关心的"。
     * 真正决定有效性的永远是取代链，不是时间衰减。
     */
    private double recencyBonus(LocalDateTime recordedAt, LocalDateTime now) {
        if (recordedAt == null) {
            return 0;
        }
        long days = Math.abs(ChronoUnit.DAYS.between(recordedAt, now));
        return Math.max(0, 1.0 - (days / 180.0));
    }

    private List<String> chineseBigrams(String text, int limit) {
        List<String> grams = new ArrayList<>();
        for (int i = 0; i + 1 < text.length() && grams.size() < limit; i++) {
            char a = text.charAt(i);
            char b = text.charAt(i + 1);
            if (isCjk(a) && isCjk(b)) {
                grams.add(text.substring(i, i + 2));
            }
        }
        return grams;
    }

    private boolean isCjk(char c) {
        return c >= 0x4E00 && c <= 0x9FFF;
    }

    // ==================== 输出 ====================

    private Map<String, Object> toMap(MemoryFact fact, LocalDateTime now) {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", fact.getId());
        item.put("subject", fact.getSubject());
        item.put("predicate", fact.getPredicate());
        item.put("object", fact.getFactValue());
        item.put("factType", fact.getFactType());
        item.put("confidence", fact.getConfidence());
        item.put("confirmed", Boolean.TRUE.equals(fact.getConfirmed()));
        item.put("trust", fact.getTrust());
        item.put("provenance", fact.getProvenance());
        item.put("validFrom", fact.getValidFrom() == null ? null : fact.getValidFrom().toString());
        item.put("eventTime", fact.getEventTime() == null ? null : fact.getEventTime().toString());
        item.put("rawTimePhrase", fact.getRawTimePhrase());
        item.put("recordedAt", fact.getRecordedAt() == null ? null : fact.getRecordedAt().toString());
        item.put("dataAsOf", fact.getDataAsOf() == null ? null : fact.getDataAsOf().toString());
        item.put("active", true);

        // "此前为 X"：变更链对用户可见，避免模型凭空给出一个数字
        if (fact.getSupersedesId() != null) {
            factRepo.findById(fact.getSupersedesId()).ifPresent(previous -> {
                item.put("previousValue", previous.getFactValue());
                item.put("previousRecordedAt", previous.getRecordedAt() == null
                        ? null : previous.getRecordedAt().toString());
            });
        }
        return item;
    }

    private boolean isBlank(String text) {
        return text == null || text.isBlank();
    }

    /** 供上层日志/调试用：事实层的规模 */
    public Map<String, Object> stats(Long userId) {
        Map<String, Object> stats = new LinkedHashMap<>();
        stats.put("total", userId == null ? 0 : factRepo.countByUserId(userId));
        stats.put("active", userId == null ? 0 : factRepo.findActiveByUserId(userId).size());
        return stats;
    }
}
