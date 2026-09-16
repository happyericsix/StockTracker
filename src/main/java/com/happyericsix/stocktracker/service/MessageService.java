package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.ChatSendRequest;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.PagedResponse;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Pageable;
import org.springframework.data.domain.Sort;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.stream.Collectors;

@Service
public class MessageService {

    private static final Logger log = LoggerFactory.getLogger(MessageService.class);
    private static final List<String> CHAT_TYPES = List.of("CHAT_USER", "CHAT_BOT");

    private final MessageRepository messageRepo;
    private final UserRepository userRepository;
    private final SseEmitterService sseService;
    private final ChatService chatService;
    private final StockService stockService;
    private final MemoryService memoryService;

    public MessageService(MessageRepository messageRepo, UserRepository userRepository,
                          SseEmitterService sseService, ChatService chatService,
                          StockService stockService, MemoryService memoryService) {
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.chatService = chatService;
        this.stockService = stockService;
        this.memoryService = memoryService;
    }

    /** 给消息响应补股票名称（解析失败回退为代码） */
    private MessageResponse withSymbolName(Message m) {
        MessageResponse r = MessageResponse.from(m);
        if (r.getRelatedSymbol() != null && !r.getRelatedSymbol().isBlank()) {
            r.setRelatedSymbolName(stockService.resolveStockName(r.getRelatedSymbol()));
        }
        return r;
    }

    public List<MessageResponse> getMessages(String username) {
        User user = getUser(username);
        return messageRepo.findByUserIdOrderByCreatedAtDesc(user.getId()).stream()
                .map(this::withSymbolName)
                .collect(Collectors.toList());
    }

    /** 按 type 过滤查询（如 type=ALERT 看预警历史，type=CHAT_USER 聊天记录） */
    public List<MessageResponse> getMessagesByType(String username, String type) {
        User user = getUser(username);
        return messageRepo.findByUserIdAndTypeOrderByCreatedAtDesc(user.getId(), type).stream()
                .map(this::withSymbolName)
                .collect(Collectors.toList());
    }

