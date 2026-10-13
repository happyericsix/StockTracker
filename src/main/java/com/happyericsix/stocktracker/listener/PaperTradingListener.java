package com.happyericsix.stocktracker.listener;

import com.happyericsix.stocktracker.event.DecisionModeSwitchedEvent;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.ExecutionContract;
import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;
import org.springframework.transaction.event.TransactionPhase;
import org.springframework.transaction.event.TransactionalEventListener;

import java.util.concurrent.atomic.AtomicLong;

@Component
public class PaperTradingListener {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingListener.class);

    /**
     * 结算节流（2026-10-08 快车道）：价格刷新升到 15 秒一次后，这里**不能**跟着每 15 秒
     * 结算一轮 —— 盘中结算吃的是 1 分钟 K 线，15 秒一评有 3/4 是对同一根 bar 的重复
     * Python 往返（每策略每分钟 4 次调用，只为最后落一个"bar 没变"）。45 秒一轮：
     * 每根新 bar 最多迟到 45 秒被看到，止损响应足够快，成本降到 1/3。
     */
    private static final long MIN_SETTLEMENT_INTERVAL_MS = 45_000;

    private final PaperTradingService paperTradingService;
    private final AtomicLong lastSettlementAt = new AtomicLong(0);

    public PaperTradingListener(PaperTradingService paperTradingService) {
        this.paperTradingService = paperTradingService;
    }

    /**
     * @Async（settlementExecutor）：这条监听里是逐策略最长 60s 的 Python 结算，
     * 同步监听会跑在单线程调度器上 —— 和价格刷新、预警评估互相排队，
     * Python 一慢 fixedRate 任务就持续滞后。
     */
    @Async("settlementExecutor")
    @EventListener
    public void onPricesRefreshed(PricesRefreshedEvent event) {
        // 节流用 compareAndSet 而不是 set：@Async 下事件可能排队连发，
        // set 会让排队的每一个都"刚跑完"而全部跳过 —— 那是节流变静默。
        long now = System.currentTimeMillis();
        long last = lastSettlementAt.get();
        if (now - last < MIN_SETTLEMENT_INTERVAL_MS) {
            return;
        }
        if (!lastSettlementAt.compareAndSet(last, now)) {
            return;   // 另一个排队的事件刚刚抢先跑走了这一轮
        }
        try {
            paperTradingService.evaluateRealtime();
        } catch (Exception e) {
            log.error("PaperTradingListener failed: {}", e.getMessage(), e);
        }
    }

    /**
     * 切到 agent 且模拟盘在跑：立刻开第一次委员会（AFTER_COMMIT + **同步** ——
     * 切换提交之后、同一请求线程里执行，用户点"切换"等的就是这个结果，
     * 切完页面立刻能看到首会结论与授权）。
     *
     * <p>为什么是 AFTER_COMMIT 而不是普通 @EventListener：发布方
     * {@code switchDecisionMode} 带 @Transactional，普通监听器会跑在**未提交的事务里** ——
     * 里面的 Python 决策调用（最长 60s）占着数据库连接等网络，且首会读到的模式
     * 还没提交、写的痕迹也挂在那笔事务上。这与 startPaper/evaluateDaily 的既定纪律一致：
     * 结算绝不在别人的长事务里跑。
     *
     * <p>为什么不能等 15:30：换决策者的意义就是"从现在起按新决策者来"。实测
     * （2026-10-06~08，国庆假期）切完之后盘中每 5 分钟留一条 awaiting_authorization、
     * 一天重复 253 次，委员会第一次会要等到当天 15:30 —— 切换后的整个上午账户是哑的。
     * 委员会门控自带 first_decision 触发（从未开过会 ⇒ 开会），这里只是把第一次提前到现在；
     * 当天没有 K 线（休市/数据未出）时这次尝试只留一条 no_bar 痕迹，不浪费模型调用。
     */
    @TransactionalEventListener(phase = TransactionPhase.AFTER_COMMIT)
    public void onDecisionModeSwitched(DecisionModeSwitchedEvent event) {
        boolean agent = ExecutionContract.DECISION_MODE_AGENT.equals(
                ExecutionContract.normalizeDecisionMode(event.mode()));
        if (!event.paperEnabled() || !agent) {
            return;
        }
        try {
            paperTradingService.evaluateOnDecisionModeSwitched(event.strategyId());
        } catch (Exception e) {
            // 首会失败不回滚切换本身：模式已经换了，下一次日线结算（15:30）会再试。
            log.error("First committee meeting after mode switch failed for strategy id={}: {}",
                    event.strategyId(), e.getMessage(), e);
        }
    }
}
