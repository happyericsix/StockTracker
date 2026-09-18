package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.service.ExecutionContract;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * 痕迹的读取视图：一条决策 + 它的证据 + **一句人话**。
 *
 * <h3>为什么"一句人话"是确定性拼出来的，而不是模型写的</h3>
 * P0 的验收标准是"随机抽 5 笔、5 次阻塞跳过，不看代码能明白为什么这一刻"。
 * 这个要求不该依赖模型可用性，也不该在每次查看时重新生成一段可能不一样的文字 ——
 * 那会让"为什么没成交"的答案本身变成不确定的东西。
 * 所以这里是 {@code skip_reason → 人话} 的**确定性映射**（每个封闭枚举值都有一句），
 * 模型要解释可以在这句话之上再讲，但不能替代它。
 */
public class PaperTradeTraceResponse {

    private Long id;
    private Long strategyId;
    private String symbol;
    private String settlementKind;
    private String trigger;
    private LocalDate tradeDate;
    private String barTime;
    private String barDate;
    private String decision;
    private String skipReason;
    private String signal;
    private String matchedConditions;
    private String fillBasis;
    private BigDecimal barClose;
    private BigDecimal cashBefore;
    private BigDecimal sharesBefore;
    private BigDecimal avgCostBefore;
    private BigDecimal equityBefore;
    private BigDecimal cashAfter;
    private BigDecimal sharesAfter;
    private BigDecimal avgCostAfter;
    private BigDecimal equityAfter;
    private String engineVersion;
    private String adjustMode;
    private Integer moneyPolicyVersion;
    private Integer snapshotSchemaVersion;
    private String snapshotJson;
    private String traceHash;
    private Integer repeatCount;
    private LocalDateTime createdAt;
    private LocalDateTime lastSeenAt;
    /** 一句人话（确定性）。 */
    private String summary;

    public PaperTradeTraceResponse() {
    }

    public static PaperTradeTraceResponse from(PaperTradeTrace entity) {
        if (entity == null) {
            return null;
        }
        PaperTradeTraceResponse response = new PaperTradeTraceResponse();
        response.id = entity.getId();
        response.strategyId = entity.getStrategy() == null ? null : entity.getStrategy().getId();
        response.symbol = entity.getSymbol();
        response.settlementKind = entity.getSettlementKind();
        response.trigger = entity.getTrigger();
        response.tradeDate = entity.getTradeDate();
        response.barTime = entity.getBarTime();
        response.barDate = entity.getBarDate();
        response.decision = entity.getDecision();
        response.skipReason = entity.getSkipReason();
        response.signal = entity.getSignal();
        response.matchedConditions = entity.getMatchedConditions();
        response.fillBasis = entity.getFillBasis();
        response.barClose = entity.getBarClose();
        response.cashBefore = entity.getCashBefore();
        response.sharesBefore = entity.getSharesBefore();
        response.avgCostBefore = entity.getAvgCostBefore();
        response.equityBefore = entity.getEquityBefore();
        response.cashAfter = entity.getCashAfter();
        response.sharesAfter = entity.getSharesAfter();
        response.avgCostAfter = entity.getAvgCostAfter();
        response.equityAfter = entity.getEquityAfter();
        response.engineVersion = entity.getEngineVersion();
        response.adjustMode = entity.getAdjustMode();
        response.moneyPolicyVersion = entity.getMoneyPolicyVersion();
        response.snapshotSchemaVersion = entity.getSnapshotSchemaVersion();
        response.snapshotJson = entity.getSnapshotJson();
        response.traceHash = entity.getTraceHash();
        response.repeatCount = entity.getRepeatCount();
        response.createdAt = entity.getCreatedAt();
        response.lastSeenAt = entity.getLastSeenAt();
        response.summary = summarize(entity);
        return response;
    }

    /**
     * 每个封闭枚举值都**必须**有一句人话（有测试盯着这件事）。
     *
     * <p>没有兜底就没法读：痕迹里出现一个没人翻译的原因，读者只能回去读代码 ——
     * 而"不看代码能明白"正是这一层存在的理由。
     */
    private static final Map<String, String> SKIP_SENTENCES = skipSentences();

