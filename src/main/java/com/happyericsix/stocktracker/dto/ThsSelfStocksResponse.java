package com.happyericsix.stocktracker.dto;

import java.util.List;

/**
 * Python {@code POST /api/v1/ths/selfstocks} 的返回。
 *
 * <p>请求体：{@code {"account":"mx_xxx","password":"<32位hex>"}}
 * （必须是 POST，凭证走 body —— 用 GET query 会让账号密码明文进访问日志）
 *
 * <p>成功返回：
 * <pre>
 * {"ok":true,"data":[{"code":"688023","market":"SH","price":123.45,"addedAt":"20240101"}]}
 * </pre>
 */
public record ThsSelfStocksResponse(
        boolean ok,
        List<Stock> data,
        String error
) {
    /**
     * 自选股条目。
     *
     * <p>⚠️ price / addedAt 用<b>包装类型</b>而不是基本类型，
     * 因为同花顺对指数（如上证指数 1A0001）不记加入价，这两个字段会是 null。
     * 用 double 接 null 会直接抛反序列化异常。
     */
    public record Stock(
            String code,        // "688023"
            String market,      // "SH" / "SZ" / "BJ" / "ZS"（不会是 null）
            Double price,       // 可能是 null
            String addedAt      // 可能是 null
    ) {
        /**
         * 转成本项目的内部规范代码，如 SH688023。
         *
         * <p>对应设计文档 §4：写库统一「市场前缀 + 代码」大写形式。
         */
        public String toProjectSymbol() {
            return market + code;
        }

        /**
         * 去掉市场前缀的纯代码，用于「底层代码等价」去重。
         *
         * <p>防止 600519 与 SH600519 在数据库里出现两行。
         */
        public String bareCode() {
            return code;
        }
    }
}
