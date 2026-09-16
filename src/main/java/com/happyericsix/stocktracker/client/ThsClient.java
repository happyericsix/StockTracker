package com.happyericsix.stocktracker.client;

import com.happyericsix.stocktracker.dto.ThsQrCreateApiResponse;
import com.happyericsix.stocktracker.dto.ThsQrPollApiResponse;
import com.happyericsix.stocktracker.dto.ThsSelfStocksResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Duration;
import java.util.List;
import java.util.Map;

@Service
public class ThsClient {
    private static final Logger log = LoggerFactory.getLogger(ThsClient.class);
    private final WebClient webClient;
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(15);

    /** 扫码轮询超时：Python 侧每次只在服务端等 3 秒，给 10 秒足够。 */
    private static final Duration POLL_TIMEOUT = Duration.ofSeconds(10);

    public ThsClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                     @Value("${internal.api-token:}") String internalToken) {
        WebClient.Builder builder = WebClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8");
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
        log.info("ThsClient initialized, base URL: {}, internal auth: {}",
                baseUrl, internalToken != null && !internalToken.isBlank());
    }

    public ThsQrCreateApiResponse createQr() {
        try {
            return webClient.get()
                    .uri("/api/v1/ths/qr/create")
                    .retrieve()
                    .bodyToMono(ThsQrCreateApiResponse.class)
                    .timeout(REQUEST_TIMEOUT)
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("ths qr create failed: HTTP {} {}", e.getStatusCode(), e.getResponseBodyAsString());
            throw new RuntimeException(extractThsError(e), e);
        } catch (Exception e) {
            log.warn("ths qr create exception: {}", e.getMessage());
            throw new RuntimeException("无法连接数据服务，请稍后重试", e);
        }
    }

    /**
     * 轮询扫码状态。前端每 4 秒调一次。
     *
     * <p>对应 Python {@code GET /api/v1/ths/qr/poll?qrSessionId=...}。
     *
     * <p>⚠️「手机还没扫」是<b>正常状态</b>（{@code ok=true, status=pending}），
     * 所以这里<b>不</b>把响应判成失败 —— 原样返回，由上层用
     * {@link ThsQrPollApiResponse#isPending()} 判断。只有网络/服务异常才抛。
     */
    public ThsQrPollApiResponse pollQr(String qrSessionId) {
        if (qrSessionId == null || qrSessionId.isBlank()) {
            throw new IllegalArgumentException("缺少 qrSessionId");
        }
        try {
            ThsQrPollApiResponse resp = webClient.get()
                    .uri(uriBuilder -> uriBuilder.path("/api/v1/ths/qr/poll")
                            .queryParam("qrSessionId", qrSessionId)
                            .build())
                    .retrieve()
                    .bodyToMono(ThsQrPollApiResponse.class)
                    .timeout(POLL_TIMEOUT)
                    .block();
            if (resp == null) {
                throw new RuntimeException("扫码状态查询失败：数据服务无响应");
            }
            return resp;
        } catch (WebClientResponseException e) {
            // 404 = 会话已过期；其它 = 真错误。两种都带上 Python 的中文提示。
            log.warn("ths qr poll failed: HTTP {} {}", e.getStatusCode(), e.getResponseBodyAsString());
            throw new RuntimeException(extractThsError(e), e);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            log.warn("ths qr poll exception: {}", e.getMessage());
            throw new RuntimeException("无法连接数据服务，请稍后重试", e);
        }
    }

    /**
     * 读取同花顺「我的自选」列表。
     *
     * <p>对应 Python {@code POST /api/v1/ths/selfstocks}。
     *
     * <p>⚠️ 必须用 <b>POST + body</b> 传凭证。Python 侧实测：用 GET query 参数
     * 会让账号密码明文出现在 uvicorn 访问日志里（违反设计文档 §10）。
     *
     * <p>失败时抛异常 —— 由 {@code ThsSyncService} 捕获后记进
     * {@code ths_bindings.lastError}，不在这里降级成空列表，
     * 否则「凭证失效」和「自选股本来就是空的」两种情况就分不清了。
     */
    public List<ThsSelfStocksResponse.Stock> selfStocks(String account, String password) {
        try {
            ThsSelfStocksResponse resp = webClient.post()
                    .uri("/api/v1/ths/selfstocks")
                    .bodyValue(Map.of("account", account, "password", password))
                    .retrieve()
                    .bodyToMono(ThsSelfStocksResponse.class)
                    .timeout(REQUEST_TIMEOUT)
                    .block();

            if (resp == null) {
                throw new RuntimeException("读取自选股失败：数据服务无响应");
            }
            if (!resp.ok()) {
                throw new RuntimeException(resp.error() == null ? "读取自选股失败" : resp.error());
            }
            return resp.data() == null ? List.of() : resp.data();
        } catch (WebClientResponseException e) {
            log.warn("ths selfstocks failed: HTTP {} {}", e.getStatusCode(), e.getResponseBodyAsString());
            throw new RuntimeException(extractThsError(e), e);
        } catch (RuntimeException e) {
            throw e;
        } catch (Exception e) {
            log.warn("ths selfstocks exception: {}", e.getMessage());
            throw new RuntimeException("无法连接数据服务，请稍后重试", e);
        }
    }

    @SuppressWarnings("unchecked")
    private String extractThsError(WebClientResponseException e) {
        try {
            Map<String, Object> body = (Map<String, Object>) e.getResponseBodyAs(Map.class);
            if (body != null) {
                Object err = body.get("error");
                if (err != null && !err.toString().isBlank()) {
                    return err.toString();
                }
            }
        } catch (Exception ignore) {
            // 响应体不是 JSON（Python 服务挂了返回 HTML 错误页）→ 用下面的通用提示
        }
        // 401 是服务间鉴权失败，最容易被误判成"同花顺挂了"，所以单独点明
        if (e.getStatusCode().value() == 401) {
            return "内部服务鉴权失败：请检查 internal.api-token 与 Python 侧 INTERNAL_API_TOKEN 是否一致";
        }
        return "同花顺服务暂时不可用，请稍后重试";
    }
}