package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 盘后复盘报告：**引用痕迹与验证结论**，并且不许假装。
 *
 * <h3>为什么这些用例是这一层的核心</h3>
 * 报告的失败方式不是崩溃，而是"写得像那么回事"：把"没验证过"写成"表现平平"、
 * 把过期的结论当成此刻的证据、把"今天什么都没发生"每天推一条。
 * 这些都不会报错，只会让用户慢慢不再打开它 —— 那正是模拟盘最常见的死法。
 */
class PaperReviewReportServiceTest {

    private final PaperTraceService traceService = mock(PaperTraceService.class);
    private final PaperEquitySnapshotRepository equityRepository =
            mock(PaperEquitySnapshotRepository.class);
    private final StrategyService strategyService = mock(StrategyService.class);
    private final PaperReviewReportService service =
            new PaperReviewReportService(traceService, equityRepository, strategyService,
                    mock(ExpectationService.class));

    private static final LocalDate DAY = LocalDate.of(2026, 9, 17);

    private static Strategy strategy() {
        return Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .user(User.builder().id(1L).username("alice").email("a@b.c").password("x").build())
                .build();
    }

    private static PaperAccount account() {
        return PaperAccount.builder()
                .id(5L).initialCapital(new java.math.BigDecimal("100000.00"))
                .cash(new java.math.BigDecimal("100.00")).shares(new java.math.BigDecimal("900.0000"))
                .avgCost(new java.math.BigDecimal("10.0000"))
                .equity(new java.math.BigDecimal("100900.00")).build();
    }

    private static PaperTradeTrace trace(String decision, String skipReason, String barTime) {
        return PaperTradeTrace.builder()
                .id(1L).strategy(strategy()).settlementKind(ExecutionContract.SETTLEMENT_REALTIME)
                .trigger(ExecutionContract.TRIGGER_EVENT).tradeDate(DAY).barTime(barTime)
                .symbol("600519").decision(decision).skipReason(skipReason)
                .fillBasis(ExecutionContract.FILL_REALTIME_LAST)
                .repeatCount(1)
                .build();
    }

    // ==================== 2.9 2026-10-09 线上事故的三条回归 ====================

