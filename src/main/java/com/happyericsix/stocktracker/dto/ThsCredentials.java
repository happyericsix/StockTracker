package com.happyericsix.stocktracker.dto;

/**
 * 同花顺会话凭证（加密存进 {@code ths_bindings.session_encrypted} 的明文结构）。
 *
 * <p>⚠️ 这是<b>敏感数据</b>：
 * <ul>
 *   <li>加密前是 JSON：{@code {"account":"mx_xxx","password":"<32位hex>"}}</li>
 *   <li>只在 {@code ThsQrService} / {@code ThsSyncService} 内部使用</li>
 *   <li><b>绝不出现在任何对外 DTO 里，绝不写日志</b></li>
 * </ul>
 *
 * <p>字段名刻意用 account/password（和同花顺返回一致），
 * 不要误以为 password 是"同花顺账号密码"—— 扫码拿到的是同花顺给的一次性凭证，
 * 但这个凭证确实就是拿去走 unified_login 的账号密码。
 */
public record ThsCredentials(String account, String password) {

    /** 序列化成待加密的 JSON。 */
    public String toJson() {
        // 手写 JSON，避免为一个两字段的小结构引入额外依赖
        return "{\"account\":\"" + escape(account) + "\",\"password\":\"" + escape(password) + "\"}";
    }

    /** 从解密后的 JSON 还原。 */
    public static ThsCredentials fromJson(String json) {
        return new ThsCredentials(extract(json, "account"), extract(json, "password"));
    }

    private static String escape(String s) {
        if (s == null) {
            return "";
        }
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    /**
     * 从 JSON 里取字段值。
     *
     * <p>之所以不用 Jackson：这个结构由本类自己写入，格式完全可控，
     * 而且 account/password 都是十六进制/字母数字，不含需要转义的字符。
     * 用简单解析可以避免"为了两字段引入依赖"。
     */
    private static String extract(String json, String field) {
        if (json == null) {
            return "";
        }
        String key = "\"" + field + "\":\"";
        int start = json.indexOf(key);
        if (start < 0) {
            throw new IllegalArgumentException("凭证 JSON 缺少字段: " + field);
        }
        start += key.length();
        int end = json.indexOf('"', start);
        if (end < 0) {
            throw new IllegalArgumentException("凭证 JSON 格式错误（字段未闭合）: " + field);
        }
        return json.substring(start, end).replace("\\\"", "\"").replace("\\\\", "\\");
    }

    /** 脱敏输出，仅供日志使用（绝不打印完整凭证）。 */
    @Override
    public String toString() {
        String a = account == null ? "" : account;
        return "ThsCredentials(account=" + (a.length() > 6 ? a.substring(0, 6) + "***" : "***")
                + ", password=<已隐藏>)";
    }
}
