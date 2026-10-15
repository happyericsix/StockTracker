package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PushLog;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PushLogRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 手机推送旁路（Bark / Server酱）：成交回执、预警触发的**第二通道**。
 *
 * <h3>设计立场：旁路就是旁路</h3>
 * 站内消息 + SSE 永远是主通道；推送只是"人不在页面时手机能响一下"。
 * 所以这里的一切失败都**只记日志 + 落一行推送日志**：用户配错 key、网络不通、
 * 服务商 500 —— 都不允许影响任何业务路径。调用方也不必 try/catch（内部已经兜住）。
 *
 * <p>A 股用户的第一刚需是"到价提醒我"（果仁有条件单、同花顺有全端推送），
 * PWA 在 iOS 上推送不可靠，所以走第三方推送服务而不是 Web Push
 * （域名 + HTTPS 之后的 Web Push 会作为新通道加进这里，Bark 降级为兜底）。
 *
 * <h3>成败语义（2026-10-15 修正）</h3>
 * 返回 true = 服务商**确认接受**：HTTP 2xx 且（Server酱）业务码 code=0。
 * 之前非 2xx 也返回 true，Profile 的"测试"按钮在 key 抄错时会假报成功——
 * 用户等一个永远不响的手机。宁可诚实返回 false，让用户当场发现配置错了。
 */
@Service
public class PushService {
    private static final Logger log = LoggerFactory.getLogger(PushService.class);

    /** bark / serverchan 之外的值一律当未配置处理（宁可不响，不能乱响）。 */
    private static final List<String> KNOWN_CHANNELS = List.of("bark", "serverchan");

    private final UserRepository userRepository;
    private final PushLogRepository pushLogRepository;
    private final ObjectMapper objectMapper;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public PushService(UserRepository userRepository,
                       PushLogRepository pushLogRepository,
                       ObjectMapper objectMapper) {
        this.userRepository = userRepository;
        this.pushLogRepository = pushLogRepository;
        this.objectMapper = objectMapper;
    }

    /** 给某用户推一条。任何失败都静默（旁路语义，见类注释）。 */
    public void push(String username, String title, String body) {
        try {
            User user = userRepository.findByUsername(username).orElse(null);
            if (user == null || user.getId() == null) {
                return;
            }
            deliver(user.getId(), user.getPushChannel(), user.getPushTarget(), title, body);
        } catch (Exception e) { // noqa: 宁静失败：推送绝不能把调用方拖垮
            log.debug("push to {} failed: {}", username, e.getMessage());
        }
    }

    /**
     * 按请求体里的配置试推一条（Profile 的"测试"按钮用）。
     *
     * <p>带用户上下文进服务层（而不是控制器直调发送）：试推也要进推送日志——
     * 用户"点了测试但手机没响"来排查时，日志里必须能看到这次尝试及其结果。
     *
     * @return 是否配置有效且服务商确认接受
     */
    public boolean pushTest(String username, String channel, String target, String title, String body) {
        User user = userRepository.findByUsername(username).orElse(null);
        if (user == null || user.getId() == null) {
            return false;
        }
        return deliver(user.getId(), channel, target, title, body);
    }

    /** 该用户最近的推送记录（Profile 页"最近推送状态"用），时间倒序。 */
    public List<Map<String, Object>> recentLogs(String username, int limit) {
        User user = userRepository.findByUsername(username).orElse(null);
        if (user == null) {
            return List.of();
        }
        List<PushLog> rows = pushLogRepository.findTop10ByUserIdOrderByCreatedAtDesc(user.getId());
        int max = Math.max(1, Math.min(limit, 10));
        List<Map<String, Object>> out = new ArrayList<>();
        for (PushLog row : rows) {
            if (out.size() >= max) {
                break;
            }
            Map<String, Object> item = new LinkedHashMap<>();
            item.put("id", row.getId());
            item.put("channel", row.getChannel());
            item.put("title", row.getTitle());
            item.put("body", row.getBody());
            item.put("ok", row.isOk());
            item.put("error", row.getError());
            item.put("createdAt", row.getCreatedAt() == null ? null : row.getCreatedAt().toString());
            out.add(item);
        }
        return out;
    }

