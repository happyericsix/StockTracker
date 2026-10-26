package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import com.happyericsix.stocktracker.dto.ChatSendRequest;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.Future;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

/**
 * 聊天机器人服务：异步调用 Python LLM（/api/v1/agent/chat），
 * 回复直接落库 + SSE 推送。不依赖 MessageService，避免循环依赖。
 */
@Service
public class ChatService {

    private static final Logger log = LoggerFactory.getLogger(ChatService.class);

    /** 聊天消息类型（与 MessageService.CHAT_TYPES 一致） */
    private static final List<String> CHAT_TYPES = List.of("CHAT_USER", "CHAT_BOT");

    /**
     * 单次带上的历史消息条数上限。
     * 服务端 agent/memory.py 还有一层字符预算兜底，两边都不设限的话
     * 请求体会随对话无限增长。
     */
    private static final int MAX_HISTORY_MESSAGES = 12;

    /**
     * 悬空轮占位（2026-10-20）：用户连发消息时，旧轮回复被中断丢弃，历史里留下
     * "只问未答"的缺口。不补这条占位，模型会把旧问题当成欠着的账抢着回答，
     * 用户新消息反而被晾在一边（线上实测：问大盘 → 连发"你好" → 回复是大盘分析）。
     */
    private static final Map<String, String> DANGLING_TURN_PLACEHOLDER = Map.of(
            "role", "assistant",
            "content", "（上面这条问题没有回复：用户在回答生成前又发了新消息。"
                    + "优先回应用户当前这条消息；旧话题等用户再提起。）");

    private final WebClient llmWebClient;
    private final MessageRepository messageRepo;
    private final UserRepository userRepository;
    private final SseEmitterService sseService;
    private final StrategyService strategyService;
    private final MemoryService memoryService;
    private final UserLlmConfigService userLlmConfigService;
    private final ThreadPoolTaskExecutor taskExecutor;
    private final ObjectMapper objectMapper;

    /**
     * 用户连发消息时的中断簿记（见 {@link #submitChat}）：
     *  - inFlightRounds：每用户当前在途的轮次任务，新一轮进来先 cancel(true) 旧的；
     *  - currentRounds：每用户最新已受理的轮次号，落库/推送前必须校验（"轮次门"）。
     *
     * 两者都不在任务结束时清理：条目数以用户数为上界，且对已完成的 Future
     * cancel 是 no-op。currentRounds 是包可见 —— 单测直接拨轮次来钉住门的行为。
     */
    final ConcurrentHashMap<Long, Future<?>> inFlightRounds = new ConcurrentHashMap<>();
    final ConcurrentHashMap<Long, AtomicLong> currentRounds = new ConcurrentHashMap<>();

    public ChatService(WebClient llmWebClient, MessageRepository messageRepo,
                       UserRepository userRepository, SseEmitterService sseService,
                       StrategyService strategyService, MemoryService memoryService,
                       UserLlmConfigService userLlmConfigService,
                       ThreadPoolTaskExecutor taskExecutor, ObjectMapper objectMapper) {
        this.llmWebClient = llmWebClient;
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.strategyService = strategyService;
        this.memoryService = memoryService;
        this.userLlmConfigService = userLlmConfigService;
        this.taskExecutor = taskExecutor;
        this.objectMapper = objectMapper;
    }

    /**
     * 聊天入口（用户消息已由 MessageService 落库后调用）。
     *
     * <p>用户连发时采用**中断旧请求**策略：新一轮先作废上一轮再跑 ——
     * 否则两条 LLM 往返并发跑：回复乱序落库、第二轮的上下文看不到第一轮的问答、
     * 且两个回复几乎同时完成，前端一次刷出两坨完整内容（2026-10-20 修复的正是这个）。
     *
     * <p>作废分两层：
     * 1. cancel(true) 中断在途线程 —— WebClient 的 block() 响应线程中断时会
     *    取消订阅并断开到 Python 的 HTTP 连接；
     * 2. 轮次门 —— 所有副作用（落库/SSE/账本/策略）前校验轮次号，迟到的旧轮次
     *    整体丢弃。线程中断只是加速，门才是正确性保证：取消信号与 saveBotReply
     *    并发竞速时，靠门拦住。
     */
    public void submitChat(Long userId, String username, String text) {
        submitChat(userId, username, text, null);
    }

