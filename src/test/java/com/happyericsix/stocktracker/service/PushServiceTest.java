package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/**
 * 推送旁路的两种命运：**配置无效就安静地不推**；**配置有效就要推对**。
 *
 * 用本地 HttpServer 当假 Bark：能同时验证"确实发了请求"和"标题/正文的 URL 编码"——
 * 正文里有中文、斜杠、空格时编码错一点，Bark 收到的就是另一句话。
 */
@ExtendWith(MockitoExtension.class)
class PushServiceTest {

    @Mock
    private UserRepository userRepository;

    private HttpServer server;
    private final List<String> paths = new CopyOnWriteArrayList<>();
    private PushService service;

    @BeforeEach
    void startFakeBark() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            paths.add(exchange.getRequestURI().getPath());
            byte[] body = "{\"code\":200}".getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(200, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        service = new PushService(userRepository);
    }

    @AfterEach
    void stopFakeBark() {
        server.stop(0);
    }

    @Test
    void invalidConfigIsANoop() {
        assertThat(service.pushTo(null, "https://x", "t", "b")).isFalse();
        assertThat(service.pushTo("bark", " ", "t", "b")).isFalse();
        assertThat(service.pushTo("sms", "https://x", "t", "b")).isFalse(); // 未知通道
        assertThat(paths).isEmpty();
    }

    @Test
    void barkRequestCarriesUrlEncodedTitleAndBody() {
        String base = "http://127.0.0.1:" + server.getAddress().getPort() + "/ABC123";
        boolean sent = service.pushTo("bark", base + "/", "模拟盘成交：测试", "10-07 600519 买入 100 股 @ 12.5");
        assertThat(sent).isTrue();
        assertThat(paths).hasSize(1);
        // 路径形如 /ABC123/{title}/{body}；解回来必须是原句（编码往返）
        String[] parts = paths.get(0).split("/", 4);
        assertThat(URLDecoder.decode(parts[2], StandardCharsets.UTF_8)).isEqualTo("模拟盘成交：测试");
        assertThat(URLDecoder.decode(parts[3], StandardCharsets.UTF_8))
                .isEqualTo("10-07 600519 买入 100 股 @ 12.5");
    }

    @Test
    void pushByUserIsSilentlySkippedWhenNotConfigured() {
        when(userRepository.findByUsername(anyString())).thenReturn(Optional.of(
                User.builder().id(1L).username("alice").pushChannel("").build()));
        service.push("alice", "t", "b");
        assertThat(paths).isEmpty();
    }

    @Test
    void pushByUserWithBarkConfigSends() {
        when(userRepository.findByUsername(anyString())).thenReturn(Optional.of(
                User.builder().id(1L).username("alice")
                        .pushChannel("bark")
                        .pushTarget("http://127.0.0.1:" + server.getAddress().getPort() + "/k")
                        .build()));
        service.push("alice", "标题", "正文");
        assertThat(paths).hasSize(1);
    }
}
