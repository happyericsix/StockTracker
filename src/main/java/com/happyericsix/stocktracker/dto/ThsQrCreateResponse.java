package com.happyericsix.stocktracker.dto;

/**
 * {@code POST /api/v1/ths/qr/create} 的返回。
 *
 * <p>对应 Python {@code GET /api/v1/ths/qr/create}，
 * 但做了一层裁剪：<b>只把前端渲染二维码需要的东西透出去</b>。
 *
 * <p>⚠️ 不含任何凭证 —— qrSessionId 只是 Python 侧的扫码会话 id，
 * 拿到它并不能读你的自选股。
 */
public record ThsQrCreateResponse(
        String qrSessionId,
        String qrUrl,
        long pollIntervalMs,
        int expiresInSec
) {}