    /** 带聚焦对象的聊天入口（agent 互通 P2）：focus 是"用户正围绕哪条新闻追问"。 */
    public void submitChat(Long userId, String username, String text,
                           ChatSendRequest.Focus focus) {
        // 先递增轮次（= 作废所有更早轮次）再取消旧任务：顺序反了的话，
        // 旧任务可能在"尚未被标记作废"的窗口里继续落库。
        long round = currentRounds.computeIfAbsent(userId, k -> new AtomicLong())
                .incrementAndGet();
        Future<?> stale = inFlightRounds.get(userId);
        if (stale != null) {
            stale.cancel(true);
        }
        Future<?> task = taskExecutor.submit(
                () -> runRound(userId, username, text, focus, round));
        inFlightRounds.put(userId, task);
    }

    /**
     * 跑一轮对话。LLM 单次调用最长 60 秒，超时兜底回复。
     * 包可见：单测直接同步调用（绕过线程池），epoch/round 语义见 {@link #submitChat}。
     */
    void runRound(Long userId, String username, String text,
                  ChatSendRequest.Focus focus, long round) {
        // 本轮的会话键在入口处定死，全程复用：LLM 一来一回可能跨过午夜，
        // 逐次重算 currentSessionKey 会把用户消息记在昨天、回复记在今天，
        // 那段对话在巩固时就被从中间劈开（两边各缺一半）。
        String sessionKey = memoryService.currentSessionKey(userId);
        try {
            // 用户跨天（= 新会话）时，把上一段对话补成"前情提要"。
            // 异步执行且失败无害（内部 try/catch），不会拖慢这条消息的回复。
            memoryService.ensurePreviousSessionConsolidated(userId, sessionKey);
            // 会话内也要有节奏地巩固：否则"我改主意了，止损改成 5%"要等第二天才被记住
            memoryService.maybeConsolidateCurrentSession(userId, sessionKey);

            // 流式通道：delta 边到边推给前端，result 形状与非流式一致（含 epoch 中断契约：
            // 仍在跑的旧轮次在 Python 侧的下一个步骤边界发现轮次落后即自行退出）。
            JsonNode resp = callLlmStream(username, userId, sessionKey, text, focus, round);

            JsonNode replies = resp != null ? resp.get("replies") : null;
            if (replies == null || !replies.isArray() || replies.isEmpty()) {
                saveBotReply(userId, sessionKey, round, "抱歉，我暂时没有理解你的问题，换个问法试试？");
                return;
            }
            for (JsonNode reply : replies) {
                String content = reply.asText();
                if (content != null && !content.isBlank()) {
                    if (!saveBotReply(userId, sessionKey, round, content)) {
                        pushStreamAbort(userId, round); // 已被新一轮取代：撤掉流式气泡，剩余回复全部丢弃
                        return;
                    }
                }
            }

            JsonNode strategyJson = resp != null ? resp.get("strategy_json") : null;
            if (strategyJson != null && strategyJson.isObject()) {
                createAndBacktestFromAgent(userId, sessionKey, username, strategyJson, round);
            }
        } catch (Exception e) {
            if (isSuperseded(userId, round)) {
                // 被新消息中断（cancel(true) 打断流式 blockLast 从这里抛出）：
                // 静默退出。绝不能落"智能助手暂时不可用"——用户已经在等新问题的答案，
                // 一条迟到的错误回复只会制造"两条问题加一条假报错"的混乱。
                log.info("聊天轮次已被新消息取代，丢弃 userId={} round={}", userId, round);
                pushStreamAbort(userId, round); // 撤掉前端正在流式输出的半截气泡
                return;
            }
            log.error("LLM 调用失败 userId={}: {}", userId, e.getMessage());
            saveBotReply(userId, sessionKey, round, "智能助手暂时不可用，请稍后再试。");
        }
    }

    /** 轮次门：本轮是否已被更新的一轮取代（未登记 = 直调/单测路径，视为未取代）。 */
    private boolean isSuperseded(Long userId, long round) {
        AtomicLong current = currentRounds.get(userId);
        return current != null && current.get() != round;
    }

