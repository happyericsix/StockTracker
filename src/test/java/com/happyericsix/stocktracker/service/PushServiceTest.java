package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PushLog;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PushLogRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

/**
 * 推送旁路的三种命运：**配置无效就安静地不推（也不留日志）**；**配置有效就要推对**；
 * **服务商拒绝要诚实返回 false**（2026-10-15 前 non-2xx 也回 true，用户等一个
 * 永远不响的手机——这条纪律由这里的用例钉住）。
 *
 * 用本地 HttpServer 当假 Bark：能同时验证"确实发了请求"和"标题/正文的 URL 编码"——
 * 正文里有中文、斜杠、空格时编码错一点，Bark 收到的就是另一句话。
 * Server酱的地址写死在生产域名上打不了本地假服务，成败判据走 verdictOf 直测。
 */
@ExtendWith(MockitoExtension.class)
class PushServiceTest {

    @Mock
    private UserRepository userRepository;

    @Mock
    private PushLogRepository pushLogRepository;

    private HttpServer server;
    private final List<String> paths = new CopyOnWriteArrayList<>();
    private volatile int respondStatus = 200;
    private volatile String respondBody = "{\"code\":200}";
    private PushService service;

    @BeforeEach
    void startFakeBark() throws IOException {
        server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/", exchange -> {
            paths.add(exchange.getRequestURI().getPath());
            byte[] body = respondBody.getBytes(StandardCharsets.UTF_8);
            exchange.sendResponseHeaders(respondStatus, body.length);
            exchange.getResponseBody().write(body);
            exchange.close();
        });
        server.start();
        // barkAllowPrivate=true：下面这些用例考察"发出去的请求与编码对不对"，
        // 假 Bark 就架在 127.0.0.1 上——SSRF 栏的拒绝行为由专门用例钉（默认 false）。
        service = new PushService(userRepository, pushLogRepository, new ObjectMapper(), true);
    }

    @AfterEach
    void stopFakeBark() {
        server.stop(0);
    }

    private String barkBase() {
        return "http://127.0.0.1:" + server.getAddress().getPort() + "/ABC123";
    }

    private void stubUser(String channel, String target) {
        when(userRepository.findByUsername(anyString())).thenReturn(Optional.of(
                User.builder().id(7L).username("alice")
                        .pushChannel(channel == null ? "" : channel)
                        .pushTarget(target)
                        .build()));
    }

    @Test
    void invalidConfigIsANoopWithoutLogRows() {
        stubUser(null, null);
        assertThat(service.pushTest("alice", null, "https://x", "t", "b")).isFalse();
        assertThat(service.pushTest("alice", "bark", " ", "t", "b")).isFalse();
        assertThat(service.pushTest("alice", "sms", "https://x", "t", "b")).isFalse(); // 未知通道
        assertThat(paths).isEmpty();
        // "没推"不是"推失败"：日志里只该有真实发出去的尝试
        verifyNoInteractions(pushLogRepository);
    }

    @Test
    void barkRequestCarriesUrlEncodedTitleAndBody() {
        stubUser("bark", barkBase() + "/");
        boolean sent = service.pushTest("alice", "bark", barkBase() + "/",
                "模拟盘成交：测试", "10-07 600519 买入 100 股 @ 12.5");
        assertThat(sent).isTrue();
        assertThat(paths).hasSize(1);
        // 路径形如 /ABC123/{title}/{body}；解回来必须是原句（编码往返）
        String[] parts = paths.get(0).split("/", 4);
        assertThat(URLDecoder.decode(parts[2], StandardCharsets.UTF_8)).isEqualTo("模拟盘成交：测试");
        assertThat(URLDecoder.decode(parts[3], StandardCharsets.UTF_8))
                .isEqualTo("10-07 600519 买入 100 股 @ 12.5");
    }

    /** 修的缺陷：Bark 回非 2xx 时必须返回 false（此前照样回 true），且日志行记录失败。 */
    @Test
    void barkNon2xxIsAnHonestFailure() {
        respondStatus = 500;
        respondBody = "{\"code\":500,\"message\":\"server error\"}";
        stubUser("bark", barkBase());
        assertThat(service.pushTest("alice", "bark", barkBase(), "t", "b")).isFalse();

        ArgumentCaptor<PushLog> captor = ArgumentCaptor.forClass(PushLog.class);
        verify(pushLogRepository).save(captor.capture());
        PushLog row = captor.getValue();
        assertThat(row.getUserId()).isEqualTo(7L);
        assertThat(row.getChannel()).isEqualTo("bark");
        assertThat(row.isOk()).isFalse();
        assertThat(row.getError()).contains("HTTP 500");
    }

    /** 成功的推送同样留痕：Profile 的"最近推送状态"靠这些行回答"有没有推出去"。 */
    @Test
    void successfulPushIsLoggedAsOk() {
        stubUser("bark", barkBase());
        service.push("alice", "标题", "正文");
        assertThat(paths).hasSize(1);

        ArgumentCaptor<PushLog> captor = ArgumentCaptor.forClass(PushLog.class);
        verify(pushLogRepository).save(captor.capture());
        PushLog row = captor.getValue();
        assertThat(row.isOk()).isTrue();
        assertThat(row.getError()).isNull();
        assertThat(row.getTitle()).isEqualTo("标题");
    }

    @Test
    void pushByUserIsSilentlySkippedWhenNotConfigured() {
        stubUser("", null);
        service.push("alice", "t", "b");
        assertThat(paths).isEmpty();
        verifyNoInteractions(pushLogRepository);
    }

    // ==================== 成败判据（verdictOf 直测，覆盖 Server酱） ====================

    @Test
    void serverChanSuccessIsHttp200AndCodeZero() {
        assertThat(service.verdictOf("serverchan", 200,
                "{\"code\":0,\"message\":\"\",\"data\":{\"pushid\":\"x\"}}")).isNull();
    }

    /** SendKey 错误时 Server酱回 HTTP 200 + code!=0——只看 HTTP 会把失败当成已送达。 */
    @Test
    void serverChanHttp200WithBusinessErrorCodeIsFailure() {
        String verdict = service.verdictOf("serverchan", 200,
                "{\"code\":40001,\"message\":\"SendKey 错误\"}");
        assertThat(verdict).contains("40001").contains("SendKey 错误");
    }

    @Test
    void anyChannelNon2xxIsFailure() {
        assertThat(service.verdictOf("serverchan", 401, "unauthorized")).contains("HTTP 401");
        assertThat(service.verdictOf("bark", 502, "bad gateway")).contains("HTTP 502");
    }

    @Test
    void barkAcceptsCode200AndRejectsOtherCodes() {
        assertThat(service.verdictOf("bark", 200, "{\"code\":200}")).isNull();
        assertThat(service.verdictOf("bark", 200, "{\"code\":400,\"message\":\"bad request\"}"))
                .contains("Bark code=400");
        // 2xx 但响应体不是 JSON（某些反代改写）：只认 HTTP 状态，不误杀
        assertThat(service.verdictOf("bark", 200, "<html>ok</html>")).isNull();
    }

    /** 推送日志写不进不许影响推送本身（旁路的旁路）。 */
    @Test
    void logWriteFailureDoesNotAffectPushResult() {
        stubUser("bark", barkBase());
        when(pushLogRepository.save(any(PushLog.class)))
                .thenThrow(new RuntimeException("db down"));
        assertThat(service.pushTest("alice", "bark", barkBase(), "标题", "正文")).isTrue();
        assertThat(paths).hasSize(1);
    }

    // ==================== Bark SSRF 栏（2026-10-15 安全测试 L1） ====================

    /** 默认（barkAllowPrivate=false）：环回/内网目标必须拒绝，原因要能读回给用户。 */
    @Test
    void barkGuardRejectsPrivateTargetsByDefault() {
        PushService guard = new PushService(userRepository, pushLogRepository,
                new ObjectMapper(), false);
        assertThat(guard.validateBarkServer("http://127.0.0.1:8080/x")).contains("内网");
        assertThat(guard.validateBarkServer("http://localhost:8080/x")).contains("内网");
        assertThat(guard.validateBarkServer("http://192.168.1.10/x")).contains("内网");
        assertThat(guard.validateBarkServer("http://10.0.0.5/x")).contains("内网");
        assertThat(guard.validateBarkServer("http://169.254.169.254/meta")).contains("内网");
        // scheme 栏与主机栏
        assertThat(guard.validateBarkServer("file:///etc/passwd")).contains("http");
        assertThat(guard.validateBarkServer("not a url")).isNotNull();
        // 拒绝时不能真的发请求（paths 空），且留下可见的失败痕迹（推送日志行）
        stubUser("bark", "http://127.0.0.1:9/x");
        assertThat(guard.pushTest("alice", "bark", "http://127.0.0.1:9/x", "t", "b")).isFalse();
        assertThat(paths).isEmpty();
        verify(pushLogRepository).save(any(PushLog.class));
    }

    /** 显式放行（自托管 Bark 在局域网）：地址栏跳过，但 scheme 栏仍然生效。 */
    @Test
    void barkGuardAllowsPrivateWhenExplicitlyConfigured() {
        PushService guard = new PushService(userRepository, pushLogRepository,
                new ObjectMapper(), true);
        assertThat(guard.validateBarkServer("http://127.0.0.1:8080/x")).isNull();
        assertThat(guard.validateBarkServer("file:///etc/passwd")).contains("http");
    }
}
