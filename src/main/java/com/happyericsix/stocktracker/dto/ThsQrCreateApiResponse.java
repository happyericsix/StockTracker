package com.happyericsix.stocktracker.dto;

/**
 * Python {@code GET /api/v1/ths/qr/create} 的返回（<b>内部</b>用）。
 *
 * <p>命名带 {@code Api} 是为了和「Java 对外暴露的」
 * {@link ThsQrCreateResponse} 区分开 —— 两者字段完全不同：
 * <ul>
 *   <li>本类：映射 Python 的三层结构 {@code {"ok","data","error"}}，只在 client 层用</li>
 *   <li>{@link ThsQrCreateResponse}：给前端用的扁平结构，不含 ok/error</li>
 * </ul>
 *
 * <p>类型依据：{@code python-data-service/test_type_contract.py} 的实测结论。
 */
public record ThsQrCreateApiResponse(
        boolean ok,
        Data data,
        String error
) {
    public record Data(
            String qrSessionId,     // "7e38665d92ac431ab5a22196698795cf"
            String qrUrl,           // "http://mobile.10jqka.com.cn/?source=PC&qrid=usk_xxx"
            long pollIntervalMs,    // 4000  —— 数字，不是字符串
            int expiresInSec        // 120   —— 数字
    ) {}

    /** 成功与否的便捷判断（data 可能是 null）。 */
    public boolean isSuccess() {
        return ok && data != null;
    }
}