    /** 聚焦对象是否是有效的 news_event（type + eventId 齐备才透传给 Python）。 */
    static boolean isNewsFocus(ChatSendRequest.Focus focus) {
        return focus != null && "news_event".equals(focus.type()) && focus.eventId() != null;
    }

    /** 聚焦对象是否是有效的 memory_lesson（反问功能：type + lessonId 齐备才透传）。 */
    static boolean isLessonFocus(ChatSendRequest.Focus focus) {
        return focus != null && "memory_lesson".equals(focus.type()) && focus.lessonId() != null;
    }

    /** 调用 agent 聊天接口（非流式）；独立成包可见方法，方便单测替换远程依赖。 */
    JsonNode callLlm(String username, Long userId, String sessionKey, String text, Long epoch) {
        return callLlm(username, userId, sessionKey, text, null, epoch);
    }

    JsonNode callLlm(String username, Long userId, String sessionKey, String text,
                     ChatSendRequest.Focus focus, Long epoch) {
        Map<String, Object> body = buildChatBody(username, userId, sessionKey, text, focus, epoch);
        return llmWebClient.post()
                .uri("/api/v1/agent/chat")
                .bodyValue(body)
                .retrieve()
                .bodyToMono(JsonNode.class)
                .timeout(Duration.ofSeconds(60))
                .block();
    }

    /**
     * 流式版（2026-10-20 前端流式输出）：消费 Python /agent/chat/stream 的 SSE 事件 ——
     * delta 事件边到边转给前端（CHAT_BOT_DELTA，打字机效果），result 事件作为返回值，
     * 形状与非流式版完全一致，runRound 的后续逻辑不用分叉。
     *
     * <p>失败语义与 Python 侧对齐：**一个增量都没收到**就失败（如流式端点暂不可用的
     * 部署组合）→ 退回非流式 callLlm 重来一次；已经转发过增量后失败 → 上抛，
     * 由 runRound 的既有兜底处理（重试会让用户看到同一段话重复两遍）。
     * 轮次被取代（取消信号打断 blockLast）→ 异常上抛，runRound 静默丢弃。
     */
    JsonNode callLlmStream(String username, Long userId, String sessionKey, String text,
                           ChatSendRequest.Focus focus, long round) {
        Map<String, Object> body = buildChatBody(username, userId, sessionKey, text, focus, round);
        String streamId = "stream-" + round;
        AtomicReference<JsonNode> result = new AtomicReference<>();
        AtomicBoolean anyDelta = new AtomicBoolean(false);
        try {
            llmWebClient.post()
                    .uri("/api/v1/agent/chat/stream")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToFlux(new ParameterizedTypeReference<ServerSentEvent<String>>() {})
                    // 事件间隔上限（不是总时长）：工具执行/模型思考的间隙要留足，
                    // 卡死（无任何事件）超过它才掐线走兜底
                    .timeout(Duration.ofSeconds(120))
                    .doOnNext(event -> handleStreamEvent(event.data(), userId, round, streamId, result, anyDelta))
                    .blockLast();
        } catch (Exception e) {
            if (!anyDelta.get() && !isSuperseded(userId, round)) {
                log.warn("流式调用失败且尚未输出任何增量，退回非流式: {}", e.getMessage());
                return callLlm(username, userId, sessionKey, text, focus, round);
            }
            throw e;
        }
        return result.get();
    }

    /** 处理一个流式事件：delta 转发前端（轮次门后不再转发），result 暂存为返回值。包可见供单测直调。 */
    void handleStreamEvent(String payload, Long userId, long round, String streamId,
                           AtomicReference<JsonNode> result, AtomicBoolean anyDelta) {
        if (payload == null || payload.isBlank()) {
            return;
        }
        try {
            JsonNode evt = objectMapper.readTree(payload);
            String type = evt.path("type").asText("");
            if ("delta".equals(type)) {
                String text = evt.path("text").asText("");
                if (!text.isEmpty()) {
                    anyDelta.set(true);
                    if (!isSuperseded(userId, round)) {
                        pushStreamEvent(userId, Map.of(
                                "type", "CHAT_BOT_DELTA", "streamId", streamId, "content", text));
                    }
                }
            } else if ("result".equals(type)) {
                result.set(evt.path("result"));
            }
        } catch (Exception e) {
            log.warn("流式事件解析失败: {}", e.getMessage());
        }
    }

