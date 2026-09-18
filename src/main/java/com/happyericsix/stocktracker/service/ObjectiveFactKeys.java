package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;

import java.time.LocalDateTime;

/**
 * 客观事实（{@code provenance=system}）的键与构造器。
 *
 * <h3>为什么需要这样一个文件</h3>
 * 记忆系统原本只有一条写入路径：会话巩固（把"用户说过的话"抽成事实与经验）。
 * 于是记忆里全是<b>用户的主张</b>，没有<b>系统验证过的观测</b> ——
 * "这只策略回测最大回撤 18.3%"、"模拟盘净值 9.7 万"这类可复算的事实一条都进不去，
 * 而它们恰恰是"改进策略"唯一可靠的依据。
 *
 * 这个类定义第二条通道的**键**。键必须由代码定义、且两边一致，理由与
 * {@code facts.py} 用别名表收敛中文谓词完全相同：取代链能工作的前提是
 * <b>同一个东西永远落在同一个键上</b>。一处写 {@code backtest_drawdown}、
 * 另一处写 {@code backtest_max_drawdown_pct}，系统里就会出现两个"回撤"事实
 * 永远互相不取代 —— 每条单独看都对，合起来自相矛盾。
 *
 * <h3>与 Python 侧的契约</h3>
 * {@link #BACKTEST_TOTAL_RETURN_PCT} 起的一组常量与
 * {@code python-data-service/agent/objective.py} 的 {@code PREDICATES} 元组
 * <b>必须逐字一致</b>，由 {@code tests/test_objective_facts.py} 的跨语言一致性测试钉住
 * （它按下面的标记注释切出这一段再逐个比对）。这个项目里"两边名字对不上就静默失效"
 * 已经发生过太多次（{@code user_id} / {@code user_name} 那次整条链路看起来像坏了却没有报错）。
 *
 * <h3>subject 用数字 id，不用策略名</h3>
 * 模型抽出的策略事实写 {@code strategy:名称}（人叫得出的名字），这里写
 * {@code strategy:<数字 id>}：名称会改、会重名，取代链必须挂在稳定标识上。
 * 两套命名空间刻意<b>不合并</b> —— 合并需要一个"名称 → id"的映射，
 * 而那个映射只有持有数据的 Java 侧掌握，记忆层不该去猜。
 */
public final class ObjectiveFactKeys {

    /** 客观事实的固定标注：代码算出来的观测，既不是用户说的，也不是模型推断的。 */
    public static final String PROVENANCE_SYSTEM = "system";
    public static final String TRUST_HIGH = "high";
    public static final String FACT_TYPE_OBSERVATION = "observation";
    public static final double CONFIDENCE_CERTAIN = 1.0;

    private ObjectiveFactKeys() {
    }

    // >>> OBJECTIVE_PREDICATES —— 与 Python 侧 objective.PREDICATES 一一对应，勿单独改动
    /** 回测：总收益（%） */
    public static final String BACKTEST_TOTAL_RETURN_PCT = "backtest_total_return_pct";
    /** 回测：同期买入持有收益（%） */
    public static final String BACKTEST_BUY_AND_HOLD_RETURN_PCT = "backtest_buy_and_hold_return_pct";
    /** 回测：超额收益（%） */
    public static final String BACKTEST_EXCESS_RETURN_PCT = "backtest_excess_return_pct";
    /** 回测：最大回撤（%） */
    public static final String BACKTEST_MAX_DRAWDOWN_PCT = "backtest_max_drawdown_pct";
    /** 回测：夏普比率 */
    public static final String BACKTEST_SHARPE = "backtest_sharpe";
    /** 回测：胜率（%）。引擎字段名是 win_rate（无 _pct 后缀），键名统一带 _pct */
    public static final String BACKTEST_WIN_RATE_PCT = "backtest_win_rate_pct";
    /** 回测：交易笔数 */
    public static final String BACKTEST_TRADE_COUNT = "backtest_trade_count";
    /** 回测：这次回测是什么时候跑的 */
    public static final String BACKTEST_AT = "backtest_at";
    /** 模拟盘：账户净值 */
    public static final String PAPER_EQUITY = "paper_equity";
    /** 模拟盘：可用现金 */
    public static final String PAPER_CASH = "paper_cash";
    /** 模拟盘：持仓股数 */
    public static final String PAPER_SHARES = "paper_shares";
    /** 模拟盘：相对初始本金的收益率（%） */
    public static final String PAPER_RETURN_PCT = "paper_return_pct";
    /** 模拟盘：最近一次结算时间 */
    public static final String PAPER_LAST_EVAL_AT = "paper_last_eval_at";
    /** 模拟盘：至今最大回撤（%，≤0）—— 由每日净值快照序列算出，单点算不出来就不记 */
    public static final String PAPER_MAX_DRAWDOWN_PCT = "paper_max_drawdown_pct";
    /** 模拟盘：当前连续空仓的交易日数（"已经多久没动了"） */
    public static final String PAPER_FLAT_DAYS = "paper_flat_days";
    // —— 样本外验证（多标的 × 多时段）：**"这条规则到底行不行"的可复算回答** ——
    // 为什么它必须进客观事实：盘后复盘要引用它，P2 的讨论协议要拿它当裁决依据，
    // 而"引用"的前提是有一份**带时间戳、可被替代、可查历史**的记录。
    // 写在对话里的话做不到这件事（下一轮就找不到了）。
    /** 验证：这次验证是何时跑的 */
    public static final String VERIFY_AT = "verify_at";
    /** 验证：有效格子数（标的 × 时段，扣掉预热/买不起一手的格子）＝**样本量** */
    public static final String VERIFY_VALID_CELLS_COUNT = "verify_valid_cells_count";
    /** 验证：跑赢买入持有的格子数 */
    public static final String VERIFY_BEAT_BUY_AND_HOLD_COUNT = "verify_beat_buy_and_hold_count";
    /** 验证：平均超额收益（%） */
    public static final String VERIFY_AVG_EXCESS_PCT = "verify_avg_excess_pct";
    /** 验证：摩擦平均占本金的比例（%）—— 亏损里有多少是换手磨掉的 */
    public static final String VERIFY_FEE_DRAG_PCT = "verify_fee_drag_pct";
    /** 验证：引擎版本（口径之一；与回测/模拟盘不同则数字不可比） */
    public static final String VERIFY_ENGINE_VERSION = "verify_engine_version";
    // —— 预期登记与回填（建议闭环）——
    // 复用客观事实通道而不是新建表：取代链天然给出"上一次预期是什么、有没有达成"的历史，
    // 模型也能通过既有检索读到它 —— 而写在对话里的承诺下一轮就找不到了。
    /** 预期：登记时间 */
    public static final String EXPECTATION_AT = "expectation_at";
    /** 预期：度量（封闭集，见 ExpectationService；都是账户层面可观测值，**不是股价预测**） */
    public static final String EXPECTATION_METRIC = "expectation_metric";
    /** 预期：门槛（度量 ≥ 门槛 记为达成） */
    public static final String EXPECTATION_THRESHOLD = "expectation_threshold";
    /** 预期：到期日（到这天之后回填） */
    public static final String EXPECTATION_DEADLINE = "expectation_deadline";
    /** 预期：状态（pending | met | unmet | unmeasurable） */
    public static final String EXPECTATION_STATUS = "expectation_status";
    /** 预期：到期时的实际度量值 */
    public static final String EXPECTATION_OUTCOME = "expectation_outcome";
    /** 预期：回填时间 */
    public static final String EXPECTATION_EVALUATED_AT = "expectation_evaluated_at";
    // <<< OBJECTIVE_PREDICATES

