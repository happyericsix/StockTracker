package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.repository.AlertRepository;
import com.happyericsix.stocktracker.service.evaluator.AlertEvaluator;
import com.happyericsix.stocktracker.service.evaluator.EvaluationResult;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

/**
 * 预警评估服务（策略模式 + 触发编排）：
 *  - 启动时按 supportedType() 注册所有 AlertEvaluator 实现
 *  - evaluate(alert, data) 根据 alert.conditionType 找对应 evaluator，跑边沿触发+重置逻辑
 *  - tryTrigger(alert, data, result) 冷却检查 → 落库 → 推消息
 *  - 未注册的 type 返回 notTriggered("unsupported")
 *
 * 设计上不再有 @Scheduled 入口；触发时机由 AlertEvaluationListener 监听 PricesRefreshedEvent
 * 触发频率与价格刷新严格一致（每 5 分钟一次），杜绝"价已变但评估没跑"的窗口期
 *
 * v1 改造要点：
 *  - evaluate 现在是 @Transactional，evaluator 改的 armed/lastEvaluatedAt 落库
 *  - 冷却从硬编码 30min 改为读 alert.cooldownMinutes（默认 5min，0=不冷却）
 */
@Service
public class AlertEvaluationService {

    private static final Logger log = LoggerFactory.getLogger(AlertEvaluationService.class);

    private final AlertRepository alertRepo;
    private final MessageService messageService;
    private final StockService stockService;
    private final List<AlertEvaluator> evaluators;
    private final Map<String, AlertEvaluator> registry = new HashMap<>();

    public AlertEvaluationService(AlertRepository alertRepo,
                                  MessageService messageService,
                                  StockService stockService,
                                  List<AlertEvaluator> evaluators) {
        this.alertRepo = alertRepo;
        this.messageService = messageService;
        this.stockService = stockService;
        this.evaluators = evaluators;
    }

    @PostConstruct
    void initRegistry() {
        for (AlertEvaluator e : evaluators) {
            AlertEvaluator prev = registry.put(e.supportedType(), e);
            if (prev != null) {
                log.warn("Duplicate AlertEvaluator for type {}: {} replaced by {}",
                        e.supportedType(), prev.getClass().getSimpleName(), e.getClass().getSimpleName());
            }
        }
        log.info("AlertEvaluationService registered {} evaluators: {}",
                registry.size(), registry.keySet());
    }

    /**
     * 单条评估：找 evaluator → 跑评估（含 armed 状态机）→ 落库
     * 由 AlertEvaluationListener 在循环里逐条调用
     *
     * 必须 public + @Transactional：跨 bean 调用 Spring 代理拦截 + 持久化 evaluator 改的字段
     */
    @Transactional
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        if (alert == null || alert.getConditionType() == null) {
            return AlertEvaluator.notTriggered("alert or conditionType null");
        }
        AlertEvaluator evaluator = registry.get(alert.getConditionType());
        if (evaluator == null) {
            return AlertEvaluator.notTriggered("unsupported type: " + alert.getConditionType());
        }
        try {
            EvaluationResult result = evaluator.evaluate(alert, data);
            // evaluator 会改 alert.armed / alert.lastEvaluatedAt，必须落库
            // 即使没触发也要保存（比如 disarmed → armed 重置，或者 lastEvaluatedAt 时间衰减基准）
            alertRepo.save(alert);
            return result;
        } catch (Exception e) {
            log.error("Evaluator {} failed for alert {} ({}): {}",
                    evaluator.getClass().getSimpleName(), alert.getId(), alert.getStockSymbol(), e.getMessage());
            return AlertEvaluator.notTriggered("evaluator exception: " + e.getClass().getSimpleName());
        }
    }

    /**
     * 触发预警：冷却检查 → 改 Alert 状态 → 写历史消息 + SSE 推送
     * 整个动作在同一事务（@Transactional）里
     *
     * 必须 public：从 Listener 跨 bean 调用，Spring 代理才能拦截 @Transactional
     */
    @Transactional
    public boolean tryTrigger(Alert alert, RefreshedPrice data, EvaluationResult result) {
        int cooldownMinutes = alert.getCooldownMinutes() != null ? alert.getCooldownMinutes() : 5;
        LocalDateTime cooldownStart = LocalDateTime.now().minusMinutes(cooldownMinutes);
        if (messageService.isAlertInCooldown(alert.getId(), cooldownStart)) {
            log.debug("Alert {} in cooldown, skip", alert.getId());
            return false;
        }

        // 1) 更新 Alert 状态
        alert.setTriggered(true);
        alert.setLastTriggeredAt(LocalDateTime.now());
        alertRepo.save(alert);

        // 2) 写历史消息 + SSE 推送
        double currentPrice = data.currentPrice();
        double triggerValue = result.triggerValue() != null ? result.triggerValue() : currentPrice;
        String text = buildMessageText(alert, data, triggerValue);
        messageService.recordAlertTrigger(alert.getUser(), alert, currentPrice, triggerValue, text);

        return true;
    }

    public Optional<AlertEvaluator> getEvaluator(String conditionType) {
        return Optional.ofNullable(registry.get(conditionType));
    }

    private String buildMessageText(Alert alert, RefreshedPrice data, double triggerValue) {
        // 预警消息用股票名称展示（解析失败回退为代码）
        String symbol = stockService.resolveStockName(alert.getStockSymbol());
        double threshold = alert.getThreshold();
        String type = alert.getConditionType();
        double v = triggerValue;

        return switch (type) {
            case "price_above"     -> String.format("%s 价格突破 %.2f（当前 %.2f）", symbol, threshold, v);
            case "price_below"     -> String.format("%s 价格跌破 %.2f（当前 %.2f）", symbol, threshold, v);
            case "pnl_percent"     -> String.format("%s 盈亏 %.2f%%，触发阈值 %.2f%%", symbol, v, threshold);
            case "rsi_overbought"  -> String.format("%s RSI 升至 %.1f，超过阈值 %.0f", symbol, v, threshold);
            case "rsi_oversold"    -> String.format("%s RSI 跌至 %.1f，低于阈值 %.0f", symbol, v, threshold);
            case "macd_golden_cross" -> String.format("%s MACD 金叉（hist %.3f）", symbol, v);
            case "macd_death_cross"  -> String.format("%s MACD 死叉（hist %.3f）", symbol, v);
            case "trailing_take_profit" -> String.format(
                    "%s 从最高点回撤 %.2f%%（触发阈值 %.2f%%，当前 %.2f）",
                    symbol, v, threshold, data.currentPrice());
            default -> String.format("%s 触发 %s 预警（值 %.4f，阈值 %.4f）", symbol, type, v, threshold);
        };
    }
}
