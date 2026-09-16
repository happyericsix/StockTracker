package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;

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

    private final WebClient llmWebClient;
    private final MessageRepository messageRepo;
    private final UserRepository userRepository;
    private final SseEmitterService sseService;
    private final StrategyService strategyService;
    private final MemoryService memoryService;

    public ChatService(WebClient llmWebClient, MessageRepository messageRepo,
                       UserRepository userRepository, SseEmitterService sseService,
                       StrategyService strategyService, MemoryService memoryService) {
        this.llmWebClient = llmWebClient;
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.strategyService = strategyService;
        this.memoryService = memoryService;
    }

    /** LLM 单次调用最长 60 秒，超时兜底回复 */
    @Async
    public void processAsync(Long userId, String username, String text) {
        try {
            String sessionKey = memoryService.currentSessionKey(userId);
            // 用户跨天（= 新会话）时，把上一段对话补成"前情提要"。
            // 异步执行且失败无害（内部 try/catch），不会拖慢这条消息的回复。
            memoryService.ensurePreviousSessionConsolidated(userId, sessionKey);
            // 会话内也要有节奏地巩固：否则"我改主意了，止损改成 5%"要等第二天才被记住
            memoryService.maybeConsolidateCurrentSession(userId, sessionKey);

            JsonNode resp = callLlm(username, userId, sessionKey, text);

            JsonNode replies = resp != null ? resp.get("replies") : null;
            if (replies == null || !replies.isArray() || replies.isEmpty()) {
                saveBotReply(userId, "抱歉，我暂时没有理解你的问题，换个问法试试？");
                return;
            }
            for (JsonNode reply : replies) {
                String content = reply.asText();
                if (content != null && !content.isBlank()) {
                    saveBotReply(userId, content);
                }
            }

            JsonNode strategyJson = resp != null ? resp.get("strategy_json") : null;
            if (strategyJson != null && strategyJson.isObject()) {
                createAndBacktestFromAgent(userId, username, strategyJson);
            }
        } catch (Exception e) {
            log.error("LLM 调用失败 userId={}: {}", userId, e.getMessage());
            saveBotReply(userId, "智能助手暂时不可用，请稍后再试。");
        }
    }

    /** 调用 agent 聊天接口；独立成包可见方法，方便单测替换远程依赖。 */
    JsonNode callLlm(String username, Long userId, String sessionKey, String text) {
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

        return llmWebClient.post()
                .uri("/api/v1/agent/chat")
                .bodyValue(body)
                .retrieve()
                .bodyToMono(JsonNode.class)
                .timeout(Duration.ofSeconds(60))
                .block();
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
            history.add(Map.of(
                    "role", "CHAT_BOT".equals(message.getType()) ? "assistant" : "user",
                    "content", content));
        }
        return history;
    }

    private void saveBotReply(Long userId, String content) {
        saveBotReply(userId, content, null, null);
    }

    private void saveBotReply(Long userId, String content, String relatedSymbol, String metadata) {
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            log.warn("推送回复失败：用户 {} 不存在", userId);
            return;
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
        appendToLedger(userId, "chat_bot", "assistant", content, relatedSymbol, metadata);
        log.info("Bot replied to user {}: {}", userId, content);
    }

    /**
     * 写记忆账本。只追加，且失败绝不影响主流程 ——
     * 记忆是增强功能，不能因为它挂了就让用户收不到回复。
     */
    private void appendToLedger(Long userId, String kind, String role, String content,
                                String symbol, String metadata) {
        try {
            memoryService.append(MemoryEvent.builder()
                    .userId(userId)
                    .sessionKey(memoryService.currentSessionKey(userId))
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

    private void createAndBacktestFromAgent(Long userId, String username, JsonNode strategyJson) {
        try {
            StrategyResponse strategy = strategyService.createFromAgent(username, strategyJson);
            String metadata = "{\"strategyId\":" + strategy.getId() + "}";
            // 策略生成是"agent 做过的事"，必须进账本：
            // 否则下次用户说"把上次那个策略的止损改一下"，前情提要里根本没有这段。
            appendToLedger(userId, "strategy", "assistant",
                    "生成策略「" + strategy.getName() + "」（" + strategy.getSymbol() + "）",
                    strategy.getSymbol(), metadata);
            try {
                JsonNode backtestResult = strategyService.runBacktest(username, strategy.getId());
                saveBotReply(userId, buildBacktestSummary(strategy, backtestResult),
                        strategy.getSymbol(), metadata);
            } catch (Exception backtestException) {
                log.warn("Agent strategy auto backtest failed id={}: {}", strategy.getId(), backtestException.getMessage());
                saveBotReply(userId,
                        "📄 策略「" + strategy.getName() + "」已保存，但自动回测暂时失败，请到策略详情重试。",
                        strategy.getSymbol(), metadata);
            }
        } catch (Exception createException) {
            log.error("Agent strategy save failed userId={}: {}", userId, createException.getMessage());
            saveBotReply(userId, "⚠️ 策略已生成，但保存失败，请稍后再试。");
        }
    }

    private String buildBacktestSummary(StrategyResponse strategy, JsonNode result) {
        JsonNode backtest = result == null ? null : result.get("backtest");
        if (backtest == null || backtest.isNull()) {
            return "📄 策略「" + strategy.getName() + "」已保存；当前暂无可用回测结果，请稍后重试。";
        }

        StringBuilder sb = new StringBuilder();
        sb.append("📈 策略「").append(strategy.getName()).append("」已自动回测：\n");
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
