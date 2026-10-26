package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.LlmTestClient;
import com.happyericsix.stocktracker.dto.UserLlmConfigRequest;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.entity.UserLlmConfig;
import com.happyericsix.stocktracker.exception.BusinessException;
import com.happyericsix.stocktracker.repository.UserLlmConfigRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 用户自带 Key（BYOK）保管语义的单测：加密落库、留空保留、校验拒绝、
 * providersFor 的"未配置 = 空数组"契约（那是 Python 侧区分用户级调用的唯一标记）。
 */
@ExtendWith(MockitoExtension.class)
class UserLlmConfigServiceTest {

    private static final Long USER_ID = 7L;
    private static final String AES_KEY = "unit-test-aes-key";

    @Mock
    private UserLlmConfigRepository repository;
    @Mock
    private UserRepository userRepository;
    @Mock
    private LlmTestClient llmTestClient;

    private UserLlmConfigService service;
    private final ObjectMapper mapper = new ObjectMapper();

    @BeforeEach
    void setUp() {
        service = new UserLlmConfigService(repository, userRepository, llmTestClient, AES_KEY);
    }

    private UserLlmConfigRequest.Slot slot(String apiKey, String baseUrl, String model) {
        UserLlmConfigRequest.Slot s = new UserLlmConfigRequest.Slot();
        s.setApiKey(apiKey);
        s.setBaseUrl(baseUrl);
        s.setModel(model);
        return s;
    }

    private UserLlmConfigRequest request(UserLlmConfigRequest.Slot primary,
                                         UserLlmConfigRequest.Slot fallback) {
        UserLlmConfigRequest r = new UserLlmConfigRequest();
        r.setPrimary(primary);
        r.setFallback(fallback);
        return r;
    }

    // ===== 保存 → 加密落库 → 状态脱敏 =====

    @Test
    void saveEncryptsKeyAndStatusNeverReturnsPlaintext() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        when(userRepository.findById(USER_ID)).thenReturn(Optional.of(new User()));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        service.save(USER_ID, request(slot("sk-my-secret-key-123456", null, null), null));

        ArgumentCaptor<UserLlmConfig> captor = ArgumentCaptor.forClass(UserLlmConfig.class);
        verify(repository).save(captor.capture());
        UserLlmConfig storedCfg = captor.getValue();
        String stored = storedCfg.getPrimaryApiKeyEncrypted();
        // GCM 每次随机 IV：密文必然不同于明文，且可被 AesGcmCipher 解回原文
        assertThat(stored).isNotEqualTo("sk-my-secret-key-123456");
        assertThat(com.happyericsix.stocktracker.util.AesGcmCipher.decrypt(stored, AES_KEY))
                .isEqualTo("sk-my-secret-key-123456");
        assertThat(storedCfg.getPrimaryBaseUrl()).isNull();   // 空 = DeepSeek 预设