    /**
     * 发送并记账。配置无效直接返回 false（**不产生日志行**——"没推"不是"推失败"，
     * 日志里只该有真实发出去的尝试，否则全是自己没配置的噪声）。
     */
    private boolean deliver(Long userId, String channel, String target, String title, String body) {
        if (channel == null || target == null || target.isBlank()
                || !KNOWN_CHANNELS.contains(channel)) {
            return false;
        }
        boolean ok = false;
        String error = null;
        try {
            HttpRequest request = "bark".equals(channel)
                    ? barkRequest(target, title, body)
                    : serverChanRequest(target, title, body);
            HttpResponse<String> resp = http.send(request, HttpResponse.BodyHandlers.ofString());
            String verdict = verdictOf(channel, resp.statusCode(),
                    resp.body() == null ? "" : resp.body());
            ok = verdict == null;
            error = verdict;
            if (!ok) {
                log.warn("push({}) rejected: {}", channel, error);
            }
        } catch (Exception e) {
            log.warn("push({}) failed: {}", channel, e.getMessage());
            error = clip(e.toString(), 500);
        }
        recordLog(userId, channel, title, body, ok, error);
        return ok;
    }

    /**
     * 服务商是否**确认接受**这次推送。返回 null = 接受；非空 = 给用户看的失败摘要。
     *
     * <ul>
     *   <li>HTTP 非 2xx 一律拒绝（Bark 与 Server酱相同）；</li>
     *   <li>Server酱的成功是 HTTP 200 + body JSON 的 {@code code == 0}——
     *       只看 HTTP 会把 SendKey 错误（code=40001，仍回 200）当成已送达；</li>
     *   <li>Bark 官方/自托管成功都回 {@code {"code":200}}；body 可解析且 code 非 200
     *       时按失败处理，解析不了（某些反代改写响应体）则只认 HTTP 状态。</li>
     * </ul>
     *
     * <p>包级可见：单测直接钉住各通道的成败判据（Server酱地址写死在生产域名上，
     * 没法像 Bark 那样用本地假服务器打真 HTTP——判据本身抽出来测更直接）。
     */
    String verdictOf(String channel, int status, String body) {
        if (status / 100 != 2) {
            return "HTTP " + status + "：" + clip(body, 200);
        }
        JsonNode parsed = readTree(body);
        if (parsed == null || !parsed.isObject() || !parsed.path("code").isNumber()) {
            return null;   // 2xx 且读不出业务码：按接受处理（Bark GET 场景的宽容面）
        }
        int code = parsed.path("code").asInt();
        if ("serverchan".equals(channel)) {
            return code == 0 ? null
                    : "Server酱 code=" + code + "：" + clip(parsed.path("message").asText(""), 200);
        }
        return code == 200 ? null : "Bark code=" + code;
    }

    private JsonNode readTree(String body) {
        try {
            return objectMapper.readTree(body);
        } catch (Exception e) {
            return null;
        }
    }

    /** 推送日志（旁路的旁路）：写不进只记 WARN，绝不影响推送本身。 */
    private void recordLog(Long userId, String channel, String title, String body,
                           boolean ok, String error) {
        try {
            pushLogRepository.save(PushLog.builder()
                    .userId(userId)
                    .channel(channel)
                    .title(clip(title, 255))
                    .body(clip(body, 500))
                    .ok(ok)
                    .error(error)
                    .createdAt(LocalDateTime.now(com.happyericsix.stocktracker.util.CnTime.ZONE))
                    .build());
        } catch (Exception e) {
            log.warn("推送日志写入失败（不影响推送）：{}", e.getMessage());
        }
    }

    /** Bark：GET {server}/{title}/{body}，全部 URL 编码（iOS 最常用的自托管推送）。 */
    private HttpRequest barkRequest(String server, String title, String body) {
        String base = server.replaceAll("/+$", "");
        String url = base + "/" + enc(title) + "/" + enc(body);
        return HttpRequest.newBuilder(URI.create(url))
                .timeout(Duration.ofSeconds(8))
                .GET()
                .build();
    }

    /** Server酱：POST https://sctapi.ftqq.com/{sendkey}.send（微信服务号推送）。 */
    private HttpRequest serverChanRequest(String sendKey, String title, String body) {
        String key = sendKey.trim();
        String form = "title=" + enc(title) + "&desp=" + enc(body);
        return HttpRequest.newBuilder(URI.create("https://sctapi.ftqq.com/" + key + ".send"))
                .timeout(Duration.ofSeconds(8))
                .header("Content-Type", "application/x-www-form-urlencoded")
                .POST(HttpRequest.BodyPublishers.ofString(form))
                .build();
    }

    private static String enc(String s) {
        return URLEncoder.encode(s == null ? "" : s, StandardCharsets.UTF_8);
    }

    private static String clip(String s, int max) {
        if (s == null) {
            return null;
        }
        String text = s.trim();
        return text.length() <= max ? text : text.substring(0, max);
    }
}
