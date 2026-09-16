package com.happyericsix.stocktracker.dto;

/**
 * {@code GET /api/v1/ths/qr/poll} 的返回（<b>给前端用</b>）。
 *
 * <p>三种情况：
 * <pre>
 * 还没扫   {"status":"pending"}                          —— 前端继续轮询
 * 扫到了   {"status":"ok", "token":"&lt;JWT&gt;", "username":"ths_1234"}
 * 过期     {"status":"expired", "message":"二维码已过期，请刷新"}
 * </pre>
 *
 * <p>⚠️ <b>绝不含同花顺凭证</b>。扫码拿到的 account/password 只留在服务端
 * 加密存库，对外只给本项目自己的 JWT。
 */
public record ThsQrPollResponse(
        String status,      // "pending" | "ok" | "expired"
        String token,       // JWT，仅 status=ok 时非空
        String username,    // 仅 status=ok 时非空
        Boolean firstLogin, // 首次扫码自动建号时为 true
        String message      // 可读提示（过期/失败时用）
) {
    public static ThsQrPollResponse pending() {
        return new ThsQrPollResponse("pending", null, null, null, null);
    }

    public static ThsQrPollResponse ok(String token, String username, boolean firstLogin) {
        return new ThsQrPollResponse("ok", token, username, firstLogin, "登录成功");
    }

    public static ThsQrPollResponse expired(String message) {
        return new ThsQrPollResponse("expired", null, null, null, message);
    }
}
