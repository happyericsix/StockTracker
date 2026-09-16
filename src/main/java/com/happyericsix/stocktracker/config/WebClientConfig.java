package com.happyericsix.stocktracker.config;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.function.client.WebClient;

/**
 * WebClient 配置，为 AkshareStockClient 提供基础 Builder。
 */
@Configuration
public class WebClientConfig {

    /** 站内聊天机器人：调用 Python LLM/Agent 服务（/api/v1/agent/chat 等）。
     *  base URL 与行情客户端同源（akshare.api.base-url），docker 下由
     *  AKSHARE_API_BASE_URL=http://python-data:8000 覆盖，避免写死 localhost 导致容器内失效。 */
    @Bean
    public WebClient llmWebClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                                  @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder().baseUrl(baseUrl);
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        return builder.build();
    }
}
