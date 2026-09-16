package com.happyericsix.stocktracker.client;

import tools.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/**
 * Java → Python 的记忆相关调用。
 *
 * <p>方向说明：数据在 Java（MySQL），LLM 调用在 Python，所以"巩固"这件事是
 * Java 发现"该总结了" → 让 Python 去调模型 → Python 把摘要通过内部接口写回 Java。
 * Python 全程没有数据库凭据。
 *
 * <p>失败语义：<b>不抛异常</b>，只记日志返回 null。记忆是增强功能，
 * 巩固失败绝不能影响用户那条消息的回复（调用方还会再兜一层 try/catch）。
 */
@Service
public class MemoryClient {

    private static final Logger log = LoggerFactory.getLogger(MemoryClient.class);

    private final WebClient webClient;

    public MemoryClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                        @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder().baseUrl(baseUrl);
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
    }

    /** 让 Python 把某个会话总结成"前情提要"并写回 Java。总结是 LLM 调用，给足 60s。 */
    public JsonNode consolidate(Long userId, String sessionKey) {
        Map<String, Object> body = new HashMap<>();
        body.put("user_id", userId);
        body.put("session_key", sessionKey);
        try {
            return webClient.post()
                    .uri("/api/v1/memory/consolidate")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(JsonNode.class)
                    .timeout(Duration.ofSeconds(60))
                    .block();
        } catch (Exception e) {
            log.warn("记忆巩固调用失败 user={} session={}: {}", userId, sessionKey, e.getMessage());
            return null;
        }
    }
}
