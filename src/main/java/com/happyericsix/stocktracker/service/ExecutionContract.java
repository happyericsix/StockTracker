package com.happyericsix.stocktracker.service;

import java.util.List;
import java.util.Map;

/**
 * 执行契约（Java 侧）：与 {@code python-data-service/agent/execution_contract.py} 一一对应。
 *
 * <h3>为什么要有这个类</h3>
 * "模拟盘 vs 回测"的对照是模拟盘存在的唯一理由，而它成立的前提是两边**口径一致且被记录**。
 * 现状是：回测按次日开盘价成交，模拟盘按信号当根收盘价成交，且没有任何地方记录
 * "这条记录属于哪个口径" —— 于是两边的数字从第一天起就不可比。
 *
 * <h3>这个类只做三件事</h3>
 * <ol>
 *   <li>把口径写成**封闭常量集**（决策 / 结算类型 / 成交价口径 / 复权口径 / 跳过原因）。
 *       未知值一律归一成 {@code unknown_*} 并告警，绝不静默丢弃 ——
 *       自由字符串会让"为什么没成交"永远统计不出来；</li>
 *   <li>提供**执行指纹**与"能不能对比"的判定：只有指纹相同的两份结果才允许对比。
 *       这是"单轨还是双轨"的答案 —— <b>不做双轨代码，做口径标签</b>；</li>
 *   <li>与 Python 侧的常量由 {@code tests/test_execution_contract_java.py} 逐字比对。
 *       本项目已经因为"两边字符串各写一份"栽过跟头（user_id / user_name 那次）。</li>
 * </ol>
 *
 * <p>刻意不做的事：口径**不放进配置文件或数据库**。改动必须走代码评审，
 * 且由 {@code /health} 暴露当前生效值 —— 否则它会变成一个没人知道当前值的黑箱。
 */
public final class ExecutionContract {

    private ExecutionContract() {
    }

    // >>> EXECUTION_CONTRACT —— 与 Python 侧 execution_contract 常量一一对应，勿单独改动
    // ---------- 决策 ----------
    public static final String DECISION_BUY = "buy";
    public static final String DECISION_SELL = "sell";
    public static final String DECISION_SKIP = "skip";

    // ---------- 结算类型 ----------
    public static final String SETTLEMENT_DAILY = "daily";
    public static final String SETTLEMENT_REALTIME = "realtime";

    // ---------- 成交价口径 ----------
    /** 信号当根收盘价（P0 日线用；回测侧需同名模式才可比）。 */
    public static final String FILL_CLOSE = "close";
    /** 次日开盘价（回测默认；模拟盘要到 P2 的"挂单 + 两阶段结算"才能用）。 */
    public static final String FILL_NEXT_OPEN = "next_open";
    /** 盘中最新价（实时结算；不参与回测对照）。 */
    public static final String FILL_REALTIME_LAST = "realtime_last";

    // ---------- 复权口径 ----------
    public static final String ADJUST_NONE = "none";
    public static final String ADJUST_QFQ = "qfq";

    // ---------- 跳过原因（封闭集） ----------
    public static final String SKIP_MARKET_CLOSED = "market_closed";
    public static final String SKIP_SUSPENDED = "suspended";
    public static final String SKIP_NO_BAR = "no_bar";
    public static final String SKIP_DATA_UNAVAILABLE = "data_unavailable";
    public static final String SKIP_LIMIT_BLOCKED = "limit_blocked";
    public static final String SKIP_T1_BLOCKED = "t1_blocked";
    public static final String SKIP_EX_DIVIDEND_DAY = "ex_dividend_day";
    public static final String SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT = "insufficient_cash_for_one_lot";
    public static final String SKIP_INSUFFICIENT_CASH = "insufficient_cash";
    public static final String SKIP_INVALID_PRICE = "invalid_price";
    public static final String SKIP_RULE_NOT_MET = "rule_not_met";

    // ---------- 归一与版本 ----------
    public static final String UNKNOWN_PREFIX = "unknown_";
    public static final int SNAPSHOT_SCHEMA_VERSION = 1;
    public static final int MONEY_POLICY_VERSION = 1;
    // <<< EXECUTION_CONTRACT

    /** 枚举值的长度上限（与 Python 侧一致）。 */
    public static final int MAX_ENUM_CHARS = 32;

    public static final List<String> DECISIONS =
            List.of(DECISION_BUY, DECISION_SELL, DECISION_SKIP);

