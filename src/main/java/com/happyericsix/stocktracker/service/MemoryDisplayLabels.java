package com.happyericsix.stocktracker.service;

import java.util.Locale;
import java.util.Map;

/**
 * 记忆内容的中文展示标签 —— **唯一真相源在 Java**（数据在谁手里，展示语义就跟到谁）。
 *
 * <h3>为什么需要这一层（2026-10-06 线上反馈："记忆页根本看不懂"）</h3>
 * 事实的 predicate 是取代链的稳定键（{@code stop_loss_pct}），subject 是
 * {@code strategy:3} 这种机器形态。它们直接下发给前端时，用户看到的就是
 * "strategy:3 backtest_total_return_pct：12.5"。此前只有前端维护了一张
 * 18 个用户谓词的翻译表，而系统里还有 25+ 个客观事实谓词
 * （见 {@link ObjectiveFactKeys}）不在其中——全是英文原文。
 *
 * <h3>消费方</h3>
 * {@code MemoryFactService.toMap} 把 {@code predicateLabel / subjectLabel / objectLabel}
 * 放进每条事实的输出：前端"记忆"页优先用它（本地表只做旧数据回退），
 * Python 注入块（recall._fact_line）同样优先用它。两个消费方都不再各自扩表，
 * 新谓词只在这里登记一次。
 *
 * <h3>与既有跨语言约束的关系</h3> 谓词键本身仍以
 * {@code ObjectiveFactKeys ↔ python objective.PREDICATES} 的一致性测试为准；
 * 这里只翻译键，不改键。
 */
final class MemoryDisplayLabels {

    private MemoryDisplayLabels() {
    }

    /**
     * 谓词 → 中文名。前 18 个是"用户设定类"（与 Python recall._PREDICATE_LABELS、
     * 前端 Memory.vue 的回退表同源）；后面是客观事实类（此前完全没有翻译，
     * 是"看不懂"的主要来源）。
     */
    static final Map<String, String> PREDICATE_LABELS = Map.ofEntries(
            // —— 用户设定类 ——
            Map.entry("stop_loss_pct", "止损"),
            Map.entry("take_profit_pct", "止盈"),
            Map.entry("trailing_stop_pct", "跟踪止盈"),
            Map.entry("risk_preference", "风险偏好"),
            Map.entry("position_size", "仓位"),
            Map.entry("holding_cost", "持仓成本"),
            Map.entry("holding", "持有"),
            Map.entry("watch_reason", "关注理由"),
            Map.entry("investment_horizon", "投资周期"),
            Map.entry("capital", "资金量"),
            Map.entry("trading_frequency", "交易频率"),
            Map.entry("sector_preference", "板块偏好"),
            Map.entry("investing_style", "投资风格"),
            Map.entry("excluded_subject", "不参与"),
            Map.entry("target_return", "目标收益"),
            Map.entry("max_drawdown_tolerance", "最大回撤容忍"),
            Map.entry("learning_goal", "学习目标"),
            Map.entry("purpose", "用途"),
            // 聚合叙事（双层记忆 2026-10-09）：值本身就是一句话，标签只作类型徽章
            Map.entry("run_summary", "运行结论"),
            // —— 客观事实：回测 ——
            Map.entry("backtest_total_return_pct", "回测总收益"),
            Map.entry("backtest_buy_and_hold_return_pct", "回测同期买入持有收益"),
            Map.entry("backtest_excess_pct", "回测超额收益"),
            Map.entry("backtest_max_drawdown_pct", "回测最大回撤"),
            Map.entry("backtest_sharpe", "回测夏普比率"),
            Map.entry("backtest_win_rate_pct", "回测胜率"),
            Map.entry("backtest_trade_count", "回测交易笔数"),
            Map.entry("backtest_at", "回测时间"),
            // —— 客观事实：模拟盘 ——
            Map.entry("paper_equity", "模拟盘净值"),
            Map.entry("paper_cash", "模拟盘可用现金"),
            Map.entry("paper_shares", "模拟盘持仓股数"),
            Map.entry("paper_return_pct", "模拟盘收益率"),
            Map.entry("paper_last_eval_at", "模拟盘最近结算"),
            Map.entry("paper_max_drawdown_pct", "模拟盘最大回撤"),
            Map.entry("paper_flat_days", "模拟盘连续空仓天数"),
            // —— 客观事实：样本外验证 ——
            Map.entry("verify_at", "验证时间"),
            Map.entry("verify_valid_cells_count", "验证有效样本数"),
            Map.entry("verify_beat_buy_and_hold_count", "跑赢买入持有的样本数"),
            Map.entry("verify_avg_excess_pct", "验证平均超额收益"),
            Map.entry("verify_fee_drag_pct", "验证摩擦损耗占比"),
            Map.entry("verify_engine_version", "验证引擎版本"),
            // —— 客观事实：预期登记与回填 ——
            Map.entry("expectation_at", "预期登记时间"),
            Map.entry("expectation_metric", "预期度量"),
            Map.entry("expectation_threshold", "预期门槛"),
            Map.entry("expectation_deadline", "预期到期日"),
            Map.entry("expectation_status", "预期状态"),
            Map.entry("expectation_outcome", "到期实际结果"),
            Map.entry("expectation_evaluated_at", "预期回填时间"));

    /** 枚举型事实值的翻译（目前只有预期状态；度量/策略名等开放值不翻译） */
    private static final Map<String, String> VALUE_LABELS = Map.of(
            "pending", "待验证",
            "met", "已达成",
            "unmet", "未达成",
            "unmeasurable", "无法度量");

    static String predicateLabel(String predicate) {
        if (predicate == null || predicate.isBlank()) {
            return "";
        }
        return PREDICATE_LABELS.getOrDefault(predicate.trim().toLowerCase(Locale.ROOT), predicate);
    }

    /**
     * 主体的展示形态。返回 {@code null} 表示"关于用户自己"——消费方（前端页面
     * 本来就在讲用户）应当隐藏主体，只显示谓词。
     * {@code strategy:3} → {@code 策略 #3}；6 位股票代码保持原文（证券语境可接受）。
     */
    static String subjectLabel(String subject) {
        String text = subject == null ? "" : subject.trim();
        if (text.isEmpty() || text.equalsIgnoreCase("user") || text.equalsIgnoreCase("用户")) {
            return null;
        }
        if (text.startsWith("strategy:")) {
            String id = text.substring("strategy:".length()).trim();
            return id.isEmpty() ? "策略" : "策略 #" + id;
        }
        return text;
    }

    /**
     * 值的展示形态：百分比谓词补上 %（"12.5" → "12.5%"，"-8" → "-8%"），
     * 枚举值翻译成中文，其余原样。判据用谓词后缀而不是白名单：
     * {@code ObjectiveFactKeys} 的约定就是 _pct = 百分数，新键自动继承。
     */
    static String objectLabel(String predicate, String value) {
        if (value == null || value.isBlank()) {
            return value;
        }
        String key = predicate == null ? "" : predicate.trim().toLowerCase(Locale.ROOT);
        String trimmed = value.trim();
        String translated = VALUE_LABELS.get(trimmed.toLowerCase(Locale.ROOT));
        if (translated != null && "expectation_status".equals(key)) {
            return translated;
        }
        if (key.endsWith("_pct") && isNumeric(trimmed)) {
            return trimmed + "%";
        }
        return value;
    }

    private static boolean isNumeric(String text) {
        try {
            Double.parseDouble(text);
            return true;
        } catch (NumberFormatException e) {
            return false;
        }
    }
}