    /** 轮次被新消息取代时通知前端撤掉正在流式输出的气泡（前端不认识的流 id 自动忽略）。 */
    private void pushStreamAbort(Long userId, long round) {
        pushStreamEvent(userId, Map.of("type", "CHAT_BOT_ABORT", "streamId", "stream-" + round));
    }

    private void pushStreamEvent(Long userId, Map<String, String> event) {
        try {
            sseService.pushRaw(userId, objectMapper.writeValueAsString(event));
        } catch (Exception e) {
            log.warn("流式事件推送失败: {}", e.getMessage());
        }
    }

    /** 组装发给 agent 的请求体（非流式 / 流式共用）。 */
    private Map<String, Object> buildChatBody(String username, Long userId, String sessionKey,
                                              String text, ChatSendRequest.Focus focus, Long epoch) {
        Map<String, Object> body = new HashMap<>();
        // 字段名必须是 user_id：Python 侧 app.py 的 agent_chat 读的是 data["user_id"]，
        // 取不到就直接 return {"replies": []}（连 LLM 都不会调用），
        // 于是 Java 收到空数组、用"没理解你的问题"兜底 —— 整条机器人链路看起来像坏了。
        // 这里原来是 user_name，与 Python 不一致，是长期存在的静默故障。
        // 契约由 python-data-service/tests/test_agent_chat_contract.py 钉住，改字段名前先看它。
        body.put("user_id", username);
        body.put("message", text);
        body.put("session_id", sessionKey);
        // 记忆账本用数字 userId 定位（user_id 字段是用户名，语义不同，必须分开传）
        body.put("memory_user_id", userId);
        // 会话历史：不带它的话，Python 侧每条消息都是一次全新、失忆的 agent，
        // "它呢？""把止损改成 5%" 这类追问必然失效（run_agent 一度根本不接 history）。
        body.put("history", buildHistory(userId, text));
        // 聚焦对象（news_event / memory_lesson）：有效才带，普通聊天不带这个键（Python 侧同判）
        if (isNewsFocus(focus)) {
            // 打点（2026-10-10 事故排查）："问 AI 没收到新闻"要么停在这一行之前
            // （前端没送/反序列化失败），要么停在 Python 的知识端点 —— 两端都有日志后，
            // 这类静默失效当场可归因，不用再靠猜。
            log.info("聚焦对话透传：news_event id={}（随消息发给 agent）", focus.eventId());
            Map<String, Object> focusBody = new HashMap<>();
            focusBody.put("type", focus.type());
            focusBody.put("event_id", focus.eventId());
            body.put("focus", focusBody);
        } else if (isLessonFocus(focus)) {
            // 反问功能（2026-10-16）：用户在记忆页对一条经验发起质疑对话
            log.info("聚焦对话透传：memory_lesson id={}（随消息发给 agent）", focus.lessonId());
            Map<String, Object> focusBody = new HashMap<>();
            focusBody.put("type", focus.type());
            focusBody.put("lesson_id", focus.lessonId());
            body.put("focus", focusBody);
        }
        // 轮次号（用户连发时中断旧请求的契约）：Python 侧登记"该用户最新已受理
        // 的轮次"，仍在跑的旧轮次在下一个步骤边界发现落后即自行退出，
        // 省下剩余的模型调用。不传（null）= 旧客户端，Python 侧不启用中断。
        if (epoch != null) {
            body.put("epoch", epoch);
        }
        // BYOK（2026-10-25）：用户自己的 LLM 供应商（未配置 = 空数组）。
        // 这个字段必须**永远带上**（哪怕空数组）：它是 Python 侧区分"用户级调用"
        // 的唯一标记——不带就会静默烧到部署者的全局 key 上（契约见
        // python-data-service/llm_service.py 的 user_providers）。
        java.util.List<Map<String, Object>> providers = userLlmConfigService.providersFor(userId);
        body.put("llm_providers", providers != null ? providers : List.of());
        return body;
    }

