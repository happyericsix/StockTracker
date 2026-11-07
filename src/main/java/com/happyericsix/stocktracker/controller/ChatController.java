package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.ChatSendRequest;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.MessageService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/chat")
public class ChatController {
    private final MessageService messageService;
    /** 发送后立即返回，回复由 ChatService 异步计算并通过 SSE 推送 */
    @PostMapping("/send")
    public Result<String> send(@RequestBody ChatSendRequest request, Authentication authentication) {
        if (request.getMessage() == null || request.getMessage().isBlank()) {
            return Result.error(400, "消息不能为空");
        }
        messageService.handleChatSend(authentication.getName(), request);
        return Result.success("processing");
    }
    @GetMapping("/history")
    public List<MessageResponse> history(Authentication authentication) {
        return messageService.getChatHistory(authentication.getName());
    }

    /**
     * 聚焦会话历史（2026-11-07 三轮）：同一条新闻/教训的问 AI 往来（缓存窗口内）。
     * 抽屉打开时回显"之前的记录"——同新闻续篇、过期即空。
     */
    @GetMapping("/focus-history")
    public List<MessageResponse> focusHistory(Authentication authentication,
                                              @RequestParam String type,
                                              @RequestParam Long id) {
        ChatSendRequest.Focus focus = "memory_lesson".equals(type)
                ? new ChatSendRequest.Focus(type, null, id)
                : new ChatSendRequest.Focus(type, id);
        return messageService.getFocusHistory(authentication.getName(), focus);
    }
}
