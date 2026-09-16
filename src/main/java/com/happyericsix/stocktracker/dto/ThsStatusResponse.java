package com.happyericsix.stocktracker.dto;

import java.time.LocalDateTime;

/**
 * {@code GET /api/v1/ths/status} 的返回：绑定状态 + 同步摘要。
 *
 * <p>数据全部来自<b>本项目的 {@code ths_bindings} 表</b>，不调 Python
 * （那正是 Python 侧 {@code /ths/status} 被删掉的原因）。
 *
 * <p>⚠️ 不含任何凭证。
 */
public record ThsStatusResponse(
        boolean bound,              // 是否已绑定同花顺账号
        LocalDateTime expireTime,   // 同花顺凭证过期时间；null = 未勾选30天免登录
        boolean expired,            // 按本地记录的过期时间判断（仅供参考，实际以调用结果为准）
        LocalDateTime lastSyncAt,
        Integer lastSyncCount,
        String lastError            // 最近一次失败原因（中文，可直接展示）
) {
    public static ThsStatusResponse unbound() {
        return new ThsStatusResponse(false, null, false, null, null, null);
    }
}
