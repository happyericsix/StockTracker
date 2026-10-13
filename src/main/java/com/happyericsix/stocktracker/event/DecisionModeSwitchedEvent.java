package com.happyericsix.stocktracker.event;

/**
 * 决策来源切换事件：{@code StrategyService} 在切换**真正发生**时发布，模拟盘侧监听。
 *
 * <h3>为什么用事件而不是直接调用</h3>
 * {@code PaperTradingService} 已经依赖 {@code StrategyService}（周频验证），
 * 反向再依赖就是构造环。而"换了决策者之后要不要立刻开第一次委员会"是模拟盘的事，
 * 策略服务不该知道 —— 它只广播"换了"这个事实，由监听方决定做什么。
 *
 * <p>只在模式**实际变化**时发布（切到当前模式是空操作，不广播）。
 */
public record DecisionModeSwitchedEvent(
        Long strategyId,
        String mode,
        boolean paperEnabled
) {}
