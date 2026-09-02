package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/**
 * 聊天机器人服务：异步调用 Python LLM（/api/v1/agent/chat），
 * 回复直接落库 + SSE 推送。不依赖 MessageService，避免循环依赖。
 */
@Service
public class ChatService {

    private static final Logger log = LoggerFactory.getLogger(ChatService.class);

    private final WebClient llmWebClient;
    private final MessageRepository messageRepo;
    private final UserRepository userRepository;
    private final SseEmitterService sseService;
    private final StrategyService strategyService;

    public ChatService(WebClient llmWebClient, MessageRepository messageRepo,
                       UserRepository userRepository, SseEmitterService sseService,
                       StrategyService strategyService) {
        this.llmWebClient = llmWebClient;
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.strategyService = strategyService;
    }

    /** LLM 单次调用最长 60 秒，超时兜底回复 */
    @Async
    public void processAsync(Long userId, String username, String text) {
        try {
            JsonNode resp = callLlm(username, text);

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
                strategyService.createFromAgent(username, strategyJson);
            }
        } catch (Exception e) {
            log.error("LLM 调用失败 userId={}: {}", userId, e.getMessage());
            saveBotReply(userId, "智能助手暂时不可用，请稍后再试。");
        }
    }

    /** 调用 agent 聊天接口；独立成包可见方法，方便单测替换远程依赖。 */
    JsonNode callLlm(String username, String text) {
        Map<String, String> body = new HashMap<>();
        body.put("user_id", username);
        body.put("message", text);

        return llmWebClient.post()
                .uri("/api/v1/agent/chat")
                .bodyValue(body)
                .retrieve()
                .bodyToMono(JsonNode.class)
                .timeout(Duration.ofSeconds(60))
                .block();
    }

    private void saveBotReply(Long userId, String content) {
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            log.warn("推送回复失败：用户 {} 不存在", userId);
            return;
        }
        Message message = Message.builder()
                .user(user)
                .type("CHAT_BOT")
                .content(content)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(userId, MessageResponse.from(message));
        log.info("Bot replied to user {}: {}", userId, content);
    }
}
