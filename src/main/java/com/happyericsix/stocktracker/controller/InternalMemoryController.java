package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.MemoryEpisodeRequest;
import com.happyericsix.stocktracker.dto.MemoryEventRequest;
import com.happyericsix.stocktracker.dto.MemoryEventsRequest;
import com.happyericsix.stocktracker.dto.MemoryFactsRequest;
import com.happyericsix.stocktracker.dto.MemoryLessonsRequest;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.service.MemoryFactService;
import com.happyericsix.stocktracker.service.MemoryLessonService;
import com.happyericsix.stocktracker.service.MemoryService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 记忆系统的<b>内部接口</b>：只给 Python agent 用，不面向前端。
 *
 * <h3>为什么要单独一套内部接口，而不是让 Python 直连数据库</h3>
 * 这是"agent 不能修改数据层"这条约束的落地方式：
 * <ul>
 *   <li>数据库凭据只存在于 Java 进程，Python 侧没有任何连接串；</li>
 *   <li>Python 能做的事被<b>接口形状</b>限制死了——读上下文、读事件、追加事件、写摘要，
 *       没有 update / delete 这种能力可以调用；</li>
 *   <li>将来把数据库账号权限收紧到只有 SELECT/INSERT 时，代码和权限两层是对齐的。</li>
 * </ul>
 *
 * <h3>鉴权</h3>
 * 与 Python 侧的 {@code require_internal_token} 中间件对称：共享密钥放在
 * {@code X-Internal-Token} 头里，比较用 {@link MessageDigest#isEqual} 防时序侧信道。
 * <b>未配置密钥时一律拒绝</b>（fail closed）——这里能读到用户的全部对话记忆，
 * 宁可不可用也不能敞开。
 */
@RestController
@RequestMapping("/api/v1/internal/memory")
public class InternalMemoryController {

    private static final Logger log = LoggerFactory.getLogger(InternalMemoryController.class);

    private final MemoryService memoryService;
    private final MemoryFactService memoryFactService;
    private final MemoryLessonService memoryLessonService;
    private final String internalToken;

    public InternalMemoryController(MemoryService memoryService,
                                    MemoryFactService memoryFactService,
                                    MemoryLessonService memoryLessonService,
                                    @Value("${internal.api-token:}") String internalToken) {
        this.memoryService = memoryService;
        this.memoryFactService = memoryFactService;
        this.memoryLessonService = memoryLessonService;
        this.internalToken = internalToken;
    }

    /** 组装给 agent 的记忆上下文：今天几号 + 前情提要 + 画像 + 相关事实与经验 */
    @GetMapping("/context")
    public ResponseEntity<Map<String, Object>> context(
            @RequestParam Long userId,
            @RequestParam(required = false) String sessionKey,
            @RequestParam(required = false) String query,
            @RequestParam(required = false) String symbol,
            @RequestParam(required = false, defaultValue = "8") int factLimit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        return ResponseEntity.ok(memoryService.buildContext(userId, sessionKey, query, symbol, factLimit));
    }

    /** 向量索引语料（供 Python 侧建本地语义索引，几分钟拉一次即可） */
    @GetMapping("/index")
    public ResponseEntity<Map<String, Object>> index(
            @RequestParam Long userId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        return ResponseEntity.ok(memoryService.indexCorpus(userId));
    }

    /** 经验记忆（"这类问题上次是怎么解决的"） */
    @GetMapping("/lessons")
    public ResponseEntity<Map<String, Object>> lessons(
            @RequestParam Long userId,
            @RequestParam(required = false) String query,
            @RequestParam(required = false) String taskType,
            @RequestParam(required = false, defaultValue = "3") int limit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        return ResponseEntity.ok(Map.of("lessons",
                memoryLessonService.searchLessons(userId, query, taskType, limit)));
    }

    /** 写入经验（一次巩固抽取出来的结果）。一律以 pending 落库，见 MemoryLessonService。 */
    @PostMapping("/lessons")
    public ResponseEntity<Map<String, Object>> saveLessons(
            @RequestBody MemoryLessonsRequest request,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        if (request.getUserId() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "userId is required"));
        }
        List<Map<String, Object>> results = new ArrayList<>();
        for (MemoryLessonService.LessonWriteResult result : memoryLessonService.saveLessons(request)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", result.id());
            item.put("taskType", result.taskType());
            item.put("symptom", result.symptom());
            item.put("action", result.action());
            item.put("occurrences", result.occurrences());
            results.add(item);
        }
        return ResponseEntity.ok(Map.of("results", results, "count", results.size()));
    }

    /** 批量追加账本事件（工具调用结果入账走这里，避免每个工具一次 HTTP） */
    @PostMapping("/events/batch")
    public ResponseEntity<Map<String, Object>> appendEvents(
            @RequestBody MemoryEventsRequest request,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        if (request.getUserId() == null || request.getSessionKey() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "userId/sessionKey are required"));
        }
        List<MemoryEvent> events = new ArrayList<>();
        for (MemoryEventRequest item : request.getEvents() == null ? List.<MemoryEventRequest>of()
                : request.getEvents()) {
            events.add(MemoryEvent.builder()
                    .userId(request.getUserId())
                    .sessionKey(request.getSessionKey())
                    .kind(item.getKind())
                    .role(item.getRole())
                    .content(item.getContent())
                    .symbol(item.getSymbol())
                    .provenance(item.getProvenance())
                    .trust(item.getTrust())
                    .meta(item.getMeta())
                    .build());
        }
        return ResponseEntity.ok(Map.of("saved", memoryService.appendEvents(events)));
    }

    /** 撤回一条事实（逻辑撤回，走取代链；物理删除是另一条合规通道） */
    @PostMapping("/facts/{id}/retract")
    public ResponseEntity<Map<String, Object>> retractFact(
            @PathVariable Long id,
            @RequestParam Long userId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        boolean done = memoryFactService.retract(userId, id);
        return done ? ResponseEntity.ok(Map.of("retracted", id))
                : ResponseEntity.status(HttpStatus.NOT_FOUND).body(Map.of("error", "fact not found"));
    }

    /** 检索当前有效的语义事实（agent 的 memory_search 工具走这里，只读） */
    @GetMapping("/facts")
    public ResponseEntity<Map<String, Object>> facts(
            @RequestParam Long userId,
            @RequestParam(required = false) String query,
            @RequestParam(required = false) String symbol,
            @RequestParam(required = false) String factType,
            @RequestParam(required = false, defaultValue = "8") int limit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        return ResponseEntity.ok(Map.of("facts",
                memoryFactService.searchFacts(userId, query, symbol, factType, limit)));
    }

    /** 某个事实键的完整历史（"这个设置以前是什么"） */
    @GetMapping("/facts/history")
    public ResponseEntity<Map<String, Object>> factHistory(
            @RequestParam Long userId,
            @RequestParam String subject,
            @RequestParam String predicate,
            @RequestParam(required = false, defaultValue = "10") int limit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        return ResponseEntity.ok(Map.of("history",
                memoryFactService.factHistory(userId, subject, predicate, limit)));
    }

    /**
     * 批量写入语义事实（一次会话巩固抽取出来的结果）。
     * 「改口」在 Java 侧判定：同键不同值 = 追加新事实并指向旧的（见 MemoryFactService）。
     */
    @PostMapping("/facts")
    public ResponseEntity<Map<String, Object>> saveFacts(
            @RequestBody MemoryFactsRequest request,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        if (request.getUserId() == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "userId is required"));
        }
        List<Map<String, Object>> results = new ArrayList<>();
        for (MemoryFactService.FactWriteResult result : memoryFactService.saveFacts(request)) {
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", result.id());
            item.put("subject", result.subject());
            item.put("predicate", result.predicate());
            item.put("object", result.value());
            item.put("action", result.action());
            item.put("supersededId", result.supersededId());
            results.add(item);
        }
        return ResponseEntity.ok(Map.of("results", results, "count", results.size()));
    }

    /** 某个会话的全部账本事件（供 Python 生成摘要） */
    @GetMapping("/events")
    public ResponseEntity<Map<String, Object>> events(
            @RequestParam Long userId,
            @RequestParam String sessionKey,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        List<Map<String, Object>> events = new ArrayList<>();
        for (MemoryEvent event : memoryService.listEvents(userId, sessionKey)) {
            events.add(toMap(event));
        }
        return ResponseEntity.ok(Map.of("events", events));
    }

    /** 追加一条账本事件（只追加） */
    @PostMapping("/events")
    public ResponseEntity<Map<String, Object>> appendEvent(
            @RequestBody MemoryEventRequest request,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        MemoryEvent saved = memoryService.append(MemoryEvent.builder()
                .userId(request.getUserId())
                .sessionKey(request.getSessionKey())
                .kind(request.getKind())
                .role(request.getRole())
                .content(request.getContent())
                .symbol(request.getSymbol())
                .provenance(request.getProvenance())
                .trust(request.getTrust())
                .eventTime(request.getEventTime())
                .rawTimePhrase(request.getRawTimePhrase())
                .meta(request.getMeta())
                .build());
        if (saved == null) {
            return ResponseEntity.badRequest().body(Map.of("error", "incomplete event"));
        }
        return ResponseEntity.ok(Map.of("id", saved.getId()));
    }

    /** 保存一条摘要（版本号由 Java 分配，Python 不用操心） */
    @PostMapping("/episodes")
    public ResponseEntity<Map<String, Object>> saveEpisode(
            @RequestBody MemoryEpisodeRequest request,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        if (request.getUserId() == null || request.getSessionKey() == null
                || request.getSummary() == null || request.getSummary().isBlank()) {
            return ResponseEntity.badRequest().body(Map.of("error", "userId/sessionKey/summary are required"));
        }
        int version = memoryService.saveEpisode(request);
        return ResponseEntity.ok(Map.of("sessionKey", request.getSessionKey(), "version", version));
    }

    private boolean authorized(String token) {
        if (internalToken == null || internalToken.isBlank()) {
            log.error("internal.api-token 未配置，拒绝内部记忆接口访问");
            return false;
        }
        if (token == null) {
            return false;
        }
        return MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                internalToken.getBytes(StandardCharsets.UTF_8));
    }

    private <T> ResponseEntity<T> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
    }

    private Map<String, Object> toMap(MemoryEvent event) {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", event.getId());
        item.put("kind", event.getKind());
        item.put("role", event.getRole());
        item.put("content", event.getContent());
        item.put("symbol", event.getSymbol());
        item.put("provenance", event.getProvenance());
        item.put("trust", event.getTrust());
        // meta 里放的是结构化细节：工具调用的参数（已按声明脱敏）与
        // 每轮用量（P1 的 kind=usage 事件把 delta 放在这里）。
        // 不出的话，"用量/审计参数"就只能靠 SQL 看，接口层等于没这个能力。
        item.put("meta", event.getMeta());
        LocalDateTime occurredAt = event.getOccurredAt();
        item.put("occurredAt", occurredAt == null ? null : occurredAt.toString());
        return item;
    }
}
