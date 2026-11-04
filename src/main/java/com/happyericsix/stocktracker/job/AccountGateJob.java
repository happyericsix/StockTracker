package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

/**
 * 账户守门员的持仓风险扫描（ACCOUNT-GATE 阶段2，2026-11-04）。
 *
 * <p>交易时段内每 {@code paper.account-gate.scan-interval-ms}（默认 5 分钟）一趟
 * **电平触发**扫描：对"实际持仓状态 vs 风险政策"的差值行动（near_stop /
 * drawdown_warning / concentration / auth_expired），越界才唤醒守门员（1 次 LLM），
 * 无越界零成本。相位判断复用 MarketStatusService（与盘中注意力 Job 同一判据），
 * 午休/盘后/休市零执行。
 *
 * <p>与信号路径（gateOnSignal，边缘触发）的分工：信号路径答"这个机会要不要"，
 * 本扫描答"我的持仓还安全吗"——一个 agent、多种唤醒源、同一份账户面板。
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class AccountGateJob {

    private final PaperTradingService paperTradingService;
    private final MarketStatusService marketStatusService;

    @Scheduled(fixedDelayString = "${paper.account-gate.scan-interval-ms:300000}",
            initialDelay = 4 * 60_000)
    public void scanRiskLevels() {
        try {
            MarketStatusResponse status = marketStatusService.current();
            String phase = status == null ? null : status.phase();   // record 组件访问
            if (!"morning".equals(phase) && !"afternoon".equals(phase)) {
                return;   // 午休/集合竞价/盘后/休市：持仓风险由下一连续竞价时段再扫
            }
            paperTradingService.runAccountGateScan();
        } catch (Exception e) {
            log.debug("账户守门员扫描跳过: {}", e.toString());   // 旁路：绝不影响结算主流程
        }
    }
}
