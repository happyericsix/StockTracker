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
 */
@Service
public class SseEmitterService {

    private static final Logger log = LoggerFactory.getLogger(SseEmitterService.class);

    private final ConcurrentHashMap<Long, CopyOnWriteArrayList<SseEmitter>> registry = new ConcurrentHashMap<>();
    private final ObjectMapper objectMapper;

    public SseEmitterService(ObjectMapper objectMapper) {
        this.objectMapper = objectMapper;
    }

    /** 注册一条长连接，永不过期（0L），断连靠心跳与事件回调清理 */
    public SseEmitter register(Long userId) {
        SseEmitter emitter = new SseEmitter(0L);
        registry.computeIfAbsent(userId, k -> new CopyOnWriteArrayList<>()).add(emitter);

        Runnable cleanup = () -> remove(userId, emitter);
        emitter.onCompletion(cleanup);
        emitter.onTimeout(cleanup);
        emitter.onError(e -> cleanup.run());

        try {
            emitter.send(SseEmitter.event().name("connected").data("ok"));
        } catch (IOException e) {
            remove(userId, emitter);
        }
        log.info("SSE connected: userId={}, online={}", userId, registry.get(userId).size());
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
                    remove(userId, emitter);
                }
            }
        } catch (Exception e) {
            log.warn("消息序列化失败: {}", e.getMessage());
        }
    }

    /** 每 25 秒心跳：保活代理/网关连接，同时剔除死连接 */
    @Scheduled(fixedRate = 25_000)
    public void heartbeat() {
        registry.forEach((userId, emitters) -> {
            for (SseEmitter emitter : emitters) {
                try {
                    emitter.send(SseEmitter.event().name("ping").data(""));
                } catch (IOException | IllegalStateException e) {
                    remove(userId, emitter);
                }
            }
        });
    }

    private void remove(Long userId, SseEmitter emitter) {
        List<SseEmitter> emitters = registry.get(userId);
        if (emitters != null) {
            emitters.remove(emitter);
            if (emitters.isEmpty()) {
                registry.remove(userId, emitters);
            }
        }
    }
}
