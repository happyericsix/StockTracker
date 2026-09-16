package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.web.reactive.function.client.WebClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.spy;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ChatServiceTest {

    @Mock
    private WebClient llmWebClient;

    @Mock
    private MessageRepository messageRepo;

    @Mock
    private UserRepository userRepository;

    @Mock
    private SseEmitterService sseService;

    @Mock
    private StrategyService strategyService;

    @Mock
    private MemoryService memoryService;

    private final ObjectMapper mapper = new ObjectMapper();

    /** 会话键固定成一个可预期值，避免用 null 去 stub callLlm */
    private static final String SESSION_KEY = "1:2026-09-16";

    private ChatService newChatService() {
        return new ChatService(llmWebClient, messageRepo, userRepository, sseService,
                strategyService, memoryService);
    }

    @Test
    void agentStrategyJsonIsPersisted() throws Exception {
        JsonNode response = mapper.readTree(
                "{\"replies\":[\"ok\"],\"strategy_json\":{\"name\":\"MACD\",\"symbol\":\"AAPL\"}}");

        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        ChatService chatService = spy(newChatService());
        doReturn(response).when(chatService).callLlm("alice", 1L, SESSION_KEY, "hi");

        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        when(userRepository.findById(1L)).thenReturn(Optional.of(user));
        when(messageRepo.save(any(Message.class))).thenAnswer(invocation -> invocation.getArgument(0));

        chatService.processAsync(1L, "alice", "hi");

        verify(strategyService, times(1)).createFromAgent(eq("alice"), any(JsonNode.class));
        // 每轮消息都要检查"上一段对话有没有总结"，否则前情提要永远补不上
        verify(memoryService, times(1)).ensurePreviousSessionConsolidated(1L, SESSION_KEY);
    }

    /**
     * 会话历史：改造前 callLlm 只发 user_id + message，Python 侧每条消息都是全新失忆的
     * agent，"它呢？""把止损改成 5%" 这类追问必然失效。这里钉住三件事：
     * 角色映射、时间顺序、以及"当前这条消息不能被重复带进去"
     * （handleChatSend 会先把当前消息落库，再异步调用本方法）。
     */
    @Test
    void historyCarriesRecentTurnsAndExcludesTheCurrentMessage() {
        ChatService chatService = newChatService();
        // 仓库按 id 倒序取最近 20 条
        when(messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(1L, List.of("CHAT_USER", "CHAT_BOT")))
                .thenReturn(List.of(
                        message("CHAT_USER", "那它呢"),
                        message("CHAT_BOT", "约 1500 元，仅供参考"),
                        message("CHAT_USER", "贵州茅台现在多少钱")));

        List<Map<String, String>> history = chatService.buildHistory(1L, "那它呢");

        assertEquals(List.of(
                Map.of("role", "user", "content", "贵州茅台现在多少钱"),
                Map.of("role", "assistant", "content", "约 1500 元，仅供参考")), history);
    }

    /** 历史为空时不能抛异常，也不该把空内容塞进请求体。 */
    @Test
    void historyIsEmptyWhenThereIsNoPriorChat() {
        ChatService chatService = newChatService();
        when(messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(1L, List.of("CHAT_USER", "CHAT_BOT")))
                .thenReturn(List.of(message("CHAT_USER", "hi")));

        assertTrue(chatService.buildHistory(1L, "hi").isEmpty());
    }

    /** 历史条数上限：请求体不能随对话无限增长。 */
    @Test
    void historyIsCappedAtRecentMessages() {
        ChatService chatService = newChatService();
        List<Message> recent = new java.util.ArrayList<>();
        for (int i = 39; i >= 20; i--) {
            recent.add(message(i % 2 == 0 ? "CHAT_USER" : "CHAT_BOT", "m" + i));
        }
        when(messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(1L, List.of("CHAT_USER", "CHAT_BOT")))
                .thenReturn(recent);

        List<Map<String, String>> history = chatService.buildHistory(1L, "新消息");

        assertEquals(12, history.size());
        // 取的是最近的一批，最早的 "m20" 必须已经被丢掉
        assertEquals("m28", history.get(0).get("content"));
        assertEquals("m39", history.get(11).get("content"));
    }

    private Message message(String type, String content) {
        return Message.builder().type(type).content(content).build();
    }
}
