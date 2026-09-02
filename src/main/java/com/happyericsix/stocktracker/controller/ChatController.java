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
}