    /**
     * 策略事实的 subject。
     *
     * <p>{@code strategyId} 为空时返回空串，调用方据此跳过 ——
     * 一条"不知道属于谁"的观测事实比没有更糟：它查不出来源，还会污染画像。
     */
    public static String strategySubject(Long strategyId) {
        return strategyId == null ? "" : "strategy:" + strategyId;
    }

    /**
     * 构造一条客观事实。参数不合法时返回 {@code null}（调用方跳过），不做宽容修补。
     *
     * <p>为什么在这里就把标注写死：{@code provenance/trust/confirmed/confidence}
     * 这四个字段表达的是同一件事 —— "这不是谁的主张，是可复算的观测"。
     * 让调用方各自填，迟早有人漏一个，而那正是"模型推断的东西被当成事实"的入口。
     */
    public static MemoryFactRequest observation(String subject, String predicate, Object value,
                                                LocalDateTime dataAsOf) {
        if (subject == null || subject.isBlank() || predicate == null || predicate.isBlank()
                || value == null) {
            return null;
        }
        if (value instanceof Double number && !Double.isFinite(number)) {
            // NaN/Infinity 不是可比较的观测：写进去只会取代掉一个真实的值
            return null;
        }
        MemoryFactRequest fact = new MemoryFactRequest();
        fact.setSubject(subject);
        fact.setPredicate(predicate);
        fact.setObject(format(value));
        fact.setFactType(FACT_TYPE_OBSERVATION);
        fact.setConfidence(CONFIDENCE_CERTAIN);
        fact.setDataAsOf(dataAsOf);
        fact.setProvenance(PROVENANCE_SYSTEM);
        fact.setTrust(TRUST_HIGH);
        fact.setConfirmed(true);
        return fact;
    }

    /**
     * 值的字符串化：与 Python 侧 {@code objective.build_fact} 保持同一口径。
     *
     * <p>两条规则，都是为了**取代链不被虚假变更污染**：
     * <ul>
     *   <li>浮点只留 3 位：回测指标本来就带噪声，多留几位只会让取代链"每天都在变"；</li>
     *   <li>整数值的浮点归一成整数：同一个量在 JSON 里可能是 {@code 7} 也可能是 {@code 7.0}
     *       （Jackson 的 {@code ObjectNode.put(String, int)} 会按 {@code double} 重载落成 7.0），
     *       不归一就会出现"值没变、取代链却多了一条"的假变更 ——
     *       而这正是"什么时候真的变了"这个问题的答案来源。</li>
     * </ul>
     */
    static String format(Object value) {
        if (value instanceof Boolean flag) {
            return flag ? "true" : "false";
        }
        if (value instanceof java.math.BigDecimal decimal) {
            // BigDecimal 走**同一套**规则（≤3 位小数、整数归一成整数）：
            // 让金额以精确值进来、以统一格式出去。少了这一支，同一个量会写成
            // "-25.0000" 与 "-25" 两种形式 —— 那正是"值没变、取代链却多一条"的来源。
            return format(decimal.doubleValue());
        }
        if (value instanceof Double || value instanceof Float) {
            double rounded = Math.round(((Number) value).doubleValue() * 1000.0) / 1000.0;
            if (rounded == Math.rint(rounded) && !Double.isInfinite(rounded)) {
                return String.valueOf((long) rounded);
            }
            return String.valueOf(rounded);
        }
        return String.valueOf(value);
    }
}
