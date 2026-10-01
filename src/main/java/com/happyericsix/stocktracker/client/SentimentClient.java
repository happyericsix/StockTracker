package com.happyericsix.stocktracker.client;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;

/**
 * Python 散户情绪服务（sentiment_client）的客户端。
 *
 * <p>异常范式与 {@link NewsClient} 相同（降级不抛异常）：情绪是**旁路增强**，
 * 它挂了不该拖垮任何主链路。Python 侧已经把失败表达成数据
 * （{@code {"ok":false,"error":…}}），这里只负责翻译超时/网络层异常。
 */
@Service
public class SentimentClient {

    private static final Logger log = LoggerFactory.getLogger(SentimentClient.class);

    /** 情绪端点背后是 4 次上游请求（三条指数 + 日线），给 20s。 */
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(20);

    private final WebClient webClient;

    public SentimentClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                           @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8");
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
    }

    /** 拉一个个股的情绪聚合（含 Python 侧 30 分钟缓存）。失败返回 null，不抛。 */
    public JsonNode getSentiment(String symbol) {
        try {
            return webClient.get()
                    .uri("/api/v1/sentiment/{symbol}", symbol)
                    .retrieve()
                    .bodyToMono(JsonNode.class)
                    .block(REQUEST_TIMEOUT);
        } catch (Exception e) {
            log.warn("散户情绪取数失败 {}: {}", symbol, e.getMessage());
            return null;
        }
    }
}
