package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.service.IntradayAttentionService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * 盘中注意力调度（⑤步）：交易时段内每 10 分钟一趟确定性检查，休市零执行。
 * 相位判断复用 MarketStatusService（与前端"这个价是哪天的"同一判据）。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class IntradayAttentionJob {

    private static final ZoneId CN = ZoneId.of("Asia/Shanghai");

    private final IntradayAttentionService attentionService;
    private final MarketStatusService marketStatusService;

    @Scheduled(fixedDelay = 10 * 60_000, initialDelay = 3 * 60_000)
    public void sweepTradingHours() {
        try {
            MarketStatusResponse status = marketStatusService.current();
            String phase = status == null ? null : status.phase();   // record 组件访问
            if (!"morning".equals(phase) && !"afternoon".equals(phase)) {
                return;   // 午休/集合竞价/盘后/休市：确定性通道也只在连续竞价时段说话
            }
            int sent = attentionService.sweep(LocalDateTime.now(CN));
            if (sent > 0) {
                log.info("盘中注意力推送 {} 条", sent);
            }
        } catch (Exception e) {
            log.debug("盘中注意力轮跳过: {}", e.toString());   // 旁路：绝不影响任何主流程
        }
    }
}