    /**
     * 组装发给 agent 的会话历史（最近的对话，按时间升序，role 用 OpenAI 风格）。
     *
     * 三个容易踩的点：
     * 1. {@code handleChatSend} 会**先把当前这条 CHAT_USER 落库**，再异步调用本方法，
     *    所以查询结果的第一条就是当前消息本身 —— 必须去掉，否则模型会在上下文里
     *    看到同一句话出现两次，进而重复生成策略。
     * 2. 只取最近的 MAX_HISTORY_MESSAGES 条：历史全量回传会让请求体随对话无限增长。
     * 3. 走 findTop20... 而不是全量查询：聊天记录会随使用无限增长，
     *    每条消息都全量读一遍既慢又没必要。
     */
    List<Map<String, String>> buildHistory(Long userId, String currentText) {
        if (userId == null) {
            return List.of();
        }
        List<Message> recent = messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(userId, CHAT_TYPES);
        if (recent == null || recent.isEmpty()) {
            return List.of();
        }

        // 倒序取回 → 反转成时间升序，并剔除刚落库的当前消息
        List<Message> ordered = new ArrayList<>(recent);
        Collections.reverse(ordered);
        Message last = ordered.get(ordered.size() - 1);
        if ("CHAT_USER".equals(last.getType()) && Objects.equals(last.getContent(), currentText)) {
            ordered.remove(ordered.size() - 1);
        }

        int start = Math.max(0, ordered.size() - MAX_HISTORY_MESSAGES);
        List<Map<String, String>> history = new ArrayList<>();
        for (Message message : ordered.subList(start, ordered.size())) {
            String content = message.getContent();
            if (content == null || content.isBlank()) {
                continue;
            }
            String role = "CHAT_BOT".equals(message.getType()) ? "assistant" : "user";
            // 悬空轮标记（中部）：连续两条 user 之间没有 assistant（用户连发、旧轮回复
            // 被中断丢弃才会出现这种形状）。不标记的话，模型会把历史里那条没被回答的
            // 问题当成"欠着的账"抢着去答，用户新消息反而被晾在一边（2026-10-20 线上实测）。
            if ("user".equals(role) && !history.isEmpty()
                    && "user".equals(history.get(history.size() - 1).get("role"))) {
                history.add(DANGLING_TURN_PLACEHOLDER);
            }
            history.add(Map.of("role", role, "content", content));
        }
        // 悬空轮标记（尾部）：最常见的情况恰恰在这里——当前消息在 Python 侧是单独
        // 拼在历史之后的（buildHistory 已把它剔除），历史最后一条悬空 user 与当前
        // user 相邻的关系只有这里能看到。
        if (!history.isEmpty() && "user".equals(history.get(history.size() - 1).get("role"))) {
            history.add(DANGLING_TURN_PLACEHOLDER);
        }
        return history;
    }

    /**
     * 保存并推送一条助手回复。返回 false = 本轮已被更新的轮次取代，没有落库。
     * 轮次门在**最前面**：取消信号与落库竞速时（Python 刚返回、保存正走在路上），
     * 靠这道门拦住 —— 落库、SSE 推送、账本三件事必须同进同退，不能只拦其中一件。
     */
    private boolean saveBotReply(Long userId, String sessionKey, long round, String content) {
        return saveBotReply(userId, sessionKey, round, content, null, null);
    }

