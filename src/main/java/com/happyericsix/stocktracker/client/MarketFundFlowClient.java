package com.happyericsix.stocktracker.client;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import tools.jackson.databind.JsonNode;

import java.time.Duration;
import java.time.Instant;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 大盘资金流（Python /api/v1/market/fund-flow）的客户端，照 {@code SentimentClient} 的
 * 同一款降级范式：吞异常返回 null，控制器据此回「暂不可用」而不是 500——
 * 资金卡是增强视图，缺席不是故障。
 *
 * <p><b>本地短缓存（2026-10-19）</b>：Python 侧虽有 10 分钟 TTL，但过期后的第一个
 * 请求要同步等东财上游（且失败结果在旧实现里不缓存，等于每次打开 Dashboard
 * 都冷启动）。这里加一层 90 秒本地缓存把页面打开的等待消掉；Python 侧重写后
 * 负责 stale-while-revalidate，两层各自独立兜底。取数失败/ok=false 时，10 分钟内
 * 的旧值照回（日频数据，分钟级陈旧无害）——回旧值比让资金卡消失更符合
 * "缺席不是故障"的设计意图。
 */
@Service
public class MarketFundFlowClient {

    private static final Logger log = LoggerFactory.getLogger(MarketFundFlowClient.class);
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(15);
    /** 本地缓存命中窗口：页面打开即刻出图；数据新鲜度由 Python 侧 TTL 保证 */
    private static final Duration LOCAL_TTL = Duration.ofSeconds(90);
    /** 上游不可用时旧值的最大回退窗口（与 Python 侧旧值上限同量级） */
    private static final Duration STALE_MAX = Duration.ofMinutes(10);

    /** 缓存的响应体只读透传给控制器序列化，不会被改动，共享同一实例是安全的 */
    private record CacheEntry(JsonNode body, Instant at) {}

    private final WebClient webClient;
    private final Map<Integer, CacheEntry> localCache = new ConcurrentHashMap<>();

    public MarketFundFlowClient(
            @Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
            @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8");
        // 空头会让 Python 走"token 配了但不对"的 401 分支，比不带头更难排查
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
    }

    /** 大盘主力资金流日线（净额亿元，升序）。失败返回 null。 */
    public JsonNode getFundFlow(int limit) {
        int key = Math.max(1, Math.min(limit, 250));
        CacheEntry hit = localCache.get(key);
        if (hit != null && Duration.between(hit.at(), Instant.now()).compareTo(LOCAL_TTL) < 0) {
            return hit.body();
        }
        JsonNode data;
        try {
            data = webClient.get()
                    .uri(uri -> uri.path("/api/v1/market/fund-flow")
                            .queryParam("limit", key)
                            .build())
                    .retrieve()
                    .bodyToMono(JsonNode.class)
                    .block(REQUEST_TIMEOUT);
        } catch (Exception e) {
            log.warn("fund-flow 调用失败: {}", e.getMessage());
            return staleOr(key, hit, "调用失败");
        }
        if (data != null && data.path("ok").asBoolean(false)) {
            localCache.put(key, new CacheEntry(data, Instant.now()));
            return data;
        }
        // Python 回了 ok=false（它自己的上游失败）：不缓存这份"不可用"，
        // 陈旧上限内先回旧值保住资金卡
        return staleOr(key, hit, "上游 ok=false");
    }

    /** 上游不可用时的旧值回退：超过 STALE_MAX 就返回 null（控制器回 503）。 */
    private JsonNode staleOr(int key, CacheEntry hit, String reason) {
        if (hit == null) return null;
        if (Duration.between(hit.at(), Instant.now()).compareTo(STALE_MAX) >= 0) return null;
        log.info("fund-flow {}，回 {}s 前的旧值（key={}）", reason,
                Duration.between(hit.at(), Instant.now()).toSeconds(), key);
        return hit.body();
    }
}