    private static Map<String, String> skipSentences() {
        Map<String, String> map = new LinkedHashMap<>();
        map.put(ExecutionContract.SKIP_RULE_NOT_MET, "规则条件不成立，按策略不动");
        map.put(ExecutionContract.SKIP_WARMUP, "指标窗口还没凑够，这条规则当时不可能触发");
        map.put(ExecutionContract.SKIP_NO_BAR, "这一天没有 K 线（休市或数据未出）");
        map.put(ExecutionContract.SKIP_MARKET_CLOSED, "非交易日");
        map.put(ExecutionContract.SKIP_SUSPENDED, "标的停牌");
        map.put(ExecutionContract.SKIP_DATA_UNAVAILABLE, "取不到行情或数据不可用");
        map.put(ExecutionContract.SKIP_LIMIT_BLOCKED, "涨跌停挡单，买不进或卖不出");
        map.put(ExecutionContract.SKIP_T1_BLOCKED, "T+1：当日买入的当日不能卖");
        map.put(ExecutionContract.SKIP_EX_DIVIDEND_DAY, "除权除息日，跳过并标注");
        map.put(ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT, "现金不足一手，买不进");
        map.put(ExecutionContract.SKIP_INSUFFICIENT_CASH, "现金不足");
        map.put(ExecutionContract.SKIP_INVALID_PRICE, "价格无效，无法成交");
        map.put(ExecutionContract.SKIP_STATE_MISMATCH, "信号与账户状态不一致（已持仓收到买入信号 / 空仓收到卖出信号），本轮没动作");
        map.put(ExecutionContract.SKIP_AGENT_UNPARSABLE, "agent 的输出读不懂（没有规范结论行或结构不完整），按不动处理");
        return map;
    }

    /** 给报告与界面用：这个原因对应的人话（未知值也给出可读的一句，不返回 null）。 */
    public static String sentenceFor(String skipReason) {
        if (skipReason == null || skipReason.isBlank()) {
            return "有成交";
        }
        String sentence = SKIP_SENTENCES.get(skipReason);
        if (sentence != null) {
            return sentence;
        }
        if (skipReason.startsWith(ExecutionContract.UNKNOWN_PREFIX)) {
            return "出现了未登记的原因（" + skipReason + "），需要核对";
        }
        return skipReason;
    }