    public static final List<String> SETTLEMENT_KINDS =
            List.of(SETTLEMENT_DAILY, SETTLEMENT_REALTIME);

    public static final List<String> FILL_BASES =
            List.of(FILL_CLOSE, FILL_NEXT_OPEN, FILL_REALTIME_LAST);

    public static final List<String> ADJUST_MODES =
            List.of(ADJUST_NONE, ADJUST_QFQ);

    public static final List<String> SKIP_REASONS = List.of(
            SKIP_MARKET_CLOSED, SKIP_SUSPENDED, SKIP_NO_BAR, SKIP_DATA_UNAVAILABLE,
            SKIP_LIMIT_BLOCKED, SKIP_T1_BLOCKED, SKIP_EX_DIVIDEND_DAY,
            SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT, SKIP_INSUFFICIENT_CASH,
            SKIP_INVALID_PRICE, SKIP_RULE_NOT_MET);

    /** 结算类型 → 成交价口径。**唯一的映射处**（Python 侧同名映射）。 */
    public static final Map<String, String> FILL_BASIS_BY_SETTLEMENT = Map.of(
            SETTLEMENT_DAILY, FILL_CLOSE,
            SETTLEMENT_REALTIME, FILL_REALTIME_LAST);

    /**
     * 把任意输入归一成封闭集里的值；不认识的一律 {@code unknown_*}。
     *
     * <p>刻意不抛异常：结算路径上因为一个枚举值不认识就中断，代价远大于记一条 {@code unknown_*}；
     * 但也绝不静默丢弃 —— 调用方应把返回值原样落库，未知值因此永远可见。
     */
    public static String normalize(String raw, List<String> allowed) {
        String text = raw == null ? "" : raw.trim();
        if (allowed.contains(text)) {
            return text;
        }
        String fallback = text.isEmpty() ? UNKNOWN_PREFIX + "unspecified" : UNKNOWN_PREFIX + text;
        return fallback.length() > MAX_ENUM_CHARS ? fallback.substring(0, MAX_ENUM_CHARS) : fallback;
    }

    public static String normalizeSkipReason(String raw) {
        return normalize(raw, SKIP_REASONS);
    }

    /** 结算类型对应的成交价口径；未登记的类型不猜、不默认取 close。 */
    public static String fillBasisFor(String settlementKind) {
        String kind = settlementKind == null ? "" : settlementKind.trim();
        String basis = FILL_BASIS_BY_SETTLEMENT.get(kind);
        return basis != null ? basis : normalize(kind, FILL_BASES);
    }

    /**
     * 一次结算/一次回测的口径指纹。只有指纹相同的两份结果才允许对比。
     *
     * <p>{@code engineVersion} 由 Python 侧推导（源码与常量的哈希）后随载荷传过来 ——
     * 人工维护版本号这件事一定会忘。
     */
    public record ExecutionFingerprint(String fillBasis, String adjustMode,
                                       int moneyPolicyVersion, String engineVersion) {

        public Map<String, Object> asMap() {
            return Map.of(
                    "fill_basis", fillBasis,
                    "adjust_mode", adjustMode,
                    "money_policy_version", moneyPolicyVersion,
                    "engine_version", engineVersion);
        }

        public boolean matches(ExecutionFingerprint other) {
            return other != null && asMap().equals(other.asMap());
        }
    }

    /**
     * 两份结果能不能对比。返回 null 表示可比，否则返回**不可比的原因**（可直接展示给用户）。
     *
     * <p>把"不可比"做成显式结论，而不是硬凑一个差额：差额看起来像信息，实际是错误。
     */
    public static String compareBlockReason(ExecutionFingerprint a, ExecutionFingerprint b) {
        if (a == null || b == null) {
            return "缺少执行指纹，无法确认口径是否一致";
        }
        if (a.matches(b)) {
            return null;
        }
        StringBuilder detail = new StringBuilder();
        Map<String, Object> mine = a.asMap();
        Map<String, Object> theirs = b.asMap();
        for (Map.Entry<String, Object> entry : mine.entrySet()) {
            Object other = theirs.get(entry.getKey());
            if (!entry.getValue().equals(other)) {
                if (detail.length() > 0) {
                    detail.append("、");
                }
                detail.append(entry.getKey()).append("(").append(entry.getValue())
                        .append(" vs ").append(other).append(")");
            }
        }
        return "口径不同，不可比：" + detail;
    }
}
