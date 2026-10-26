package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.LlmTestClient;
import com.happyericsix.stocktracker.dto.UserLlmConfigRequest;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.entity.UserLlmConfig;
import com.happyericsix.stocktracker.exception.BusinessException;
import com.happyericsix.stocktracker.repository.UserLlmConfigRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.AesGcmCipher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * 用户自带 LLM Key（BYOK，2026-10-25）：加密保管 + 按调用解密下发。
 *
 * <p>职责边界：
 * <ul>
 *   <li><b>保管</b>：主槽（DeepSeek 预设或自定义 OpenAI 兼容）+ 单条备用，
 *       AES/GCM 加密落 {@code user_llm_configs}（复用 {@link AesGcmCipher}，
 *       密钥 {@code llm.aes.key}，未配置 fail-closed）；</li>
 *   <li><b>下发</b>：{@link #providersFor(Long)} 解密并构造 Python 契约的
 *       {@code llm_providers} 数组。所有用户触发的 AI 调用（聊天/委员会/盯盘/
 *       反思/复盘/记忆巩固）都要带上它——<b>未配置也返回空数组</b>（显式空 =
 *       用户级调用，Python 侧据此降级，绝不静默烧部署者的全局 Key）；</li>
 *   <li><b>测试</b>：转发 Python {@code /api/v1/llm/test}（先测后存，参照
 *       /user/push/test 的成熟范式）。</li>
 * </ul>
 *
 * <p>⚠️ Key 明文只存在于方法栈内：绝不进日志（只允许 {@link #mask} 后的形态）、
 * 绝不回传前端（接口只回脱敏形态）、只在 INTERNAL_API_TOKEN 保护的内网随请求体下发。
 */
@Service
public class UserLlmConfigService {

    private static final Logger log = LoggerFactory.getLogger(UserLlmConfigService.class);

    /** 与 Python llm_service.USER_LLM_CHAT_PATH_RE 同一条白名单（纵深防御，两侧都拦）。 */
    private static final Pattern CHAT_PATH_RE = Pattern.compile("^/[A-Za-z0-9._/-]*$");
    private static final String DEFAULT_CHAT_PATH = "/v1/chat/completions";

    private final UserLlmConfigRepository repository;
    private final UserRepository userRepository;
    private final LlmTestClient llmTestClient;
    private final String aesKey;

    public UserLlmConfigService(UserLlmConfigRepository repository,
                                UserRepository userRepository,
                                LlmTestClient llmTestClient,
                                @Value("${llm.aes.key:}") String aesKey) {
        this.repository = repository;
        this.userRepository = userRepository;
        this.llmTestClient = llmTestClient;
        this.aesKey = aesKey;
    }

    /** Key 保管是否启用（LLM_AES_KEY 是否配置）。未启用时前端明示"服务器未启用"。 */
    public boolean enabled() {
        return aesKey != null && !aesKey.isBlank();
    }

    private void requireEnabled() {
        if (!enabled()) {
            throw new BusinessException(503, "服务器未启用 Key 保管（需配置 LLM_AES_KEY）");
        }
    }

    /** 用户是否已配置可用的主槽。 */
    public boolean configured(Long userId) {
        return repository.findByUserId(userId)
                .map(c -> c.getPrimaryApiKeyEncrypted() != null && !c.getPrimaryApiKeyEncrypted().isBlank())
                .orElse(false);
    }

    // ==================== 状态（脱敏，绝不回传明文） ====================

    public Map<String, Object> status(Long userId) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("enabled", enabled());
        UserLlmConfig cfg = repository.findByUserId(userId).orElse(null);
        out.put("configured", cfg != null && cfg.getPrimaryApiKeyEncrypted() != null);
        out.put("primary", slotStatus(cfg, true));
        out.put("fallback", slotStatus(cfg, false));
        out.put("lastTestedAt", cfg == null ? null : cfg.getLastTestedAt());
        out.put("lastTestOk", cfg == null ? null : cfg.getLastTestOk());
        out.put("lastError", cfg == null ? null : cfg.getLastError());
        out.put("updatedAt", cfg == null ? null : cfg.getUpdatedAt());
        return out;
    }

    private Map<String, Object> slotStatus(UserLlmConfig cfg, boolean primary) {
        boolean present = cfg != null && (primary
                ? cfg.getPrimaryApiKeyEncrypted() != null && !cfg.getPrimaryApiKeyEncrypted().isBlank()
                : cfg.getFallbackApiKeyEncrypted() != null && !cfg.getFallbackApiKeyEncrypted().isBlank());
        if (!present) {
            return null;
        }
        Map<String, Object> slot = new LinkedHashMap<>();
        if (primary) {
            slot.put("baseUrl", blankToNull(cfg.getPrimaryBaseUrl()));
            slot.put("preset", isBlank(cfg.getPrimaryBaseUrl()));   // 空 = DeepSeek 预设
            slot.put("model", blankToNull(cfg.getPrimaryModel()));
        } else {
            slot.put("baseUrl", blankToNull(cfg.getFallbackBaseUrl()));
            slot.put("model", blankToNull(cfg.getFallbackModel()));
            slot.put("chatPath", isBlank(cfg.getFallbackChatPath())
                    ? DEFAULT_CHAT_PATH : cfg.getFallbackChatPath());
        }
        slot.put("maskedKey", mask(decryptQuietly(primary
                ? cfg.getPrimaryApiKeyEncrypted() : cfg.getFallbackApiKeyEncrypted())));
        return slot;
    }

    // ==================== 保存（留空 Key = 保留旧值） ====================

    public Map<String, Object> save(Long userId, UserLlmConfigRequest request) {
        requireEnabled();
        if (request == null || request.getPrimary() == null) {
            throw new BusinessException(400, "主槽必须配置（primary 不能为空）");
        }
        UserLlmConfig cfg = repository.findByUserId(userId).orElse(null);
        boolean hasSavedPrimary = cfg != null && !isBlank(cfg.getPrimaryApiKeyEncrypted());
        boolean hasSavedFallback = cfg != null && !isBlank(cfg.getFallbackApiKeyEncrypted());

        UserLlmConfigRequest.Slot primary = request.getPrimary();
        String primaryKey = normalizeKey(primary.getApiKey(), hasSavedPrimary,
                cfg == null ? null : cfg.getPrimaryApiKeyEncrypted(), "主槽");
        String primaryBaseUrl = normalizeBaseUrl(primary.getBaseUrl(), "主槽");
        String primaryModel = normalizeModel(primary.getModel(), "主槽");
        // 自定义主槽必须写模型（模型名是供应商私有的，预设留空才默认 deepseek-chat）
        if (!isBlank(primaryBaseUrl) && isBlank(primaryModel)) {
            throw new BusinessException(400, "自定义端点的主槽必须填写模型名");
        }
        String primaryChatPath = normalizeChatPath(primary.getChatPath());

        String fallbackKey = null;
        String fallbackBaseUrl = null;
        String fallbackModel = null;
        String fallbackChatPath = null;
        if (request.getFallback() != null) {
            UserLlmConfigRequest.Slot fallback = request.getFallback();
            fallbackBaseUrl = normalizeBaseUrl(fallback.getBaseUrl(), "备用槽");
            fallbackModel = normalizeModel(fallback.getModel(), "备用槽");
            if (isBlank(fallbackBaseUrl) || isBlank(fallbackModel)) {
                throw new BusinessException(400, "备用槽的 base_url 与模型名必须配齐");
            }
            fallbackKey = normalizeKey(fallback.getApiKey(), hasSavedFallback,
                    cfg == null ? null : cfg.getFallbackApiKeyEncrypted(), "备用槽");
            fallbackChatPath = normalizeChatPath(fallback.getChatPath());
        }

        if (cfg == null) {
            User user = userRepository.findById(userId)
                    .orElseThrow(() -> new BusinessException(404, "用户不存在"));
            cfg = UserLlmConfig.builder().user(user).build();
        }
        cfg.setPrimaryApiKeyEncrypted(primaryKey);
        cfg.setPrimaryBaseUrl(primaryBaseUrl);
        cfg.setPrimaryModel(primaryModel);
        cfg.setPrimaryChatPath(primaryChatPath);
        cfg.setFallbackApiKeyEncrypted(fallbackKey);
        cfg.setFallbackBaseUrl(fallbackBaseUrl);
        cfg.setFallbackModel(fallbackModel);
        cfg.setFallbackChatPath(fallbackChatPath);
        // 配置已变，旧的测试结论作废（避免"改了 Key 还显示上次通过"的误导）
        cfg.setLastTestedAt(null);
        cfg.setLastTestOk(null);
        cfg.setLastError(null);
        cfg.setUpdatedAt(LocalDateTime.now());
        repository.save(cfg);
        return status(userId);
    }

    /** 留空 = 保留已存 Key（与 pushTarget 同一语义）；首次保存必须填。 */
    private String normalizeKey(String raw, boolean hasSaved, String savedEncrypted, String slotLabel) {
        String key = raw == null ? "" : raw.trim();
        if (!key.isEmpty()) {
            return AesGcmCipher.encrypt(key, aesKey);
        }
        if (hasSaved && savedEncrypted != null) {
            return savedEncrypted;
        }
        throw new BusinessException(400, slotLabel + "的 API Key 必填（留空仅用于保留已存 Key）");
    }

    private String normalizeBaseUrl(String raw, String slotLabel) {
        String url = raw == null ? "" : raw.trim();
        if (url.isEmpty()) {
            return null;
        }
        if (url.endsWith("/")) {
            url = url.substring(0, url.length() - 1);
        }
        if (url.length() > 500) {
            throw new BusinessException(400, slotLabel + "的 base_url 过长");
        }
        if (!url.startsWith("https://")
                && !url.startsWith("http://localhost") && !url.startsWith("http://127.0.0.1")) {
            throw new BusinessException(400, slotLabel + "的 base_url 必须是 https（本机调试除外）");
        }
        return url;
    }

    private String normalizeModel(String raw, String slotLabel) {
        String model = raw == null ? "" : raw.trim();
        if (model.length() > 200) {
            throw new BusinessException(400, slotLabel + "的模型名过长");
        }
        return model.isEmpty() ? null : model;
    }

    private String normalizeChatPath(String raw) {
        String path = raw == null || raw.isBlank() ? DEFAULT_CHAT_PATH : raw.trim();
        if (!CHAT_PATH_RE.matcher(path).matches()) {
            throw new BusinessException(400, "chat_path 只能是 / 开头的路径（字母/数字/._/-）");
        }
        if (path.length() > 200) {
            throw new BusinessException(400, "chat_path 过长");
        }
        // 默认值不落库（null = Python 侧默认），减少一列无意义的冗余
        return DEFAULT_CHAT_PATH.equals(path) ? null : path;
    }

    // ==================== 下发（Python 契约：llm_providers） ====================

    /**
     * 构造发给 Python 的 {@code llm_providers}。
     *
     * <p><b>永远返回非 null</b>：未配置/解密失败时返回空列表——显式空数组正是
     * "用户级调用但没配 Key"的契约标记（Python 侧据此降级，绝不回落 env Key）。
     * 新闻解读等系统级调用则完全不带这个字段（走 env），见各 Client。
     */
    public List<Map<String, Object>> providersFor(Long userId) {
        UserLlmConfig cfg = repository.findByUserId(userId).orElse(null);
        if (cfg == null || isBlank(cfg.getPrimaryApiKeyEncrypted())) {
            return new ArrayList<>();
        }
        // 主槽 key 解密失败（LLM_AES_KEY 被轮换/密文损坏）→ 按未配置降级
        //（用户侧表现为"请在个人中心重新配置"，比打挂聊天链路好得多）
        String primaryKey = decryptQuietly(cfg.getPrimaryApiKeyEncrypted());
        if (isBlank(primaryKey)) {
            return new ArrayList<>();
        }
        List<Map<String, Object>> providers = new ArrayList<>();
        Map<String, Object> primary = new HashMap<>();
        primary.put("name", "primary");
        primary.put("api_key", primaryKey);
        if (!isBlank(cfg.getPrimaryBaseUrl())) {
            primary.put("base_url", cfg.getPrimaryBaseUrl());
        }
        if (!isBlank(cfg.getPrimaryModel())) {
            primary.put("model", cfg.getPrimaryModel());
        }
        if (!isBlank(cfg.getPrimaryChatPath())) {
            primary.put("chat_path", cfg.getPrimaryChatPath());
        }
        providers.add(primary);

        if (!isBlank(cfg.getFallbackApiKeyEncrypted())
                && !isBlank(cfg.getFallbackBaseUrl()) && !isBlank(cfg.getFallbackModel())) {
            String fallbackKey = decryptQuietly(cfg.getFallbackApiKeyEncrypted());
            if (!isBlank(fallbackKey)) {
                Map<String, Object> fallback = new HashMap<>();
                fallback.put("name", "fallback");
                fallback.put("api_key", fallbackKey);
                fallback.put("base_url", cfg.getFallbackBaseUrl());
                fallback.put("model", cfg.getFallbackModel());
                if (!isBlank(cfg.getFallbackChatPath())) {
                    fallback.put("chat_path", cfg.getFallbackChatPath());
                }
                providers.add(fallback);
            }
        }
        return providers;
    }

    // ==================== 测试（先测后存，参照 /user/push/test） ====================

    /**
     * 探测连通性。form 非 null = 测表单值（不落库、不动 lastTest*）；
     * form 为 null = 复测已存配置（结果写回 lastTest*）。
     */
    public Map<String, Object> test(Long userId, UserLlmConfigRequest form) {
        requireEnabled();
        List<Map<String, Object>> providers;
        if (form != null && form.getPrimary() != null) {
            providers = formProviders(userId, form);
        } else {
            providers = providersFor(userId);
            if (providers.isEmpty()) {
                throw new BusinessException(400, "还没有可测试的 Key：先在上方填写并保存");
            }
        }
        JsonNode raw = llmTestClient.test(userId, providers);
        List<Map<String, Object>> results = new ArrayList<>();
        if (raw == null || !raw.has("results") || !raw.get("results").isArray()) {
            throw new BusinessException(502, "探测服务无响应，请稍后重试");
        }
        boolean allOk = true;
        for (JsonNode item : raw.get("results")) {
            String name = item.path("name").asText("");
            String slot = name.endsWith(":primary") ? "primary"
                    : name.endsWith(":fallback") ? "fallback" : name;
            Map<String, Object> r = new LinkedHashMap<>();
            r.put("slot", slot);
            r.put("ok", item.path("ok").asBoolean(false));
            r.put("latencyMs", item.path("latency_ms").asInt(0));
            r.put("model", item.path("model").asText(""));
            if (item.hasNonNull("error")) {
                r.put("error", item.path("error").asText());
            }
            allOk = allOk && item.path("ok").asBoolean(false);
            results.add(r);
        }

        // 复测已存配置才落结论；表单测试的结论在用户点"保存"时作废重来
        if (form == null) {
            final boolean allOkFinal = allOk;
            repository.findByUserId(userId).ifPresent(cfg -> {
                cfg.setLastTestedAt(LocalDateTime.now());
                cfg.setLastTestOk(allOkFinal);
                cfg.setLastError(allOkFinal ? null
                        : results.stream().filter(r -> !Boolean.TRUE.equals(r.get("ok")))
                                .map(r -> String.valueOf(r.get("error")))
                                .filter(s -> s != null && !s.isBlank())
                                .findFirst().orElse("探测失败"));
                repository.save(cfg);
            });
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("results", results);
        out.put("allOk", allOk);
        return out;
    }

    /** 测表单值：留空 Key 用已存 Key 补齐，校验失败如实报错（不落库）。 */
    private List<Map<String, Object>> formProviders(Long userId, UserLlmConfigRequest form) {
        UserLlmConfig cfg = repository.findByUserId(userId).orElse(null);
        UserLlmConfigRequest.Slot primary = form.getPrimary();
        String primaryKey = primary.getApiKey() == null || primary.getApiKey().isBlank()
                ? (cfg == null ? null : decryptQuietly(cfg.getPrimaryApiKeyEncrypted()))
                : primary.getApiKey().trim();
        if (primaryKey == null || primaryKey.isBlank()) {
            throw new BusinessException(400, "主槽的 API Key 必填（或先保存过再留空）");
        }
        String baseUrl = normalizeBaseUrl(primary.getBaseUrl(), "主槽");
        String model = normalizeModel(primary.getModel(), "主槽");
        if (baseUrl != null && model == null) {
            throw new BusinessException(400, "自定义端点的主槽必须填写模型名");
        }

        List<Map<String, Object>> providers = new ArrayList<>();
        Map<String, Object> p = new HashMap<>();
        p.put("name", "primary");
        p.put("api_key", primaryKey);
        if (baseUrl != null) {
            p.put("base_url", baseUrl);
        }
        if (model != null) {
            p.put("model", model);
        }
        String chatPath = normalizeChatPath(primary.getChatPath());
        if (chatPath != null) {
            p.put("chat_path", chatPath);
        }
        providers.add(p);

        if (form.getFallback() != null) {
            UserLlmConfigRequest.Slot fallback = form.getFallback();
            String fbBaseUrl = normalizeBaseUrl(fallback.getBaseUrl(), "备用槽");
            String fbModel = normalizeModel(fallback.getModel(), "备用槽");
            if (fbBaseUrl == null || fbModel == null) {
                throw new BusinessException(400, "备用槽的 base_url 与模型名必须配齐");
            }
            String fbKey = fallback.getApiKey() == null || fallback.getApiKey().isBlank()
                    ? (cfg == null ? null : decryptQuietly(cfg.getFallbackApiKeyEncrypted()))
                    : fallback.getApiKey().trim();
            if (fbKey == null || fbKey.isBlank()) {
                throw new BusinessException(400, "备用槽的 API Key 必填（或先保存过再留空）");
            }
            Map<String, Object> f = new HashMap<>();
            f.put("name", "fallback");
            f.put("api_key", fbKey);
            f.put("base_url", fbBaseUrl);
            f.put("model", fbModel);
            String fbChatPath = normalizeChatPath(fallback.getChatPath());
            if (fbChatPath != null) {
                f.put("chat_path", fbChatPath);
            }
            providers.add(f);
        }
        return providers;
    }

    // ==================== 清除 ====================

    public void clear(Long userId) {
        repository.deleteByUserId(userId);
    }

    // ==================== 工具 ====================

    /**
     * 解密。失败不抛（密钥被轮换/密文损坏时，降级为"未配置"而不是打挂聊天链路），
     * 但必须留下 warn 日志——静默解密失败 = 所有用户 AI 突然降级且无人知道为什么。
     */
    private String decryptQuietly(String encrypted) {
        if (isBlank(encrypted)) {
            return null;
        }
        try {
            return AesGcmCipher.decrypt(encrypted, aesKey);
        } catch (Exception e) {
            log.warn("用户 LLM Key 解密失败（LLM_AES_KEY 是否被轮换？）: {}", e.getMessage());
            return null;
        }
    }

    /** 展示/日志专用脱敏：sk-abc***wxyz。 */
    static String mask(String key) {
        if (key == null || key.isBlank()) {
            return null;
        }
        if (key.length() <= 8) {
            return "***";
        }
        return key.substring(0, 3) + "***" + key.substring(key.length() - 4);
    }

    private static boolean isBlank(String s) {
        return s == null || s.isBlank();
    }

    private static String blankToNull(String s) {
        return isBlank(s) ? null : s;
    }
}
