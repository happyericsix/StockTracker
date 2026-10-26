package com.happyericsix.stocktracker.dto;

import lombok.Data;

/**
 * 保存/测试用户 LLM Key 配置的请求体（BYOK，2026-10-25）。
 *
 * <p>交互语义与消息推送的 pushTarget 完全一致：<b>apiKey 留空 = 保留已存 Key</b>
 * （避免"只是换个模型名"把 Key 抹掉），已存的 Key 绝不回传前端（只回脱敏形态）。
 *
 * <p>槽位规则（校验在 UserLlmConfigService）：
 * <ul>
 *   <li>primary.baseUrl 留空 = DeepSeek 官方预设；非空（自定义 OpenAI 兼容端点）时
 *       primary.model 必填；</li>
 *   <li>fallback 整体可选（传 null = 清除备用槽）；传了则 baseUrl/model 必填；</li>
 *   <li>主槽必须存在：只配备用不配主槽没有意义（Python 侧按主→备顺序切换）。</li>
 * </ul>
 */
@Data
public class UserLlmConfigRequest {

    /** 主槽。不可为 null。 */
    private Slot primary;

    /** 备用槽。null = 清除；非 null 时 baseUrl/model 必填、apiKey 首次必填。 */
    private Slot fallback;

    @Data
    public static class Slot {
        /** API Key 明文（仅本次请求存在）。留空 = 保留已存 Key 不改。 */
        private String apiKey;
        /** base_url。主槽留空 = DeepSeek 预设；备用槽必填。 */
        private String baseUrl;
        /** 模型名。主槽预设时可留空（deepseek-chat）；自定义端点/备用槽必填。 */
        private String model;
        /** chat/completions 路径后缀（高级项，默认 /v1/chat/completions，智谱等需要覆盖）。 */
        private String chatPath;
    }
}
