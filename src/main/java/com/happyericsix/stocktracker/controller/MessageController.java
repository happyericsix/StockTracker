package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.PagedResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.UnreadCountResponse;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.MessageService;
import com.happyericsix.stocktracker.service.SseEmitterService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.servlet.mvc.method.annotation.SseEmitter;

import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/messages")
public class MessageController {

    private final MessageService messageService;
    private final SseEmitterService sseService;
    private final UserRepository userRepository;

    @GetMapping
    public PagedResponse<MessageResponse> getMessages(
            @RequestParam(required = false) String type,
            @RequestParam(defaultValue = "0") int page,
            @RequestParam(defaultValue = "20") int size,
            @RequestParam(required = false) String since,  // ISO 格式：2025-08-01T00:00:00
            Authentication authentication) {
        LocalDateTime sinceTime = null;
        if (since != null && !since.isBlank()) {
            try {
                sinceTime = LocalDateTime.parse(since, DateTimeFormatter.ISO_LOCAL_DATE_TIME);
            } catch (Exception e) {
                // 解析失败忽略，按"不限时间"处理
            }
        }
        return messageService.getMessagesPaged(authentication.getName(), page, size, type, sinceTime);
    }

    @GetMapping("/unread-count")
    public UnreadCountResponse getUnreadCount(Authentication authentication) {
        return new UnreadCountResponse(messageService.getUnreadCount(authentication.getName()));
    }

    @PutMapping("/{id}/read")
    public Result<String> markRead(@PathVariable Long id, Authentication authentication) {
        messageService.markRead(authentication.getName(), id);
        return Result.success("ok");
    }

    @PutMapping("/read-all")
    public Result<String> markAllRead(Authentication authentication) {
        messageService.markAllRead(authentication.getName());
        return Result.success("ok");
    }

    /** SSE 长连接：token 由 TokenParamFilter 从查询参数转为 Authorization 头 */
    @GetMapping("/stream")
    public SseEmitter stream(Authentication authentication) {
        User user = userRepository.findByUsername(authentication.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        return sseService.register(user.getId());
    }
}