    private static String summarize(PaperTradeTrace entity) {
        StringBuilder text = new StringBuilder();
        text.append(entity.getTradeDate() == null ? "" : entity.getTradeDate().toString());
        if (entity.getBarTime() != null && !entity.getBarTime().isBlank()) {
            text.append(' ').append(entity.getBarTime());
        }
        text.append("（").append(entity.getSymbol() == null ? "" : entity.getSymbol()).append("）");

        if (ExecutionContract.DECISION_SKIP.equals(entity.getDecision())) {
            text.append(" 未成交：").append(sentenceFor(entity.getSkipReason()));
            if (ExecutionContract.SKIP_RULE_NOT_MET.equals(entity.getSkipReason())
                    || ExecutionContract.SKIP_WARMUP.equals(entity.getSkipReason())) {
                // 这两种"不动"是**正常状态**（不是异常），把当时的持仓与净值一起写出来，
                // 否则读者分不清"没动"与"没跑"
                text.append("；当时净值 ").append(entity.getEquityAfter())
                        .append("，持仓 ").append(entity.getSharesAfter()).append(" 股");
            }
        } else {
            boolean buying = ExecutionContract.DECISION_BUY.equals(entity.getDecision());
            text.append(buying ? " 成交：买入" : " 成交：卖出")
                    .append("，价格 ").append(entity.getBarClose())
                    .append("（口径 ").append(entity.getFillBasis()).append("）")
                    .append("，成交后净值 ").append(entity.getEquityAfter());
        }
        if (entity.getRepeatCount() != null && entity.getRepeatCount() > 1) {
            text.append("；同一结论重复 ").append(entity.getRepeatCount()).append(" 次");
        }
        return text.toString();
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public Long getStrategyId() { return strategyId; }
    public void setStrategyId(Long strategyId) { this.strategyId = strategyId; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getSettlementKind() { return settlementKind; }
    public void setSettlementKind(String settlementKind) { this.settlementKind = settlementKind; }
    public String getTrigger() { return trigger; }
    public void setTrigger(String trigger) { this.trigger = trigger; }
    public LocalDate getTradeDate() { return tradeDate; }
    public void setTradeDate(LocalDate tradeDate) { this.tradeDate = tradeDate; }
    public String getBarTime() { return barTime; }
    public void setBarTime(String barTime) { this.barTime = barTime; }
    public String getBarDate() { return barDate; }
    public void setBarDate(String barDate) { this.barDate = barDate; }
    public String getDecision() { return decision; }
    public void setDecision(String decision) { this.decision = decision; }
    public String getSkipReason() { return skipReason; }
    public void setSkipReason(String skipReason) { this.skipReason = skipReason; }
    public String getSignal() { return signal; }
    public void setSignal(String signal) { this.signal = signal; }
    public String getMatchedConditions() { return matchedConditions; }
    public void setMatchedConditions(String matchedConditions) { this.matchedConditions = matchedConditions; }
    public String getFillBasis() { return fillBasis; }
    public void setFillBasis(String fillBasis) { this.fillBasis = fillBasis; }
    public BigDecimal getBarClose() { return barClose; }
    public void setBarClose(BigDecimal barClose) { this.barClose = barClose; }
    public BigDecimal getCashBefore() { return cashBefore; }
    public void setCashBefore(BigDecimal cashBefore) { this.cashBefore = cashBefore; }
    public BigDecimal getSharesBefore() { return sharesBefore; }
    public void setSharesBefore(BigDecimal sharesBefore) { this.sharesBefore = sharesBefore; }
    public BigDecimal getAvgCostBefore() { return avgCostBefore; }
    public void setAvgCostBefore(BigDecimal avgCostBefore) { this.avgCostBefore = avgCostBefore; }
    public BigDecimal getEquityBefore() { return equityBefore; }
    public void setEquityBefore(BigDecimal equityBefore) { this.equityBefore = equityBefore; }
    public BigDecimal getCashAfter() { return cashAfter; }
    public void setCashAfter(BigDecimal cashAfter) { this.cashAfter = cashAfter; }
    public BigDecimal getSharesAfter() { return sharesAfter; }
    public void setSharesAfter(BigDecimal sharesAfter) { this.sharesAfter = sharesAfter; }
    public BigDecimal getAvgCostAfter() { return avgCostAfter; }
    public void setAvgCostAfter(BigDecimal avgCostAfter) { this.avgCostAfter = avgCostAfter; }
    public BigDecimal getEquityAfter() { return equityAfter; }
    public void setEquityAfter(BigDecimal equityAfter) { this.equityAfter = equityAfter; }
    public String getEngineVersion() { return engineVersion; }
    public void setEngineVersion(String engineVersion) { this.engineVersion = engineVersion; }
    public String getAdjustMode() { return adjustMode; }
    public void setAdjustMode(String adjustMode) { this.adjustMode = adjustMode; }
    public Integer getMoneyPolicyVersion() { return moneyPolicyVersion; }
    public void setMoneyPolicyVersion(Integer moneyPolicyVersion) { this.moneyPolicyVersion = moneyPolicyVersion; }
    public Integer getSnapshotSchemaVersion() { return snapshotSchemaVersion; }
    public void setSnapshotSchemaVersion(Integer snapshotSchemaVersion) { this.snapshotSchemaVersion = snapshotSchemaVersion; }
    public String getSnapshotJson() { return snapshotJson; }
    public void setSnapshotJson(String snapshotJson) { this.snapshotJson = snapshotJson; }
    public String getTraceHash() { return traceHash; }
    public void setTraceHash(String traceHash) { this.traceHash = traceHash; }
    public Integer getRepeatCount() { return repeatCount; }
    public void setRepeatCount(Integer repeatCount) { this.repeatCount = repeatCount; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getLastSeenAt() { return lastSeenAt; }
    public void setLastSeenAt(LocalDateTime lastSeenAt) { this.lastSeenAt = lastSeenAt; }
    public String getSummary() { return summary; }
    public void setSummary(String summary) { this.summary = summary; }
}