        // 状态只回脱敏形态：明文绝不出现（让 find 桩回放刚保存的实体）
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.of(storedCfg));
        Map<String, Object> status = service.status(USER_ID);
        String json = mapper.writeValueAsString(status);
        assertThat(json).doesNotContain("sk-my-secret-key-123456");
        @SuppressWarnings("unchecked")
        Map<String, Object> primary = (Map<String, Object>) status.get("primary");
        assertThat((String) primary.get("maskedKey")).startsWith("sk-").contains("***");
        assertThat(status.get("configured")).isEqualTo(true);
    }

    @Test
    void blankApiKeyKeepsStoredKeyButUpdatesOtherFields() {
        // GCM 随机 IV：同明文两次加密的密文不同，所以密文只算一次、全程复用
        String savedEncrypted = serviceEncrypt("sk-old-key-abcdefgh");
        UserLlmConfig saved = UserLlmConfig.builder()
                .primaryApiKeyEncrypted(savedEncrypted)
                .primaryBaseUrl(null).primaryModel(null)
                .build();
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.of(saved));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        service.save(USER_ID, request(slot("", "https://api.siliconflow.cn",
                "Qwen/Qwen2.5-72B-Instruct"), null));

        // 留空 = 保留旧 Key（与 pushTarget 同一语义，密文逐字节不变），其余字段按新值更新
        assertThat(saved.getPrimaryApiKeyEncrypted()).isEqualTo(savedEncrypted);
        assertThat(saved.getPrimaryBaseUrl()).isEqualTo("https://api.siliconflow.cn");
        assertThat(saved.getPrimaryModel()).isEqualTo("Qwen/Qwen2.5-72B-Instruct");
    }

    private String serviceEncrypt(String plaintext) {
        // 与被测服务同一套加密（直接借 AesGcmCipher 造密文，避免暴露私有助手）
        return com.happyericsix.stocktracker.util.AesGcmCipher.encrypt(plaintext, AES_KEY);
    }

    // ===== 校验拒绝 =====

    @Test
    void firstSaveWithoutPrimaryKeyIsRejected() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.save(USER_ID, request(slot("", null, null), null)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("API Key 必填");
    }

    @Test
    void fallbackSlotRequiresBaseUrlAndModel() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.save(USER_ID,
                request(slot("sk-p", null, null), slot("sk-f", "", "glm-4.6"))))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("配齐");
    }

    @Test
    void plainHttpBaseUrlIsRejected() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.save(USER_ID,
                request(slot("sk-p", "http://api.example.com", "m"), null)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("https");
    }

    @Test
    void localhostHttpIsAllowedForDev() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        when(userRepository.findById(USER_ID)).thenReturn(Optional.of(new User()));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));
        service.save(USER_ID, request(slot("sk-p", "http://localhost:8001", "m"), null));
        // 不抛即通过
    }

    @Test
    void customPrimaryWithoutModelIsRejected() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.save(USER_ID,
                request(slot("sk-p", "https://api.siliconflow.cn", ""), null)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("模型名");
    }

    @Test
    void badChatPathIsRejected() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        UserLlmConfigRequest.Slot fb = slot("sk-f", "https://api.siliconflow.cn", "m");
        fb.setChatPath("../etc/passwd");
        assertThatThrownBy(() -> service.save(USER_ID, request(slot("sk-p", null, null), fb)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("chat_path");
    }

    @Test
    void missingPrimaryKeyOnNullFormRejected() {
        assertThatThrownBy(() -> service.save(USER_ID, request(null, null)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("主槽");
    }

    // ===== fail-closed：LLM_AES_KEY 未配置 =====

    @Test
    void disabledWhenAesKeyBlank() {
        UserLlmConfigService disabled =
                new UserLlmConfigService(repository, userRepository, llmTestClient, "");
        assertThat(disabled.enabled()).isFalse();
        assertThatThrownBy(() -> disabled.save(USER_ID, request(slot("sk", null, null), null)))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("LLM_AES_KEY");
    }

    // ===== providersFor：Python 契约 =====

    @Test
    void providersForUnconfiguredUserReturnsEmptyListNotNull() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        // 空数组（而非 null/缺字段）= 用户级调用但没配 Key 的契约标记
        assertThat(service.providersFor(USER_ID)).isEmpty();
    }

    @Test
    void providersForDecryptsBothSlots() {
        UserLlmConfig cfg = UserLlmConfig.builder()
                .primaryApiKeyEncrypted(serviceEncrypt("sk-primary"))
                .primaryBaseUrl(null).primaryModel(null)
                .fallbackApiKeyEncrypted(serviceEncrypt("sk-fallback"))
                .fallbackBaseUrl("https://api.siliconflow.cn")
                .fallbackModel("Qwen/Qwen2.5-72B-Instruct")
                .fallbackChatPath(null)
                .build();
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.of(cfg));

        List<Map<String, Object>> providers = service.providersFor(USER_ID);

        assertThat(providers).hasSize(2);
        assertThat(providers.get(0))
                .containsEntry("name", "primary")
                .containsEntry("api_key", "sk-primary");
        assertThat(providers.get(0)).doesNotContainKey("base_url");   // 预设不带，Python 用默认
        assertThat(providers.get(1))
                .containsEntry("name", "fallback")
                .containsEntry("api_key", "sk-fallback")
                .containsEntry("base_url", "https://api.siliconflow.cn")
                .containsEntry("model", "Qwen/Qwen2.5-72B-Instruct");
    }

    @Test
    void providersForDegradesToEmptyWhenDecryptionFails() {
        // LLM_AES_KEY 被轮换 → 解密失败 → 按未配置降级（而不是把 null key 发给 Python）
        UserLlmConfig cfg = UserLlmConfig.builder()
                .primaryApiKeyEncrypted("not-valid-ciphertext")
                .build();
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.of(cfg));
        assertThat(service.providersFor(USER_ID)).isEmpty();
    }

    // ===== 脱敏 =====

    @Test
    void maskNeverRevealsMiddle() {
        assertThat(UserLlmConfigService.mask("sk-abcdefghij")).isEqualTo("sk-***ghij");
        assertThat(UserLlmConfigService.mask("short")).isEqualTo("***");
        assertThat(UserLlmConfigService.mask(null)).isNull();
    }

    // ===== 测试（探测）=====

    @Test
    void testSavedConfigUpdatesLastTestFields() {
        UserLlmConfig cfg = UserLlmConfig.builder()
                .primaryApiKeyEncrypted(serviceEncrypt("sk-primary"))
                .build();
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.of(cfg));
        when(llmTestClient.test(any(), any())).thenReturn(mapper.readTree(
                "{\"results\":[{\"name\":\"u7:primary\",\"ok\":true,\"latency_ms\":120,"
                        + "\"model\":\"deepseek-chat\"}]}"));
        when(repository.save(any())).thenAnswer(inv -> inv.getArgument(0));

        Map<String, Object> out = service.test(USER_ID, null);

        assertThat(out.get("allOk")).isEqualTo(true);
        assertThat(cfg.getLastTestOk()).isTrue();
        assertThat(cfg.getLastTestedAt()).isNotNull();
        @SuppressWarnings("unchecked")
        List<Map<String, Object>> results = (List<Map<String, Object>>) out.get("results");
        assertThat(results.get(0)).containsEntry("slot", "primary");
    }

    @Test
    void testWithoutAnyConfigIsRejected() {
        when(repository.findByUserId(USER_ID)).thenReturn(Optional.empty());
        assertThatThrownBy(() -> service.test(USER_ID, null))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("先");
    }
}
