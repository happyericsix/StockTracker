package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.service.PaperEquitySeries;
import com.happyericsix.stocktracker.service.PaperSchedule;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

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

    /**
     * 委员会活性（P2，2026-10-28）：回答"agent 到底还看不看盘"——2026-10-28 生产事故
     * 的教训：门控的**正常态就是沉默**，沉默的原因必须主动可见，否则每个安静的日子
     * 都像死机。全部从痕迹表推导（lastCommitteeAt 按"agent_llm_calls&gt;0 的最新一条"，
     * 不信任何每天刷新的可变字段——那个会被静默喂饱）。
     */
    public record CommitteeStatus(
            /** 上次**真开会**的日子（YYYY-MM-DD）；null = 从没开过。 */
            LocalDate lastCommitteeAt,
            /** 距上次真开会的自然日数；null = 从没开过。 */
            Integer daysSinceRealMeeting,
            /** stale 保底：再不开会也会被强制召集的日子；null = 从没开过（首次决策随时触发）。 */
            LocalDate nextForcedMeetingAt,
            /** 持仓与否决定保底天数（持仓盯得紧）——前端不必知道规则，只看日期。 */
            boolean holding,
            /** 最近一次**门控判定**的触发理由（逗号分隔；空 = 没开会也没触发）。 */
            String lastGateReasons,
            /** 观察员最近一次扫视的注意力档位（low/med/high）；null = 观察员缺席或未启用。 */
            String observerAttention,
            /** 观察员最近一次扫视的一句话备注；null = 同上。 */
            String observerNote) {
    }

    /**
     * 前日预案状态（前日预案制，agent 模式才有）：当前生效/最近一份预案的摘要。
     * "active"=今天在生效窗口内；用户在页面上要能一眼看到"明天什么条件下动手"。
     */
    public record PlanStatus(
            /** 今天是否处于预案生效窗口内（盘中据此接管）。 */
            boolean active,
            /** 预案生效首日（= 签发结算日的下一交易日）。 */
            LocalDate planDate,
            /** 预案最后一个有效日。 */
            LocalDate validUntil,
            /** 委员会写的人话摘要（≤200字）；空串 = 没写。 */
            String summary,
            /** 入场条目数 / 出场条目数。 */
            int entries,
            int exits,
            /** 签发时间（哪次会写的）。 */
            LocalDateTime issuedAt) {
    }

    private Long strategyId;
    private String name;
    private String symbol;
    private boolean paperEnabled;
    /** rule / agent（存量策略的 null 已归一成 rule）。 */
    private String decisionMode;
    private LocalDate decisionModeSince;
    private java.time.LocalDateTime configUpdatedAt;
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
    /** 委员会活性（agent 模式才有；rule 模式为 null）。 */
    private CommitteeStatus committeeStatus;
    /** 前日预案状态（agent 模式才有；没签过预案为 null）。 */
    private PlanStatus planStatus;

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

    /** 规则最近一次被用户修改（null=从未）：页面据此提示"前后口径不可比"。 */
    public java.time.LocalDateTime getConfigUpdatedAt() { return configUpdatedAt; }
    public void setConfigUpdatedAt(java.time.LocalDateTime configUpdatedAt) { this.configUpdatedAt = configUpdatedAt; }
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
    public CommitteeStatus getCommitteeStatus() { return committeeStatus; }
    public void setCommitteeStatus(CommitteeStatus committeeStatus) { this.committeeStatus = committeeStatus; }
    public PlanStatus getPlanStatus() { return planStatus; }
    public void setPlanStatus(PlanStatus planStatus) { this.planStatus = planStatus; }
}
