package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 预警消息的 agent 读取口（2026-10-16）：{@code GET /api/v1/internal/messages/alerts}。
 *
 * <p>给 AI 助手的 {@code get_my_alerts} 工具用——用户问"我最近有什么预警 / 错过了什么"
 * 时，助手能查到**价格指标预警（ALERT）与新闻预警（NEWS_ALERT）**两类。照
 * {@code InternalStrategyController} 的同一条纪律：
 *
 * <ol>
 *   <li><b>只读</b>：agent 不能改消息（没有标记已读的内部端点，读回执是用户行为）；</li>
 *   <li><b>身份不接受模型传入的userId之外的字段</b>，userId 由 tool_scope 注入；</li>
 *   <li><b>范围刻意只有两类预警</b>：哨兵/复盘报告有自己的页面与工具（策略侧），
 *       聊天记录（CHAT_*）绝不下发——那是对话本身，不是预警。</li>
 * </ol>
 */
@RestController
@RequestMapping("/api/v1/internal/messages")
public class InternalMessagesController {

    private static final Logger log = LoggerFactory.getLogger(InternalMessagesController.class);

    /** agent 看预警：只这两类。扩大范围前先想"模型拿着它能回答什么问题"。 */
    private static final List<String> ALERT_TYPES = List.of("ALERT", "NEWS_ALERT");

    private static final int MAX_LIMIT = 30;

    private final UserRepository userRepository;
    private final MessageRepository messageRepository;
    private final String internalToken;

    public InternalMessagesController(UserRepository userRepository,
                                      MessageRepository messageRepository,
                                      @Value("${internal.api-token:}") String internalToken) {
        this.userRepository = userRepository;
        this.messageRepository = messageRepository;
        this.internalToken = internalToken;
    }

    /** 用户最近的预警（新→旧，默认 10 条）：id/类型/内容/关联标的/时间/是否已读。 */
    @GetMapping("/alerts")
    public ResponseEntity<Map<String, Object>> alerts(
            @RequestParam Long userId,
            @RequestParam(defaultValue = "10") int limit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).build();
        }
        int size = Math.max(1, Math.min(limit, MAX_LIMIT));
        List<Message> messages = messageRepository
                .findByUserIdAndTypeIn(user.getId(), ALERT_TYPES, PageRequest.of(0, size))
                .getContent();

        List<Map<String, Object>> items = messages.stream().map(m -> {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", m.getId());
            row.put("type", m.getType());
            row.put("content", m.getContent());
            row.put("relatedSymbol", m.getRelatedSymbol());
            row.put("createdAt", m.getCreatedAt() == null ? null : m.getCreatedAt().toString());
            row.put("read", Boolean.TRUE.equals(m.getRead()));
            return row;
        }).toList();

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("alerts", items);
        body.put("types", ALERT_TYPES);
        return ResponseEntity.ok(body);
    }

    private boolean authorized(String token) {
        if (internalToken == null || internalToken.isBlank()) {
            log.error("internal.api-token 未配置，拒绝内部预警消息接口访问");
            return false;
        }
        if (token == null) {
            return false;
        }
        return java.security.MessageDigest.isEqual(
                token.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                internalToken.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }

    private ResponseEntity<Map<String, Object>> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED)
                .body(Map.of("error", "unauthorized"));
    }
}
