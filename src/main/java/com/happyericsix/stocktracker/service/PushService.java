package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;

/**
 * 手机推送旁路（Bark / Server酱）：成交回执、预警触发的**第二通道**。
 *
 * <h3>设计立场：旁路就是旁路</h3>
 * 站内消息 + SSE 永远是主通道；推送只是"人不在页面时手机能响一下"。
 * 所以这里的一切失败都**只记日志**：用户配错 key、网络不通、服务商 500 ——
 * 都不允许影响任何业务路径。调用方也不必 try/catch（内部已经兜住）。
 *
 * <p>A 股用户的第一刚需是"到价提醒我"（果仁有条件单、同花顺有全端推送），
 * PWA 在 iOS 上推送不可靠，所以走第三方推送服务而不是 Web Push。
 */
@Service
public class PushService {
    private static final Logger log = LoggerFactory.getLogger(PushService.class);

    /** bark / serverchan 之外的值一律当未配置处理（宁可不响，不能乱响）。 */
    private static final List<String> KNOWN_CHANNELS = List.of("bark", "serverchan");

    private final UserRepository userRepository;
    private final HttpClient http = HttpClient.newBuilder()
            .connectTimeout(Duration.ofSeconds(5))
            .build();

    public PushService(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    /** 给某用户推一条。任何失败都静默（旁路语义，见类注释）。 */
    public void push(String username, String title, String body) {
        try {
            User user = userRepository.findByUsername(username).orElse(null);
            if (user == null) {
                return;
            }
            pushTo(user.getPushChannel(), user.getPushTarget(), title, body);
        } catch (Exception e) { // noqa: 宁静失败：推送绝不能把调用方拖垮
            log.debug("push to {} failed: {}", username, e.getMessage());
        }
    }

    /** 直接按配置推送（Profile 的"测试"按钮用；返回是否配置有效并已尝试发送）。 */
    public boolean pushTo(String channel, String target, String title, String body) {
        if (channel == null || target == null || target.isBlank()
                || !KNOWN_CHANNELS.contains(channel)) {
            return false;
        }
        try {
            HttpRequest request = "bark".equals(channel)
                    ? barkRequest(target, title, body)
                    : serverChanRequest(target, title, body);
            HttpResponse<String> resp = http.send(request, HttpResponse.BodyHandlers.ofString());
            if (resp.statusCode() / 100 != 2) {
                log.warn("push({}) returned HTTP {}: {}", channel, resp.statusCode(),
                        resp.body() == null ? "" : resp.body().substring(0,
                                Math.min(200, resp.body().length())));
            }
            return true;
        } catch (Exception e) {
            log.warn("push({}) failed: {}", channel, e.getMessage());
            return false;
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
}
