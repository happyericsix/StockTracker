package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Spy;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.PageImpl;
import org.springframework.data.domain.Pageable;
import org.springframework.http.ResponseEntity;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 预警消息内部端点的口径（2026-10-16）：只回 ALERT + NEWS_ALERT 两类
 * （CHAT_* 绝不下发）、按时间新→旧、token 常量时间校验（错 token 401、
 * 未配置 token 一律拒绝——与 Python 侧 fail-closed 对齐）。
 */
@ExtendWith(MockitoExtension.class)
class InternalMessagesControllerTest {

    private static final String TOKEN = "secret-internal";

    @Mock
    private UserRepository userRepository;
    @Mock
    private MessageRepository messageRepository;

    @InjectMocks
    private InternalMessagesController controller;

    // @Value 构造注入的 token 字段在 @InjectMocks 下不会带上，测试里反射补上
    @org.junit.jupiter.api.BeforeEach
    void setUp() throws Exception {
        java.lang.reflect.Field field = InternalMessagesController.class.getDeclaredField("internalToken");
        field.setAccessible(true);
        field.set(controller, TOKEN);
    }

    private final User alice = User.builder().id(7L).username("alice")
            .password("x").email("a@b.c").build();

    @Test
    @SuppressWarnings("unchecked")
    void returnsOnlyAlertTypesNewestFirst() {
        when(userRepository.findById(7L)).thenReturn(Optional.of(alice));
        Message alert = Message.builder().type("ALERT").content("RSI 72")
                .relatedSymbol("600519").read(false).build();
        alert.setId(1L);
        Message news = Message.builder().type("NEWS_ALERT").content("【利好】…")
                .relatedSymbol("SH600418").read(true).build();
        news.setId(2L);
        when(messageRepository.findByUserIdAndTypeIn(eq(7L), any(), any(Pageable.class)))
                .thenReturn(new PageImpl<>(List.of(news, alert)));

        ResponseEntity<Map<String, Object>> resp =
                controller.alerts(7L, 10, TOKEN);
        assertEquals(200, resp.getStatusCode().value());
        List<Map<String, Object>> items = (List<Map<String, Object>>) resp.getBody().get("alerts");
        assertEquals(2, items.size());
        assertEquals("NEWS_ALERT", items.get(0).get("type"));
        assertTrue((Boolean) items.get(0).get("read"));
        assertFalse((Boolean) items.get(1).get("read"));
    }

    @Test
    void wrongTokenIsUnauthorized() {
        ResponseEntity<Map<String, Object>> resp =
                controller.alerts(7L, 10, "wrong");
        assertEquals(401, resp.getStatusCode().value());
    }

    @Test
    void unknownUserIsNotFound() {
        when(userRepository.findById(404L)).thenReturn(Optional.empty());
        ResponseEntity<Map<String, Object>> resp =
                controller.alerts(404L, 10, TOKEN);
        assertEquals(404, resp.getStatusCode().value());
    }
}
