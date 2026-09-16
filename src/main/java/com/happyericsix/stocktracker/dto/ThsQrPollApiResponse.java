package com.happyericsix.stocktracker.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * Python {@code GET /api/v1/ths/qr/poll} 的返回（<b>内部</b>用）。
 *
 * <p>命名带 {@code Api} 是为了和「Java 对外暴露的」
 * {@link ThsQrPollResponse} 区分开。
 *
 * <p>⚠️ 这个接口<b>返回形状不唯一</b>，一共四种，写 DTO 时最容易出错：
 * <pre>
 * ① 还没扫（最常见）   {"ok":true,  "data":{"status":"pending"}}
 * ② 扫到了            {"ok":true,  "data":{"status":"ok","session":{...}}}
 * ③ 会话不存在/过期    {"ok":false, "error":"二维码已过期..."}   HTTP 404
 * ④ 其它错误          {"ok":false, "error":"等待扫码超时..."}    HTTP 502
 * </pre>
 *
 * <p>注意 ① 里<b>没有 session 字段</b>，所以 {@code data.session} 会是 null。
 * 用 {@link #isPending()} / {@link #isConfirmed()} 判断，不要直接取 session。
 */
public record ThsQrPollApiResponse(
        boolean ok,
        Data data,
        String error
) {
    public record Data(
            String status,      // "pending" | "ok"
            Session session     // status=pending 时为 null
    ) {}

    /**
     * 扫码成功后的凭证。
     *
     * <p>⚠️ account/password 是<b>同花顺的账号和密码</b>，不是 token。
     * 必须加密后存进 ths_bindings 表（30 天免登录靠它）。
     */
    public record Session(
            String account,     // "mx_7miffcx00"
            String password,    // 32 位 hex
            String qrid,
            // ⚠️ 这个字段的 JSON 键名是下划线 expire_time（同花顺原始字段名），
            //    和本接口其它字段的驼峰命名不一致，所以必须加 @JsonProperty。
            @JsonProperty("expire_time") long expireTime
    ) {}

    /** 手机还没扫 —— 正常状态，前端应继续轮询。 */
    public boolean isPending() {
        return ok && data != null && "pending".equals(data.status());
    }

    /** 扫到了 —— 可以建绑定 + 签发 JWT。 */
    public boolean isConfirmed() {
        return ok && data != null && "ok".equals(data.status());
    }
}