    /**
     * 分页查询（消息中心主用）
     * @param page   页码（0-based）
     * @param size   每页条数
     * @param type   可选过滤：ALERT/CHAT_USER/CHAT_BOT/SYSTEM；null=全部
     * @param since  可选时间范围：只返回 createdAt >= since 的消息；null=不限
     */
    public PagedResponse<MessageResponse> getMessagesPaged(String username, int page, int size, String type, java.time.LocalDateTime since) {
        User user = getUser(username);
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));
        boolean hasType = type != null && !type.isBlank();
        boolean hasSince = since != null;

        Page<Message> result;
        if (hasType && hasSince) {
            result = messageRepo.findByUserIdAndTypeAndCreatedAtGreaterThanEqual(user.getId(), type, since, pageable);
        } else if (hasType) {
            result = messageRepo.findByUserIdAndType(user.getId(), type, pageable);
        } else if (hasSince) {
            result = messageRepo.findByUserIdAndCreatedAtGreaterThanEqual(user.getId(), since, pageable);
        } else {
            result = messageRepo.findByUserId(user.getId(), pageable);
        }

        List<MessageResponse> content = result.getContent().stream()
                .map(this::withSymbolName)
                .toList();
        return new PagedResponse<>(
                content,
                result.getNumber(),
                result.getSize(),
                result.getTotalElements(),
                result.getTotalPages()
        );
    }

    public long getUnreadCount(String username) {
        User user = getUser(username);
        return messageRepo.countByUserIdAndReadFalse(user.getId());
    }

    @Transactional
    public void markRead(String username, Long id) {
        User user = getUser(username);
        Message message = messageRepo.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("消息不存在"));
        if (Boolean.FALSE.equals(message.getRead())) {
            message.setRead(true);
            messageRepo.save(message);
        }
    }

    @Transactional
    public void markAllRead(String username) {
        User user = getUser(username);
        messageRepo.markAllReadByUserId(user.getId());
    }

    public List<MessageResponse> getChatHistory(String username) {
        User user = getUser(username);
        return messageRepo.findByUserIdAndTypeInOrderByCreatedAtAsc(user.getId(), CHAT_TYPES).stream()
                .map(this::withSymbolName)
                .collect(Collectors.toList());
    }

    /**
     * 发送聊天消息：
     * 1. 立即落库 CHAT_USER 并回显推送
     * 2. 写记忆账本（用户说了什么，这是"前情提要"的原料，只追加）
     * 3. 异步交给 ChatService 调 LLM，回复通过 saveAndPush 回来
     */
    @Transactional
    public void handleChatSend(String username, ChatSendRequest request) {
        User user = getUser(username);
        Message userMsg = Message.builder()
                .user(user)
                .type("CHAT_USER")
                .content(request.getMessage())
                .read(true)
                .build();
        userMsg = messageRepo.save(userMsg);
        sseService.push(user.getId(), withSymbolName(userMsg));

        appendUserEventToLedger(user.getId(), request.getMessage());

        chatService.processAsync(user.getId(), username, request.getMessage());
    }

    /**
     * 用户消息入账。失败只记日志 —— 记忆是增强功能，
     * 账本写不进去也绝不能让用户发不出消息。
     */
    private void appendUserEventToLedger(Long userId, String content) {
        try {
            memoryService.appendChat(userId, memoryService.currentSessionKey(userId),
                    "chat_user", "user", content, null);
        } catch (Exception e) {
            log.warn("记忆账本写入失败 user={}: {}", userId, e.getMessage());
        }
    }

    /** 内部统一入口：落库 + SSE 推送（预警任务与聊天回复共用） */
    @Transactional
    public void saveAndPush(User user, String type, String content, String relatedSymbol) {
        Message message = Message.builder()
                .user(user)
                .type(type)
                .content(content)
                .relatedSymbol(relatedSymbol)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(user.getId(), withSymbolName(message));
        log.info("Pushed {} to user {}: {}", type, user.getUsername(), content);
    }

    /**
     * 预警触发便捷方法：写一条 type=ALERT 的消息（含 alertId + JSON 元数据），并 SSE 推送
     *
     * @param user          预警所属用户
     * @param alert         触发的预警实体
     * @param currentPrice  触发时的当前价
     * @param triggerValue  触发的指标值（RSI=72.5 / pnl%=8.2 / price=195.32）
     * @param messageText   人类可读的描述，如 "AAPL RSI 升至 72.5，超过阈值 70"
     */
    @Transactional
    public void recordAlertTrigger(User user, Alert alert, double currentPrice, double triggerValue, String messageText) {
        String metadata = String.format(
                "{\"triggerPrice\":%.4f,\"triggerValue\":%.4f,\"threshold\":%.4f,\"conditionType\":\"%s\"}",
                currentPrice, triggerValue, alert.getThreshold(), escapeJson(alert.getConditionType())
        );
        Message message = Message.builder()
                .user(user)
                .type("ALERT")
                .content(messageText)
                .relatedSymbol(alert.getStockSymbol())
                .alertId(alert.getId())
                .metadata(metadata)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(user.getId(), withSymbolName(message));
        log.info("Alert triggered: user={}, symbol={}, type={}, message={}",
                user.getUsername(), alert.getStockSymbol(), alert.getConditionType(), messageText);
    }

    /**
     * 冷却判断：指定预警在 since 之后是否触发过
     * 用于 AlertEvaluationListener 防抖，避免同一预警短时间反复触发
     */
    public boolean isAlertInCooldown(Long alertId, java.time.LocalDateTime since) {
        return messageRepo.existsByAlertIdAndCreatedAtGreaterThan(alertId, since);
    }

    private String escapeJson(String s) {
        if (s == null) return "";
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }
}
