package com.happyericsix.stocktracker.service;

import tools.jackson.databind.ObjectMapper;
import com.happyericsix.stocktracker.dto.MessageResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.io.IOException;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;

/**
 * SSE 连接注册表：按 userId 维护在线连接，负责推送消息与保活心跳。
 * 单机内存实现；多实例部署时需换 Redis pub/sub。
 *
 * <h3>两条通道（2026-10-08 起）</h3>
 * <ul>
 *   <li><b>消息</b>（{@code /api/v1/messages/stream}）：站内消息推送，按用户定向；</li>
 *   <li><b>行情</b>（{@code /api/v1/stocks/stream}）：快车道价格推送。行情是公开市场
 *       数据，且自选股是"全体用户股票的并集"刷新的，所以**推给所有连着行情通道的
 *       连接**、不按用户过滤 —— 个人部署下这最简单也不会泄漏任何私有信息。</li>
 * </ul>
 */
@Service
public class SseEmitterService {

    private static final Logger log = LoggerFactory.getLogger(SseEmitterService.class);

    private final ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> registry = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> quoteRegistry =
            new ConcurrentHashMap<>();
    private final ObjectMapper objectMapper;

    public SseEmitterService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /** 注册一条长连接，永不过期（0L），断连靠心跳与事件回调清理 */
    public SseEmitter register(Long userId) {
        SseEmitter emitter = doRegister(userId, registry);
        log.info("SSE connected: userId={}, online={}", userId, registry.get(userId).size());
        return emitter;
    }

    /** 注册一条行情长连接（与消息通道分开：事件名不同、推送方不同、但心跳共用）。 */
    public SseEmitter registerQuotes(Long userId) {
        SseEmitter emitter = doRegister(userId, quoteRegistry);
        log.info("SSE quotes connected: userId={}, online={}", userId, quoteRegistry.get(userId).size());
        return emitter;
    }

    private SseEmitter doRegister(Long userId,
                                  ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> target) {
        SseEmitter emitter = new SseEmitter(0L);
        target.computeIfAbsent(userId, k -> new CopyOnWriteArrayList<>()).add(emitter);

        Runnable cleanup = () -> remove(target, userId, emitter);
        emitter.onCompletion(cleanup);
        emitter.onTimeout(cleanup);
        emitter.onError(e -> cleanup.run());

        try {
            emitter.send(SseEmitter.event().name("connected").data("ok"));
        } catch (IOException e) {
            remove(target, userId, emitter);
        }
        return emitter;
    }

    /** 推送一条消息给指定用户的所有连接 */
    public void push(Long userId, MessageResponse message) {
        List<SseEmitter> emitters = registry.get(userId);
        if (emitters == null || emitters.isEmpty()) {
            return;
        }
        try {
            String json = objectMapper.writeValueAsString(message);
            for (SseEmitter emitter : emitters) {
                try {
                    emitter.send(SseEmitter.event().name("message").data(json));
                } catch (IOException | IllegalStateException e) {
                    remove(registry, userId, emitter);
                }
            }
        } catch (Exception e) {
            log.warn("消息序列化失败: {}", e.getMessage());
        }
    }

    /**
     * 推送一批最新价（快车道，事件名 {@code quotes}）给所有行情连接。
     *
     * <p>序列化由调用方完成（它才知道这一批的形状），这里只负责发；
     * 发送失败按死连接清理 —— 与消息通道同一条纪律。
     */
    public void pushQuotes(String json) {
        if (json == null || json.isBlank() || quoteRegistry.isEmpty()) {
            return;
        }
        quoteRegistry.forEach((userId, emitters) -> {
            for (SseEmitter emitter : emitters) {
                try {
                    emitter.send(SseEmitter.event().name("quotes").data(json));
                } catch (IOException | IllegalStateException e) {
                    remove(quoteRegistry, userId, emitter);
                }
            }
        });
    }

    /** 每 25 秒心跳：保活代理/网关连接，同时剔除死连接（两条通道都要保活）。 */
    @Scheduled(fixedRate = 25_000)
    public void heartbeat() {
        heartbeat(registry);
        heartbeat(quoteRegistry);
    }

    private void heartbeat(ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> target) {
        target.forEach((userId, emitters) -> {
            for (SseEmitter emitter : emitters) {
                try {
                    emitter.send(SseEmitter.event().name("ping").data(""));
                } catch (IOException | IllegalStateException e) {
                    remove(target, userId, emitter);
                }
            }
        });
    }

    private void remove(ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> target,
                        Long userId, SseEmitter emitter) {
        List<SseEmitter> emitters = target.get(userId);
        if (emitters != null) {
            emitters.remove(emitter);
            if (emitters.isEmpty()) {
                target.remove(userId, emitters);
            }
        }
    }
}
