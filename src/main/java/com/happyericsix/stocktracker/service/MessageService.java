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
    /** 预警推送旁路。允许为 null（单测）：推送失败不影响站内消息。 */
    private final PushService pushService;

    public MessageService(MessageRepository messageRepo, UserRepository userRepository,
                          SseEmitterService sseService, ChatService chatService,
                          StockService stockService, MemoryService memoryService,
                          PushService pushService) {
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.chatService = chatService;
        this.stockService = stockService;
        this.memoryService = memoryService;
        this.pushService = pushService;
    }

    /** 给消息响应补股票名称（解析失败回退为代码） */
    private MessageResponse withSymbolName(Message m) {
        MessageResponse r = MessageResponse.from(m);
        if (r.getRelatedSymbol() != null && !r.getRelatedSymbol().isBlank()) {
            r.setRelatedSymbolName(stockService.resolveStockName(r.getRelatedSymbol()));
        }
        return r;
    }

    /**
     * 消息中心的类型口径（2026-10-10 收窄）：**只收预警与系统级告警**。
     *
     * <p>为什么收窄：聊天回复用户在聊天界面当场看过了、模拟盘日报/哨兵观察
     * 活在模拟盘页面（配合策略调整）—— 它们再进消息中心就是同一件事说第二遍，
     * 而重复通知是用户关掉通知的第一原因。聊天/日报数据本身仍照常落库
     * （聊天历史、审计与回放都靠它），只是不再出现在消息中心与未读角标里。
     *
     * <p>STRATEGY_REVIEW（2026-10-15）是收窄原则下的**刻意的例外**：月度小结/季度
     * 策略信是用户点名要的"提醒"——频率是每策略每月最多一条，不属于日报级噪声；
     * 而且它不是"页面上能看的东西再说一遍"，复盘页面并不展示它。
     */
    private static final List<String> CENTER_TYPES = List.of("ALERT", "STRATEGY_SENTINEL", "STRATEGY_REVIEW", "NEWS_ALERT");

    /**
     * 分页查询（消息中心主用）
     * @param page   页码（0-based）
     * @param size   每页条数
     * @param type   可选过滤，必须落在消息中心口径内（通常就是 ALERT）
     * @param since  可选时间范围：只返回 createdAt >= since 的消息；null=不限
     */
    public PagedResponse<MessageResponse> getMessagesPaged(String username, int page, int size, String type, java.time.LocalDateTime since) {
        User user = getUser(username);
        Pageable pageable = PageRequest.of(page, size, Sort.by(Sort.Direction.DESC, "createdAt"));
        List<String> types = centerTypes(type);
        boolean hasSince = since != null;

        Page<Message> result;
        if (types.isEmpty()) {
            result = Page.empty(pageable);
        } else if (hasSince) {
            result = messageRepo.findByUserIdAndTypeInAndCreatedAtGreaterThanEqual(
                    user.getId(), types, since, pageable);
        } else {
            result = messageRepo.findByUserIdAndTypeIn(user.getId(), types, pageable);
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

    /** 口径与请求 type 的交集：type 不在口径内 → 空列表（返回空页而不是破例放行）。 */
    private static List<String> centerTypes(String type) {
        if (type == null || type.isBlank()) {
            return CENTER_TYPES;
        }
        return CENTER_TYPES.contains(type) ? List.of(type) : List.of();
    }

    /** 未读角标同样只算口径内的：聊天与日报不再制造红点。 */
    public long getUnreadCount(String username) {
        User user = getUser(username);
        return messageRepo.countByUserIdAndReadFalseAndTypeIn(user.getId(), CENTER_TYPES);
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
     * 3. **事务提交后**再异步交给 ChatService 调 LLM，
     *    回复由 ChatService.saveBotReply 自己落库并推送
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

        // 异步线程的 buildHistory 依赖"这条 CHAT_USER 已提交可见"来剔除当前消息；
        // 事务提交前触发会把同一条消息重复喂给模型。注册 afterCommit 兜住这一点。
        if (org.springframework.transaction.support.TransactionSynchronizationManager
                .isSynchronizationActive()) {
            org.springframework.transaction.support.TransactionSynchronizationManager
                    .registerSynchronization(
                            new org.springframework.transaction.support.TransactionSynchronization() {
                                @Override
                                public void afterCommit() {
                                    chatService.submitChat(user.getId(), username, request.getMessage(),
                                            request.getFocus());
                                }
                            });
        } else {
            // 无事务上下文（单测直调）时保持原行为
            chatService.submitChat(user.getId(), username, request.getMessage(), request.getFocus());
        }
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

    /**
     * 这份报告是否已发过（按幂等键）。调用方用它决定"要不要花成本生成"——
     * 例如季度信的评语半环要调一次 LLM，先问一声再动手。
     */
    public boolean reportSent(String dedupeKey) {
        return dedupeKey != null && !dedupeKey.isBlank()
                && messageRepo.findByDedupeKey(dedupeKey).isPresent();
    }

    /**
     * 发一条**幂等**的系统消息（报告类）。
     *
     * <h3>为什么报告需要幂等键</h3>
     * 报告由定时任务产生，而定时任务会被重跑：应用重启、手工补跑、同一份数据被两个入口各处理一次。
     * 没有键的后果不是"多一条消息"，而是<b>用户开始忽略这类消息</b>——
     * 一份每天可能出现两三次的复盘，第三周就没人看了（这正是 P1 用已读率验收的原因）。
     *
     * <p>已存在同一个键的消息时：**什么都不做**并返回那条已有消息。
     * 刻意不更新内容：报告是"当时那一刻的结论"，被后来的重跑悄悄改写，
     * 就等于把历史记录改掉了（真正变化的内容应该是"新的结论"，
     * 它有自己的键——比如新的一天）。
     *
     * <p><b>为什么是独立事务（REQUIRES_NEW）</b>：报告的调用方是结算任务。
     * 加入调用方事务的话，一次报告写入失败（唯一键冲突、DB 抖动）会把整个事务标记成
     * rollback-only，于是**为了发一条消息而丢掉当天的真实成交**。
     * 报告是增强件，必须自己承担失败的后果。
     */
    @Transactional(propagation = org.springframework.transaction.annotation.Propagation.REQUIRES_NEW)
    public Message saveReport(User user, String type, String dedupeKey, String relatedSymbol,
                              String content) {
        if (user == null || dedupeKey == null || dedupeKey.isBlank()) {
            log.warn("报告缺少幂等键，已丢弃（无键的报告会在重跑时变成重复消息）");
            return null;
        }
        var existing = messageRepo.findByDedupeKey(dedupeKey);
        if (existing.isPresent()) {
            log.debug("报告已存在，跳过推送 key={}", dedupeKey);
            return existing.get();
        }
        Message message = Message.builder()
                .user(user)
                .type(type)
                .content(content)
                .relatedSymbol(relatedSymbol)
                .dedupeKey(dedupeKey)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(user.getId(), withSymbolName(message));
        log.info("Pushed report {} to user {}: key={}", type, user.getUsername(), dedupeKey);
        return message;
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
        // 手机推送旁路：人不在页面时预警也要能到手机。失败只记日志（PushService 内部已兜）。
        if (pushService != null) {
            try {
                pushService.push(user.getUsername(), "预警触发：" + alert.getStockSymbol(), messageText);
            } catch (Exception e) {
                log.debug("alert push failed: {}", e.getMessage());
            }
        }
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