    private boolean saveBotReply(Long userId, String sessionKey, long round, String content,
                                 String relatedSymbol, String metadata) {
        if (isSuperseded(userId, round)) {
            log.info("丢弃已被新消息取代的聊天回复 userId={} round={}", userId, round);
            return false;
        }
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            log.warn("推送回复失败：用户 {} 不存在", userId);
            return false;
        }
        Message message = Message.builder()
                .user(user)
                .type("CHAT_BOT")
                .content(content)
                .relatedSymbol(relatedSymbol)
                .metadata(metadata)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(userId, MessageResponse.from(message));
        // 记忆账本：助手回复也入账，否则前情提要只有一半（用户问了什么、但没有回答过什么）
        appendToLedger(userId, sessionKey, "chat_bot", "assistant", content, relatedSymbol, metadata);
        log.info("Bot replied to user {}: {}", userId, content);
        return true;
    }

    /**
     * 写记忆账本。只追加，且失败绝不影响主流程 ——
     * 记忆是增强功能，不能因为它挂了就让用户收不到回复。
     * sessionKey 必须由调用方传入本轮开始时定下的那个（见 processAsync），不在这里重算。
     */
    private void appendToLedger(Long userId, String sessionKey, String kind, String role, String content,
                                String symbol, String metadata) {
        try {
            memoryService.append(MemoryEvent.builder()
                    .userId(userId)
                    .sessionKey(sessionKey)
                    .kind(kind)
                    .role(role)
                    .content(content)
                    .symbol(symbol)
                    .meta(metadata)
                    .provenance("model")
                    .trust("medium")
                    .build());
        } catch (Exception e) {
            log.warn("记忆账本写入失败 user={} kind={}: {}", userId, kind, e.getMessage());
        }
    }

    private void createAndBacktestFromAgent(Long userId, String sessionKey, String username,
                                            JsonNode strategyJson, long round) {
        try {
            // 轮次门：别替已被取代的轮次创建策略（创建是外部可见的副作用）
            if (isSuperseded(userId, round)) {
                return;
            }
            StrategyResponse strategy = strategyService.createFromAgent(username, strategyJson);
            String metadata = "{\"strategyId\":" + strategy.getId() + "}";
            // 策略生成是"agent 做过的事"，必须进账本：
            // 否则下次用户说"把上次那个策略的止损改一下"，前情提要里根本没有这段。
            appendToLedger(userId, sessionKey, "strategy", "assistant",
                    "生成策略「" + strategy.getName() + "」（" + strategy.getSymbol() + "）",
                    strategy.getSymbol(), metadata);
            // 回测是重活，轮次已死就别跑了；策略本身已创建（真实存在），账本如实记录
            if (isSuperseded(userId, round)) {
                return;
            }
            try {
                JsonNode backtestResult = strategyService.runBacktest(username, strategy.getId());
                saveBotReply(userId, sessionKey, round, buildBacktestSummary(strategy, backtestResult),
                        strategy.getSymbol(), metadata);
            } catch (Exception backtestException) {
                log.warn("Agent strategy auto backtest failed id={}: {}", strategy.getId(), backtestException.getMessage());
                saveBotReply(userId, sessionKey, round,
                        "策略「" + strategy.getName() + "」已保存，但自动回测暂时失败，请到策略详情重试。",
                        strategy.getSymbol(), metadata);
            }
        } catch (Exception createException) {
            log.error("Agent strategy save failed userId={}: {}", userId, createException.getMessage());
            // 校验拒绝（IllegalArgumentException）带着人话原因（如"stop_loss_pct 必须为负数"）：
            // 原样透传给用户，助手才有机会当场修正后重试，而不是笼统一句"稍后再试"。
            String reason = createException instanceof IllegalArgumentException
                    ? createException.getMessage() : null;
            saveBotReply(userId, sessionKey, round, reason != null
                    ? "策略未保存：" + reason + "。可以让助手修正后重试。"
                    : "策略已生成，但保存失败，请稍后再试。");
        }
    }

    private String buildBacktestSummary(StrategyResponse strategy, JsonNode result) {
        JsonNode backtest = result == null ? null : result.get("backtest");
        if (backtest == null || backtest.isNull()) {
            return "策略「" + strategy.getName() + "」已保存；当前暂无可用回测结果，请稍后重试。";
        }

        StringBuilder sb = new StringBuilder();
        sb.append("策略「").append(strategy.getName()).append("」已自动回测：\n");
        sb.append("- 总收益：").append(doubleOr(backtest.get("total_return_pct"), 0)).append("%\n");
        sb.append("- 买入持有：").append(doubleOr(backtest.get("buy_and_hold_return_pct"), 0)).append("%\n");
        sb.append("- 超额收益：").append(doubleOr(backtest.get("excess_return_pct"), 0)).append("%\n");
        sb.append("- 最大回撤：").append(doubleOr(backtest.get("max_drawdown_pct"), 0)).append("%\n");
        sb.append("- 夏普比率：").append(doubleOr(backtest.get("sharpe_ratio"), 0)).append("\n");
        sb.append("- 胜率：").append(doubleOr(backtest.get("win_rate"), 0)).append("%\n");
        sb.append("- 交易笔数：").append((int) doubleOr(backtest.get("trade_count"), 0));
        sb.append("\n可在策略详情查看权益曲线和交易记录。");
        return sb.toString();
    }

    private double doubleOr(JsonNode node, double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return node.asDouble();
    }
}
