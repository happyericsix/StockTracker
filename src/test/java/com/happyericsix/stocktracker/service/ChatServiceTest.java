package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.ChatSendRequest;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;
import org.springframework.web.reactive.function.client.WebClient;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doAnswer;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.never;
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

    @Mock
    private UserLlmConfigService userLlmConfigService;

    private final ObjectMapper mapper = new ObjectMapper();

    /** 会话键固定成一个可预期值，避免用 null 去 stub callLlm */
    private static final String SESSION_KEY = "1:2026-09-16";

    private ChatService newChatService() {
        // 单线程池：submitChat 的中断语义必须串行才能确定性地观察（第二轮排队，
        // 直到第一轮被 cancel 中断退出后才开始跑）
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(1);
        executor.setMaxPoolSize(1);
        executor.setQueueCapacity(50);
        executor.setThreadNamePrefix("test-chat-");
        executor.initialize();
        return new ChatService(llmWebClient, messageRepo, userRepository, sseService,
                strategyService, memoryService, userLlmConfigService, executor, mapper);
    }

    @Test
    void agentStrategyJsonIsPersisted() throws Exception {
        JsonNode response = mapper.readTree(
                "{\"replies\":[\"ok\"],\"strategy_json\":{\"name\":\"MACD\",\"symbol\":\"AAPL\"}}");

        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        ChatService chatService = spy(newChatService());
        doReturn(response).when(chatService).callLlmStream("alice", 1L, SESSION_KEY, "hi", null, 0L);

        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        when(userRepository.findById(1L)).thenReturn(Optional.of(user));
        when(messageRepo.save(any(Message.class))).thenAnswer(invocation -> invocation.getArgument(0));

        // 直调 runRound（不经线程池）：currentRounds 未登记 → 视为最新轮次
        chatService.runRound(1L, "alice", "hi", null, 0L);

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

    /**
     * 聚焦过滤（agent 互通 P2）：只有 type=news_event 且 eventId 齐备才透传 ——
     * null / 未知 type / 缺 id 都按普通聊天处理（Python 侧对收到的 focus 同判）。
     */
    @Test
    void focusFilterOnlyAcceptsCompleteNewsEvent() {
        assertTrue(ChatService.isNewsFocus(new ChatSendRequest.Focus("news_event", 321L)),
                "完整的 news_event 应透传");
        assertFalse(ChatService.isNewsFocus(null), "无聚焦 = 普通聊天");
        assertFalse(ChatService.isNewsFocus(new ChatSendRequest.Focus("unknown_type", 1L)),
                "未知 type 不透传（前端拼错字段不能炸掉聊天）");
        assertFalse(ChatService.isNewsFocus(new ChatSendRequest.Focus("news_event", null)),
                "缺 eventId 不透传");
    }

    /**
     * 反问聚焦（方案一 2026-10-16）：type=memory_lesson 且 lessonId 齐备才透传；
     * 两种聚焦互不认账（news_event 不吃 lessonId，memory_lesson 不吃 eventId）。
     */
    @Test
    void focusFilterOnlyAcceptsCompleteMemoryLesson() {
        assertTrue(ChatService.isLessonFocus(
                        new ChatSendRequest.Focus("memory_lesson", null, 41L)),
                "完整的 memory_lesson 应透传");
        assertFalse(ChatService.isLessonFocus(null), "无聚焦 = 普通聊天");
        assertFalse(ChatService.isLessonFocus(
                        new ChatSendRequest.Focus("memory_lesson", 41L, null)),
                "eventId 冒充不了 lessonId");
        assertFalse(ChatService.isLessonFocus(
                        new ChatSendRequest.Focus("news_event", 321L)),
                "news_event 不是反问聚焦");
    }

    /**
     * 用户连发（中断旧请求策略）：旧轮次的回复必须整体丢弃 ——
     * 不落库、不 SSE 推送、不入记忆账本。否则两条 LLM 往返并发跑，
     * 回复乱序落库、几乎同时到达，前端一次刷出两坨完整内容。
     */
    @Test
    void supersededRoundIsDiscarded() throws Exception {
        JsonNode response = mapper.readTree("{\"replies\":[\"旧回复\"],\"strategy_json\":null}");
        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        ChatService chatService = spy(newChatService());
        doReturn(response).when(chatService).callLlmStream("alice", 1L, SESSION_KEY, "hi", null, 1L);
        // 用户已发出更新的消息（轮次 2）—— 轮次 1 已作废
        chatService.currentRounds.computeIfAbsent(1L, k -> new AtomicLong()).set(2L);

        chatService.runRound(1L, "alice", "hi", null, 1L);

        verify(messageRepo, never()).save(any());
        verify(sseService, never()).push(any(), any());
        verify(memoryService, never()).append(any());
        // 撤掉前端正在流式输出的半截气泡
        verify(sseService).pushRaw(eq(1L), argThat(json -> json.contains("CHAT_BOT_ABORT")));
    }

    /**
     * 被中断的轮次不能落"智能助手暂时不可用"兜底：用户已经在等新问题的答案，
     * 一条迟到的错误回复只会制造"两条问题加一条假报错"的混乱。
     * （cancel(true) 打断 WebClient block() 时表现为 callLlm 抛异常。）
     */
    @Test
    void interruptedRoundSavesNoErrorFallback() {
        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        ChatService chatService = spy(newChatService());
        doThrow(new RuntimeException("Interrupted")).when(chatService)
                .callLlmStream("alice", 1L, SESSION_KEY, "hi", null, 1L);
        chatService.currentRounds.computeIfAbsent(1L, k -> new AtomicLong()).set(2L);

        chatService.runRound(1L, "alice", "hi", null, 1L);

        verify(messageRepo, never()).save(any());
        verify(sseService).pushRaw(eq(1L), argThat(json -> json.contains("CHAT_BOT_ABORT")));
    }

    /** 真实失败（非中断，如 60 秒超时）仍要落兜底回复——不能把"暂时不可用"一并吞掉。 */
    @Test
    void failedRoundStillSavesFallback() {
        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        ChatService chatService = spy(newChatService());
        doThrow(new RuntimeException("timeout")).when(chatService)
                .callLlmStream("alice", 1L, SESSION_KEY, "hi", null, 1L);
        chatService.currentRounds.computeIfAbsent(1L, k -> new AtomicLong()).set(1L);
        User user = User.builder().id(1L).username("alice").password("secret").email("a@b.c").build();
        when(userRepository.findById(1L)).thenReturn(Optional.of(user));
        when(messageRepo.save(any(Message.class))).thenAnswer(inv -> inv.getArgument(0));

        chatService.runRound(1L, "alice", "hi", null, 1L);

        ArgumentCaptor<Message> captor = ArgumentCaptor.forClass(Message.class);
        verify(messageRepo, times(1)).save(captor.capture());
        assertTrue(captor.getValue().getContent().contains("智能助手暂时不可用"),
                "非中断的失败仍要有兜底回复");
    }

    /**
     * 连发的端到端语义：第一条还在 LLM 往返里时收到第二条 ——
     * 第一条被中断（callLlm 抛 InterruptedException → 静默退出，什么都不落），
     * 第二条正常回复。单线程池保证第二轮在第一轮退出后才开跑，
     * 所以"只落一条 round2 的回复"是确定性断言。
     */
    @Test
    @Timeout(15)
    void newerMessageInterruptsTheInFlightRound() throws Exception {
        ChatService chatService = spy(newChatService());
        when(memoryService.currentSessionKey(1L)).thenReturn(SESSION_KEY);
        User user = User.builder().id(1L).username("alice").password("secret").email("a@b.c").build();
        when(userRepository.findById(1L)).thenReturn(Optional.of(user));
        CountDownLatch round1InLlm = new CountDownLatch(1);
        CountDownLatch saved = new CountDownLatch(1);
        when(messageRepo.save(any(Message.class))).thenAnswer(inv -> {
            saved.countDown();
            return inv.getArgument(0);
        });
        doAnswer(inv -> {
            Long round = inv.getArgument(5);
            if (round != null && round == 1L) {
                round1InLlm.countDown();
                Thread.sleep(30_000); // 模拟长 LLM 往返，只有 interrupt 能提前结束
            }
            return mapper.readTree("{\"replies\":[\"round2 ok\"],\"strategy_json\":null}");
        }).when(chatService).callLlmStream(eq("alice"), eq(1L), eq(SESSION_KEY), anyString(), any(), anyLong());

        chatService.submitChat(1L, "alice", "hi");
        assertTrue(round1InLlm.await(5, TimeUnit.SECONDS), "第一轮应已进入 LLM 调用");
        chatService.submitChat(1L, "alice", "hi again");

        assertTrue(saved.await(5, TimeUnit.SECONDS), "第二轮的回复应正常落库");
        ArgumentCaptor<Message> captor = ArgumentCaptor.forClass(Message.class);
        verify(messageRepo, times(1)).save(captor.capture());
        assertEquals("round2 ok", captor.getValue().getContent(),
                "被中断的第一轮不能留下任何落库内容");
    }

    /**
     * 悬空轮占位（尾部，最常见形态）：用户问 A → 连发 B → A 的回复被中断丢弃。
     * 历史里 A 是最后一条 user（当前消息 B 在 Python 侧单独拼在历史之后），
     * 不补 assistant 占位的话模型会抢着答 A（2026-10-20 线上实测正是这个形状）。
     */
    @Test
    void danglingUserTurnAtHistoryTailGetsPlaceholder() {
        ChatService chatService = newChatService();
        when(messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(1L, List.of("CHAT_USER", "CHAT_BOT")))
                .thenReturn(List.of(
                        message("CHAT_USER", "你好"),
                        message("CHAT_USER", "现在大盘整体情况怎么样"),
                        message("CHAT_BOT", "早上好")));

        List<Map<String, String>> history = chatService.buildHistory(1L, "你好");

        assertEquals(3, history.size());
        assertEquals("assistant", history.get(0).get("role")); // 早上好
        assertEquals("user", history.get(1).get("role"));      // 大盘问题（悬空）
        assertEquals("assistant", history.get(2).get("role")); // 占位
        assertTrue(history.get(2).get("content").contains("没有回复"),
                "占位要明说这条没有回复，模型才不会当欠账去补");
    }

    /** 悬空轮占位（中部）：三连发时历史内部出现相邻 user，中间也要隔一条占位。 */
    @Test
    void danglingUserTurnInsideHistoryGetsPlaceholder() {
        ChatService chatService = newChatService();
        when(messageRepo.findTop20ByUserIdAndTypeInOrderByIdDesc(1L, List.of("CHAT_USER", "CHAT_BOT")))
                .thenReturn(List.of(
                        message("CHAT_USER", "第三条"),
                        message("CHAT_USER", "第二条"),
                        message("CHAT_USER", "第一条"),
                        message("CHAT_BOT", "开场")));

        List<Map<String, String>> history = chatService.buildHistory(1L, "第三条");

        // 开场 / 第一条 / 占位 / 第二条 / 占位（尾部）—— 永远不会出现相邻两条 user
        assertEquals(5, history.size());
        for (int i = 1; i < history.size(); i++) {
            assertFalse("user".equals(history.get(i).get("role"))
                            && "user".equals(history.get(i - 1).get("role")),
                    "历史中不允许相邻两条 user（第 " + i + " 位）");
        }
    }

    /**
     * 流式增量转发（2026-10-20）：delta 事件转成 CHAT_BOT_DELTA 推给前端；
     * 轮次被取代后增量不再转发（省流量）；result 事件照常暂存，终态裁决交给轮次门。
     */
    @Test
    void streamDeltaIsForwardedUntilSuperseded() throws Exception {
        ChatService chatService = newChatService();
        AtomicReference<JsonNode> result = new AtomicReference<>();
        AtomicBoolean anyDelta = new AtomicBoolean(false);
        chatService.currentRounds.computeIfAbsent(1L, k -> new AtomicLong()).set(3L);

        chatService.handleStreamEvent("{\"type\":\"delta\",\"text\":\"大盘\"}",
                1L, 3L, "stream-3", result, anyDelta);
        assertTrue(anyDelta.get(), "收到过增量");
        verify(sseService).pushRaw(eq(1L), argThat(json ->
                json.contains("CHAT_BOT_DELTA") && json.contains("大盘") && json.contains("stream-3")));

        // 轮次 3 已被轮次 4 取代：增量不再转发
        chatService.currentRounds.get(1L).set(4L);
        chatService.handleStreamEvent("{\"type\":\"delta\",\"text\":\"继续\"}",
                1L, 3L, "stream-3", result, anyDelta);
        verify(sseService, times(1)).pushRaw(any(), anyString());

        // result 事件不受门影响（返回值交给 runRound 的轮次门统一裁决）
        chatService.handleStreamEvent("{\"type\":\"result\",\"result\":{\"replies\":[]}}",
                1L, 3L, "stream-3", result, anyDelta);
        assertNotNull(result.get(), "result 事件应暂存为返回值");
    }
}
