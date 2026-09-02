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

import java.util.Optional;

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

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void agentStrategyJsonIsPersisted() throws Exception {
        JsonNode response = mapper.readTree(
                "{\"replies\":[\"ok\"],\"strategy_json\":{\"name\":\"MACD\",\"symbol\":\"AAPL\"}}");

        ChatService chatService = spy(new ChatService(
                llmWebClient, messageRepo, userRepository, sseService, strategyService));
        doReturn(response).when(chatService).callLlm("alice", "hi");

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
    }
}
