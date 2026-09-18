package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.service.PaperEquitySeries;
import com.happyericsix.stocktracker.service.PaperSchedule;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

/**
 * 模拟盘总览：**一次请求回答四个问题**。
 *
 * <ol>
 *   <li>在跑什么：策略、标的、决策来源（规则 / 委员会）、本金、是否已启动；</li>
 *   <li>赚没赚：净值、期间收益、**相对买入持有的超额**、最大回撤、空仓占比、样本天数；</li>
 *   <li>今天动没动：最近一次结算（哪天、什么结论、为什么没成交 —— 一句人话）；</li>
 *   <li>下次什么时候：下一次日线结算时间与口径说明。</li>
 * </ol>
 *
 * <h3>为什么必须是一个聚合端点</h3>
 * 前端若按策略数分别去取账户/净值/痕迹/预期，N 条策略就是 4N 次请求 ——
 * 页面又慢又难维护。更重要的是：<b>"下一次评估时间""超额怎么算"这些口径必须只有一处实现</b>，
 * 分散到前端拼装迟早会出现"总览页和详情页数字不一样"。
 *
 * <p>汇总数字全部来自 {@link PaperEquitySeries#summarize} —— 与详情页、周报同一份算法。
 */
public class PaperOverviewResponse {

    /** 最近一次结算：给"今天到底动没动"一个确定的答案。 */
    public record LastSettlement(LocalDate tradeDate, String settlementKind, String decision,
                                 String skipReason, String signal, String decisionMode,
                                 Integer agentLlmCalls, Integer agentTokens, String trigger,
                                 LocalDateTime at,
                                 /**
                                  * 一句人话（确定性，不由模型生成）。
                                  *
                                  * <p>直接用痕迹读视图的**完整句子**（成交/未成交都写全），
                                  * 而不是只把 {@code skip_reason} 翻成一个词：后者在成交那天会输出
                                  * "有成交" 这种等于没说的话，而这一行恰恰要回答"今天动没动、动了多少"。
                                  */
                                 String sentence) {
    }

    /** 当前有效的可验证预期（没登记过就是 null —— "没有承诺"与"承诺到期未知"必须分得开）。 */
    public record Expectation(String metric, String metricLabel, BigDecimal threshold,
                              LocalDate deadline, String status, BigDecimal outcome,
                              boolean pending, String sentence) {
    }

    private Long strategyId;
    private String name;
    private String symbol;
    private boolean paperEnabled;
    /** rule / agent（存量策略的 null 已归一成 rule）。 */
    private String decisionMode;
    private LocalDate decisionModeSince;
    private BigDecimal initialCapital;
    /** 账户是否被评估过至少一次；false 时下面那些数字都还没有意义。 */
    private boolean evaluated;
    private BigDecimal equity;
    private BigDecimal cash;
    private BigDecimal shares;
    private BigDecimal avgCost;
    private BigDecimal lastPrice;
    /** 持仓市值 = 股数 × 最新价（拿不到最新价时为 null，不拿成本冒充）。 */
    private BigDecimal positionValue;
    private String lastSignal;
    private LocalDateTime lastEvalAt;
    /** 由净值序列算出的汇总；没有净值点时为 null。 */
    private PaperEquitySeries.Summary summary;
    private int traceCount;
    private LastSettlement lastSettlement;
    private Expectation expectation;
    private PaperSchedule.NextEvaluation nextEvaluation;
    /** 下一次日线结算的人话（含"按工作日近似"的边界）。 */
    private String nextEvaluationNote;

    public static List<String> metrics() {
        return PaperEquitySeries.METRICS;
    }

    public Long getStrategyId() { return strategyId; }
    public void setStrategyId(Long strategyId) { this.strategyId = strategyId; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public boolean isPaperEnabled() { return paperEnabled; }
    public void setPaperEnabled(boolean paperEnabled) { this.paperEnabled = paperEnabled; }
    public String getDecisionMode() { return decisionMode; }
    public void setDecisionMode(String decisionMode) { this.decisionMode = decisionMode; }
    public LocalDate getDecisionModeSince() { return decisionModeSince; }
    public void setDecisionModeSince(LocalDate decisionModeSince) { this.decisionModeSince = decisionModeSince; }
    public BigDecimal getInitialCapital() { return initialCapital; }
    public void setInitialCapital(BigDecimal initialCapital) { this.initialCapital = initialCapital; }
    public boolean isEvaluated() { return evaluated; }
    public void setEvaluated(boolean evaluated) { this.evaluated = evaluated; }
    public BigDecimal getEquity() { return equity; }
    public void setEquity(BigDecimal equity) { this.equity = equity; }
    public BigDecimal getCash() { return cash; }
    public void setCash(BigDecimal cash) { this.cash = cash; }
    public BigDecimal getShares() { return shares; }
    public void setShares(BigDecimal shares) { this.shares = shares; }
    public BigDecimal getAvgCost() { return avgCost; }
    public void setAvgCost(BigDecimal avgCost) { this.avgCost = avgCost; }
    public BigDecimal getLastPrice() { return lastPrice; }
    public void setLastPrice(BigDecimal lastPrice) { this.lastPrice = lastPrice; }
    public BigDecimal getPositionValue() { return positionValue; }
    public void setPositionValue(BigDecimal positionValue) { this.positionValue = positionValue; }
    public String getLastSignal() { return lastSignal; }
    public void setLastSignal(String lastSignal) { this.lastSignal = lastSignal; }
    public LocalDateTime getLastEvalAt() { return lastEvalAt; }
    public void setLastEvalAt(LocalDateTime lastEvalAt) { this.lastEvalAt = lastEvalAt; }
    public PaperEquitySeries.Summary getSummary() { return summary; }
    public void setSummary(PaperEquitySeries.Summary summary) { this.summary = summary; }
    public int getTraceCount() { return traceCount; }
    public void setTraceCount(int traceCount) { this.traceCount = traceCount; }
    public LastSettlement getLastSettlement() { return lastSettlement; }
    public void setLastSettlement(LastSettlement lastSettlement) { this.lastSettlement = lastSettlement; }
    public Expectation getExpectation() { return expectation; }
    public void setExpectation(Expectation expectation) { this.expectation = expectation; }
    public PaperSchedule.NextEvaluation getNextEvaluation() { return nextEvaluation; }
    public void setNextEvaluation(PaperSchedule.NextEvaluation nextEvaluation) { this.nextEvaluation = nextEvaluation; }
    public String getNextEvaluationNote() { return nextEvaluationNote; }
    public void setNextEvaluationNote(String nextEvaluationNote) { this.nextEvaluationNote = nextEvaluationNote; }
}
