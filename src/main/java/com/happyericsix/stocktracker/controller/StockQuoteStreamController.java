package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.SseEmitterService;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

/**
 * 行情 SSE 长连接（快车道，2026-10-08）：前端用 EventSource 订阅，
 * 每次价格刷新（交易时段约 15 秒一次）就收到一批最新价。
 *
 * <p>token 由 {@code TokenParamFilter} 从查询参数转为 Authorization 头 ——
 * EventSource 无法携带请求头，与消息流同一条纪律。
 */
@RestController
@RequestMapping("/api/v1/stocks")
public class StockQuoteStreamController {

    private final SseEmitterService sseService;
    private final UserRepository userRepository;

    public StockQuoteStreamController(SseEmitterService sseService, UserRepository userRepository) {
        this.sseService = sseService;
        this.userRepository = userRepository;
    }

    @GetMapping("/stream")
    public SseEmitter stream(Authentication authentication,
            @org.springframework.web.bind.annotation.RequestParam(required = false) String focus) {
        User user = userRepository.findByUsername(authentication.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        // focus=图表页正在看的标的：进 15 秒刷新批，SSE 会推它的实时价（2026-10-15）
        return sseService.registerQuotes(user.getId(), focus);
    }
}
