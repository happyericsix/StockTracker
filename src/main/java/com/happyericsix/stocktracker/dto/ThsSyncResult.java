package com.happyericsix.stocktracker.dto;

import java.time.LocalDateTime;

/**
 * {@code POST /api/v1/ths/sync} 的返回：同步结果摘要。
 *
 * <p>字段含义：
 * <ul>
 *   <li>{@code added} 本次新建了几只</li>
 *   <li>{@code unchanged} 已存在、跳过没动的有几只</li>
 *   <li>{@code total} 同花顺侧一共返回几只</li>
 * </ul>
 *
 * <p>注意 {@code unchanged} 不是"失败"—— 已存在的行按设计<b>原样不动</b>，
 * 这样用户手填的 buyPrice / quantity 才不会被覆盖。
 */
public record ThsSyncResult(
        int added,
        int unchanged,
        int total,
        LocalDateTime lastSyncAt
) {}
