package com.happyericsix.stocktracker.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 用户自带的 LLM Key（BYOK，2026-10-25）：主槽 + 单条备用。
 *
 * <p>背景：此前所有用户触发的 AI 调用烧的都是部署者的 {@code DEEPSEEK_API_KEY}
 * （开放注册 = 陌生人花你的钱，{@code AUTH_REGISTRATION_ENABLED} 开关就是为此而生）。
 * 从这张表起，用户级调用（聊天/交易委员会/盘中盯盘/反思/复盘/记忆巩固）一律用
 * 用户自己配置的 Key——由 {@code service/UserLlmConfigService} 解密后随请求体
 * 下发给 Python（{@code llm_providers} 字段，契约见 llm_service.user_providers）。
 *
 * <p>槽位语义（与 Python 侧对齐）：
 * <ul>
 *   <li><b>主槽</b>：{@code primaryBaseUrl} 留空 = DeepSeek 官方预设
 *       （{@code https://api.deepseek.com} + {@code deepseek-chat}）；
 *       非空 = 任意 OpenAI 兼容端点，此时 model 必填；</li>
 *   <li><b>备用槽</b>：可选。任意 OpenAI 兼容端点（硅基流动/Moonshot/智谱…），
 *       baseUrl/model/key 三件配齐才生效——主槽持续过载冷却 10 分钟时自动切过来，
 *       恢复后切回（复用 Python 侧现成的供应商冷却/切换机制）。</li>
 * </ul>
 *
 * <p>⚠️ 敏感数据纪律（与 {@link ThsBinding} 同一套）：
 * <ul>
 *   <li>两个 key 字段 AES/GCM 加密存储且 {@code @JsonIgnore}——绝不随实体序列化出网；</li>
 *   <li>加密密钥走配置项 {@code llm.aes.key}（环境变量 {@code LLM_AES_KEY}），
 *       未配置时 fail-closed：保管功能不可用但应用可启动；</li>
 *   <li>明文只在 Service 方法栈内存在：绝不进日志（日志只允许 {@code mask()} 后的形态）、
 *       绝不回传前端（接口只回脱敏形态）、只在 Java↔Python 内网随请求体下发。</li>
 * </ul>
 */
@Entity
@Table(name = "user_llm_configs", uniqueConstraints = {
        @UniqueConstraint(columnNames = {"user_id"})
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class UserLlmConfig {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User user;

    /**
     * AES/GCM 加密后的主槽 API Key（格式 base64(iv + ciphertext + tag)）。
     * 主槽约定：DeepSeek 预设（只填 key）或自定义 OpenAI 兼容端点（key + baseUrl + model）。
     */
    @JsonIgnore
    @Column(nullable = false, length = 4096)
    private String primaryApiKeyEncrypted;

    /** 主槽 base_url；空 = DeepSeek 官方预设。非空时必须是 https（本机调试除外）。 */
    @Column(length = 500)
    private String primaryBaseUrl;

    /** 主槽模型名；空 = deepseek-chat（仅预设时可空，自定义端点必填）。 */
    @Column(length = 200)
    private String primaryModel;

    /** 主槽 chat/completions 路径后缀；空 = /v1/chat/completions（智谱等端点需覆盖）。 */
    @Column(length = 200)
    private String primaryChatPath;

    /** AES/GCM 加密后的备用槽 API Key。备用槽整体可选：三件配齐才生效。 */
    @JsonIgnore
    @Column(length = 4096)
    private String fallbackApiKeyEncrypted;

    /** 备用槽 base_url（OpenAI 兼容端点，如 https://api.siliconflow.cn）。 */
    @Column(length = 500)
    private String fallbackBaseUrl;

    /** 备用槽模型名（供应商私有，如 Qwen/Qwen2.5-72B-Instruct）。 */
    @Column(length = 200)
    private String fallbackModel;

    /**
     * 备用槽 chat/completions 路径后缀：绝大多数端点是 /v1/chat/completions；
     * 智谱（bigmodel.cn）的兼容端点要配 /chat/completions（base 配到 /api/paas/v4）。
     */
    @Column(length = 200)
    private String fallbackChatPath;

    /** 最近一次连通性测试的时间（「测试」按钮，参照 /user/push/test 的先测后存语义）。 */
    private LocalDateTime lastTestedAt;

    /** 最近一次测试主槽是否通过。 */
    private Boolean lastTestOk;

    /** 最近一次测试的失败原因（可直接给用户看的中文，已脱敏）。 */
    @Column(length = 500)
    private String lastError;

    /** 最后一次保存配置的时间。 */
    private LocalDateTime updatedAt;
}
