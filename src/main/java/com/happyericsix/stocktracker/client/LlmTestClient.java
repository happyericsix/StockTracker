package com.happyericsix.stocktracker.client;

import tools.jackson.databind.JsonNode;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Java → Python 的 LLM 连通性探测（BYOK 配套，2026-10-25）。
 *
 * <p>对应 Python {@code POST /api/v1/llm/test}：逐条探测供应商（每家 15s 超时、
 * max_tokens=8），返回 {@code {"results": [{name, ok, latency_ms, model, error?}]}}。
 *
 * <p>⚠️ 请求体里带用户 Key 明文：<b>绝不能进日志</b>（这里只记异常类型与消息，
 * WebClient 默认也不打请求体），且只在 INTERNAL_API_TOKEN 保护的内网通道上传输。
 */
@Service
public class LlmTestClient {

    private static final Logger log = LoggerFactory.getLogger(LlmTestClient.class);

    private final WebClient webClient;

    public LlmTestClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                         @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder().baseUrl(baseUrl);
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
    }

    /**
     * 探测一批供应商。providers 形如 [{name, base_url, api_key, model, chat_path?}]。
     * 失败不抛异常：探测的目的就是把"每一家各自通不通"带回去给用户看。
     */
    public JsonNode test(Long scopeUserId, List<Map<String, Object>> providers) {
        Map<String, Object> body = new HashMap<>();
        body.put("scope", scopeUserId);
        body.put("providers", providers);
        try {
            return webClient.post()
                    .uri("/api/v1/llm/test")
                    .bodyValue(body)
                    .retrieve()
                    .bodyToMono(JsonNode.class)
                    // Python 侧每家 15s、最多主备两家串行 → 40s 上限
                    .timeout(Duration.ofSeconds(40))
                    .block();
        } catch (Exception e) {
            log.warn("LLM 连通性探测调用失败 user={}: {}", scopeUserId, e.getMessage());
            return null;
        }
    }
}