    /**
     * 消息中心按**纯文本**渲染（white-space: pre-wrap，无 markdown），
     * 报告文案里任何 `**` 都会原样露出（当天实发："委员会开会后**明确关闭了盘中执行**"）。
     * 这条用例把"报告是纯文本"钉成契约：所有措辞来源（跳过原因翻译、决策来源行、
     * 验证行）都不许带 markdown 标记。
     */
    @Test
    void reportsArePlainTextWithoutMarkdownMarkers() {
        Strategy agentStrategy = Strategy.builder()
                .id(10L).name("均线上穿").symbol("600519")
                .user(strategy().getUser())
                .decisionMode("agent").decisionModeSince(LocalDate.of(2026, 10, 6))
                .build();
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_AGENT_INTRADAY_DISABLED,
                        "2026-09-17 09:40:00"),
                PaperTradeTrace.builder()
                        .id(2L).strategy(agentStrategy).settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                        .trigger(ExecutionContract.TRIGGER_CRON).tradeDate(DAY)
                        .symbol("600519").decision(ExecutionContract.DECISION_SKIP)
                        .skipReason(ExecutionContract.SKIP_NO_BAR).repeatCount(3)
                        .build()));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        String text = service.dailyReportText(agentStrategy, account(), DAY);
        assertNotNull(text);

        assertNotNull(text);
        assertFalse(text.contains("**"),
                "报告是纯文本消息，不许带 markdown 标记：" + text);
        // agent 模式与未验证的措辞确实被走到了（不然上面那条断言是空转）
        assertTrue(text.contains("agent 多角色委员会"), text);
        assertTrue(text.contains("单个标的的偶然"), text);
    }

    /**
     * 盘中痕迹的 barTime 是完整时间戳，summarize 已经写过 tradeDate ——
     * 再拼一遍就是"2026-09-17 2026-09-17 09:40:00"（当天实发）。
     */
    @Test
    void theSummaryDoesNotRepeatTheDateForIntradayTraces() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_AGENT_INTRADAY_DISABLED,
                        "2026-09-17 09:40:00")));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertNotNull(text);
        assertFalse(text.contains("2026-09-17 2026-09-17"),
                "日期不许重复拼接：" + text);
        assertTrue(text.contains("2026-09-17 09:40:00"), text);
    }

    /**
     * 盘中评估次数数的是**实际评估**（repeat_count），不是去重后的痕迹行数 ——
     * 否则同一条消息里出现"盘中评估 1 次"与"同一结论重复 317 次"的自相矛盾
     * （当天实发：317 轮盯盘全在跑，日报却说只评估了 1 次）。
     */
    @Test
    void intradayEvaluationCountCountsEveryRepeatNotDedupedRows() {
        PaperTradeTrace repeated = PaperTradeTrace.builder()
                .id(1L).strategy(strategy()).settlementKind(ExecutionContract.SETTLEMENT_REALTIME)
                .trigger(ExecutionContract.TRIGGER_EVENT).tradeDate(DAY).barTime("2026-09-17 14:57:00")
                .symbol("600519").decision(ExecutionContract.DECISION_SKIP)
                .skipReason(ExecutionContract.SKIP_AGENT_INTRADAY_DISABLED)
                .repeatCount(317)
                .build();
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(repeated));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertNotNull(text);
        assertTrue(text.contains("盘中评估 317 次"), text);
        assertFalse(text.contains("盘中评估 1 次"), text);
    }

    // ==================== 1. 什么值得推一条消息 ====================

    @Test
    void aBlockingSkipIsWorthTelling() {
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED, "2026-09-17 09:40:00")));
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_LIMIT_BLOCKED, "2026-09-17 09:40:00")));
    }

    @Test
    void aQuietDayIsNotWorthATableMessage() {
        // "规则没成立""指标没算出来"是**正常状态**：每天推一条等于训练用户忽略这类消息
        assertFalse(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        assertFalse(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_WARMUP, null)));
    }

    @Test
    void aFillIsAlwaysWorthTelling() {
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        assertTrue(PaperReviewReportService.isWorthTelling(
                trace(ExecutionContract.DECISION_SELL, null, null)));
    }

    @Test
    void aQuietDayProducesNoMessage() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));

        assertNull(service.dailyReportText(strategy(), account(), DAY));
    }

    @Test
    void aDayWithoutAnyTraceProducesNoMessageEither() {
        // 连痕迹都没有 = 结算没走到留痕那一步。这时候发"今天很平静"是**假话**
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of());

        assertNull(service.dailyReportText(strategy(), account(), DAY));
    }

    // ==================== 2. 日报内容：引用痕迹 + 验证结论 ====================

    @Test
    void theDailyReportCitesTheTraceAndTheVerification() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED, "2026-09-17 09:40:00"),
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 21, 10, -1.35, 1.883, "2a9dce9df73b"));

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertNotNull(text);
        // 引用痕迹：那句人话直接来自痕迹（同一套翻译，不另写一份）
        assertTrue(text.contains("T+1：当日买入的当日不能卖"), text);
        assertTrue(text.contains("2026-09-17"), text);
        // 引用验证结论：样本量 + 结论 + 摩擦 + 口径
        assertTrue(text.contains("21 个有效格子里 10 格跑赢买入持有"), text);
        assertTrue(text.contains("-1.35%"), text);
        assertTrue(text.contains("1.883%"), text);
        assertTrue(text.contains("2a9dce9df73b"), text);
        // 账户一行也在
        assertTrue(text.contains("净值 100900.00"), text);

    }

    @Test
    void aNeverVerifiedStrategyIsSaidToBeUnverifiedNotToBeFlat() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertTrue(text.contains("还没有做过样本外验证"), text);
        assertTrue(text.contains("偶然"), "没有验证时必须提醒这可能是偶然");
    }

    @Test
    void aVerificationWithoutSampleIsNotPresentedAsAConclusion() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 0, 0, null, null, ""));

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertTrue(text.contains("没有可用样本"), text);
        assertTrue(text.contains("不构成结论"), text);
    }

    @Test
    void aStaleVerificationIsMarkedStale() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-08-01T09:00:00", 21, 10, -1.35, 1.883, "abc"));

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertTrue(text.contains("未更新"), text);
        assertTrue(text.contains("不能当作此刻的证据"), text);
    }

    @Test
    void aFreshVerificationIsNotMarkedStale() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(
                new StrategyService.VerificationSummary("2026-09-14T09:00:00", 21, 10, -1.35, 1.883, "abc"));

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertFalse(text.contains("不能当作此刻的证据"), text);
    }

    // ==================== 2.5 每日简报：安静的日子也要说一句 ====================

    /**
     * 安静的日子**必须有**一条简报。
     *
     * <p>这条用例守的是可发现性：原来的纪律是"有事才说"，代价是
     * **"没消息"与"服务没在跑"在用户那边长得一模一样**。
     * 实测反馈就是那句"我不问 AI 都不知道有模拟盘这个功能"。
     */
    @Test
    void aQuietDayStillGetsABriefingThatSaysWhyNothingHappened() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));

        String text = service.briefingText(strategy(), account(), DAY);
        assertNotNull(text);

        assertTrue(text.contains("【模拟盘简报】"), text);
        // 原因用痕迹那句人话（同一套翻译，不另写一份）
        assertTrue(text.contains("规则条件不成立"), text);
        // 赚没赚：至少要有净值与相对本金的收益
        assertTrue(text.contains("净值 100900.00"), text);
        assertTrue(text.contains("+0.90%"), text);
        // 下次什么时候：把"它还在跑"这件事说成确定的时间点
        assertTrue(text.contains("下一次评估："), text);
        assertTrue(text.contains("按工作日近似"), "没有交易日历这件事必须写在明处：" + text);
    }

    /** 连痕迹都没有的日子**不发**简报：那是结算没走到留痕那一步，说"今天很平静"是假话。 */
    @Test
    void aBriefingIsNotSentWhenTheSettlementLeftNoTrace() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of());

        assertNull(service.briefingText(strategy(), account(), DAY));
    }

    /** agent 模式的简报要写明"只在日线结算决策"，否则用户会以为盘中还会自己动。 */
    @Test
    void aCommitteeModeBriefingExplainsThatItOnlyDecidesAtSettlement() {
        Strategy agent = Strategy.builder()
                .id(10L).name("委员会策略").symbol("600519")
                .decisionMode(ExecutionContract.DECISION_MODE_AGENT)
                .user(User.builder().id(1L).username("alice").email("a@b.c").password("x").build())
                .build();
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));

        String text = service.briefingText(agent, account(), DAY);
        assertNotNull(text);

        assertTrue(text.contains("盘中由盯盘 agent 事件驱动执行"), text);
    }

    // ==================== 3. 空转期：沉默本身也是信息 ====================

    @Test
    void theWeeklyReportStatesTheMinimumSampleGateInsteadOfCryingWolf() {
        // 连续 3 天没动 ≠ "异常沉默"。不到门槛就说"还不能判断"，不制造"它坏了"的暗示
        when(traceService.listRecent(eq(10L), any(Integer.class))).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);

        String text = service.idleWeeklyText(strategy(), account(), DAY);
        assertNotNull(text);

        assertTrue(text.contains("空转期"), text);
        assertTrue(text.contains(String.valueOf(PaperReviewReportService.IDLE_DAYS_THRESHOLD)),
                text);
    }

    @Test
    void theWeeklyReportIsSkippedWhenTheWeekAlreadyHadANoteworthyDay() {
        PaperTradeTrace blocking = trace(ExecutionContract.DECISION_SKIP,
                ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT, null);
        blocking.setTradeDate(DAY.minusDays(2));
        when(traceService.listRecent(eq(10L), any(Integer.class))).thenReturn(List.of(blocking));

        assertNull(service.idleWeeklyText(strategy(), account(), DAY));
    }

    @Test
    void aReportFailureNeverEscapes() {
        // 报告是增强：结算与痕迹都已落库，生成失败只是页面上少一段文本，绝不能抛出去
        when(traceService.listForDay(10L, DAY)).thenThrow(new RuntimeException("db down"));

        assertNull(service.dailyReportText(strategy(), account(), DAY));
    }

    // ==================== 4. 机会成本：空仓也要被记账 ====================

    private static PaperEquitySnapshot snapshot(String date, String equity, String close, double shares) {
        return PaperEquitySnapshot.builder()
                .tradeDate(LocalDate.parse(date))
                .equity(new java.math.BigDecimal(equity))
                .cash(new java.math.BigDecimal("0.00"))
                .shares(java.math.BigDecimal.valueOf(shares))
                .closePrice(new java.math.BigDecimal(close))
                .build();
    }

    @Test
    void theReportPricesTheCostOfDoingNothing() {
        /**
         * 这是整份报告里最该看的一行：账户一直空仓（净值不动），标的涨了 10%。
         * 账面上"什么都没发生"，实际上相对基准亏了 10% —— 把那 10% 写出来，
         * "不动"才第一次有了代价（而不是一个看起来中性的 0）。
         */
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        // 最近没有"值得说"的动作（否则空转期摘要会主动跳过自己）
        when(traceService.listRecent(eq(10L), any(Integer.class))).thenReturn(List.of(
                trace(ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);
        when(equityRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of(
                snapshot("2026-09-01", "100000.00", "10.0000", 0),
                snapshot("2026-09-02", "100000.00", "10.5000", 0),
                snapshot("2026-09-03", "100000.00", "11.0000", 0)));

        String text = service.idleWeeklyText(strategy(), account(), DAY);
        assertNotNull(text);

        assertTrue(text.contains("净值曲线（自 2026-09-01 起"), text);
        assertTrue(text.contains("空仓占 100.0%"), text);
        assertTrue(text.contains("同期买入持有 +10.00%"), text);
        assertTrue(text.contains("超额 -10.00%"), text);
        assertTrue(text.contains("代价"), "负超额必须写明这是「不动」的代价：" + text);
    }

    @Test
    void theReportSaysThereIsNoCurveYetInsteadOfInventingOne() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);
        when(equityRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of());

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertTrue(text.contains("还没有快照"), text);
    }

    @Test
    void aShortCurveIsMarkedAsTooFewSamples() {
        when(traceService.listForDay(10L, DAY)).thenReturn(List.of(
                trace(ExecutionContract.DECISION_BUY, null, null)));
        when(strategyService.latestVerification(1L, 10L)).thenReturn(null);
        when(equityRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(List.of(
                snapshot("2026-09-01", "100000.00", "10.0000", 0),
                snapshot("2026-09-02", "100000.00", "10.0000", 0)));

        String text = service.dailyReportText(strategy(), account(), DAY);

        assertTrue(text.contains("样本还很少"), text);
    }
}
