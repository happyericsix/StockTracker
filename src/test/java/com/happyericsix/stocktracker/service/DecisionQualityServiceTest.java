package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.when;

/**
 * 决策质量对账的数学。这页是产品的"良心面板"——数字错了比没有这页更糟。
 *
 * 钉住四件事：买卖的方向对齐（卖在跌前是 win）；没走满窗口的成交必须 pending
 * 而不是被算进胜率；统计算全量、明细只裁剪；数据缺失时安全退化为空。
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class DecisionQualityServiceTest {

    @Mock
    private PaperTradeRepository paperTradeRepository;
    @Mock
    private PaperTradeTraceRepository paperTradeTraceRepository;
    @Mock
    private StockDataGateway stockDataGateway;

    private DecisionQualityService service;

    @BeforeEach
    void setUp() {
        // 2026-10-14：quality()（智囊 vs 规则对比）已删，构造器少了 UserRepository；
        // 剩下的两个方法（经验包/教训退役）不依赖用户名解析
        service = new DecisionQualityService(paperTradeRepository, paperTradeTraceRepository,
                stockDataGateway);
    }

    /** 造一段日线：trade 日收盘 100，之后每天 +1。 */
    private void historyFrom(String startDate, double... closes) {
        Map<String, StockHistoryResponse.DailyPrice> series = new LinkedHashMap<>();
        LocalDate d = LocalDate.parse(startDate);
        for (double c : closes) {
            series.put(d.toString(), new StockHistoryResponse.DailyPrice(
                    "1", "1", "1", String.valueOf(c), "1000"));
            d = d.plusDays(1);
        }
        when(stockDataGateway.getStockHistory(anyString(), anyString()))
                .thenReturn(new StockHistoryResponse(
                        new StockHistoryResponse.MetaData("600519"), series));
    }

    private static PaperTrade trade(String side, double price, LocalDate date) {
        return PaperTrade.builder()
                .side(side).price(BigDecimal.valueOf(price)).tradeDate(date)
                .symbol("600519")
                .strategy(Strategy.builder().id(10L).name("测试").user(
                        User.builder().id(1L).username("alice").build()).build())
                .build();
    }

    // ==================== 单策略经验包（P4 经验回流） ====================
    // （原「智囊 vs 规则」quality() 的 4 个用例已随方法一起删除：按决策源分组对比
    //  的前提不成立——规则出信号、委员会划边界、边界内执行，是同一流水线的层。）

    @Test
    @SuppressWarnings("unchecked")
    void strategyExperienceReportsWinRateAndPerTradeVerdicts() {
        // 与 quality 同一套数学：买在涨前 win、卖在涨前 loss；经验包必须口径一致，
        // 否则委员会看到的"自己的病历"和对账页对不上，可信度归零
        historyFrom("2026-09-01", 100, 101, 102, 103, 104, 105);
        Strategy strategy = Strategy.builder().id(10L).name("测试").symbol("600519")
                .user(User.builder().id(1L).username("alice").build()).build();
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(10L))
                .thenReturn(List.of(
                        trade("SELL", 100, LocalDate.of(2026, 9, 1)),
                        trade("BUY", 100, LocalDate.of(2026, 9, 1))));

        Map<String, Object> out = service.strategyExperience(strategy, 6);

        assertThat(out).isNotNull();
        assertThat(((Number) out.get("evaluatedTrades")).intValue()).isEqualTo(2);
        assertThat(((Number) out.get("winRatePct")).doubleValue()).isEqualTo(50.0);
        assertThat(((Number) out.get("avgAlignedPct")).doubleValue()).isEqualTo(0.0);
        List<Map<String, Object>> recent = (List<Map<String, Object>>) out.get("recent");
        assertThat(recent).hasSize(2);
        assertThat(recent.stream().filter(r -> Boolean.TRUE.equals(r.get("win")))).hasSize(1);
        // 开场白钉住：这是"自己的成绩单"，且明确不是预测
        assertThat(String.valueOf(out.get("note")))
                .contains("自己过去的成交成绩单").contains("不是对未来走势的判断");
    }

    @Test
    void strategyExperienceWithoutEvaluatedTradesGivesMinimalPendingPacket() {
        // 只有 3 根 bar：都还没走满 5 日 → 不许算胜率，但要告诉委员会"交易过、还没法对账"
        historyFrom("2026-09-01", 100, 101, 102);
        Strategy strategy = Strategy.builder().id(10L).name("测试").symbol("600519")
                .user(User.builder().id(1L).username("alice").build()).build();
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(10L))
                .thenReturn(List.of(trade("BUY", 100, LocalDate.of(2026, 9, 1))));

        Map<String, Object> out = service.strategyExperience(strategy, 6);

        assertThat(out).isNotNull();
        assertThat(out).doesNotContainKey("winRatePct");
        assertThat(((Number) out.get("pendingTrades")).intValue()).isEqualTo(1);
    }

    @Test
    void strategyExperienceIsNullWhenTheStrategyNeverTraded() {
        // 没交易过的策略不带经验字段 —— 新策略照旧从零读盘（fail-open）
        Strategy strategy = Strategy.builder().id(11L).name("新策略").symbol("600519")
                .user(User.builder().id(1L).username("alice").build()).build();
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(11L))
                .thenReturn(List.of());

        assertThat(service.strategyExperience(strategy, 6)).isNull();
    }

    // ==================== 教训退出机制：agentOutcomeSince ====================

    /** 造一笔指定策略/决策源的成交（traceId 非空时以痕迹的 decision_mode 为准）。 */
    private static PaperTrade tradeOf(Strategy strategy, String side, double price,
                                      LocalDate date, Long traceId) {
        return PaperTrade.builder()
                .side(side).price(BigDecimal.valueOf(price)).tradeDate(date)
                .symbol("600519").strategy(strategy).traceId(traceId)
                .build();
    }

    @Test
    void agentOutcomeSinceOnlyCountsAgentTradesBornAfterTheLesson() {
        // 教训退出机制的口径：只看教训出生之后的 agent 成交，规则成交不替教训背锅/背书。
        // 上升序列：买入赢、卖出输。
        double[] closes = new double[30];
        for (int i = 0; i < closes.length; i++) {
            closes[i] = 100 + i;
        }
        historyFrom("2026-01-01", closes);

        Strategy agentStrategy = Strategy.builder().id(10L).name("agent策略").symbol("600519")
                .decisionMode("agent")
                .user(User.builder().id(1L).username("alice").build()).build();

        PaperTrade beforeSince = tradeOf(agentStrategy, "buy", 100, LocalDate.of(2026, 1, 3), null);
        PaperTrade win = tradeOf(agentStrategy, "buy", 101, LocalDate.of(2026, 1, 5), null);
        PaperTrade loss = tradeOf(agentStrategy, "sell", 102, LocalDate.of(2026, 1, 6), null);
        // 痕迹判为 rule 的成交：教训只进 agent 路径，这单不该进统计
        PaperTrade ruleMode = tradeOf(agentStrategy, "buy", 103, LocalDate.of(2026, 1, 7), 55L);
        when(paperTradeTraceRepository.findById(55L)).thenReturn(Optional.of(
                PaperTradeTrace.builder().decisionMode("rule").build()));
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(10L))
                .thenReturn(List.of(ruleMode, loss, win, beforeSince));

        Map<String, Object> out = service.agentOutcomeSince(agentStrategy, LocalDate.of(2026, 1, 5));

        assertThat(((Number) out.get("evaluated")).intValue()).isEqualTo(2);
        assertThat(((Number) out.get("wins")).intValue()).isEqualTo(1);
        assertThat(((Number) out.get("winRatePct")).doubleValue()).isEqualTo(50.0);
    }

    @Test
    void agentOutcomeSinceWithNoEvaluableSamplesReportsZeroNotGarbage() {
        // 没有可判读样本（全 pending / 全被过滤）：evaluated=0 + winRatePct=null，
        // 调用方按"证据不足，不退役"处理 —— 绝不在空样本上给一个看起来像 0% 的胜率。
        historyFrom("2026-09-01", 100, 101, 102);
        Strategy strategy = Strategy.builder().id(12L).name("agent策略").symbol("600519")
                .decisionMode("agent")
                .user(User.builder().id(1L).username("alice").build()).build();
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(12L))
                .thenReturn(List.of(
                        tradeOf(strategy, "buy", 100, LocalDate.of(2026, 9, 1), null)));

        Map<String, Object> out = service.agentOutcomeSince(strategy, LocalDate.of(2026, 9, 1));

        assertThat(((Number) out.get("evaluated")).intValue()).isEqualTo(0);
        assertThat(out.get("winRatePct")).isNull();
    }

    // ==================== 往返路径口径：MAE/MFE（roundTripOf）====================

    /** 造一笔带 FIFO 批次明细的卖出。 */
    private static PaperTrade sellWithLots(String matchedLots, double exitPrice,
                                           LocalDate exitDate) {
        return PaperTrade.builder()
                .side("sell").price(BigDecimal.valueOf(exitPrice)).tradeDate(exitDate)
                .shares(BigDecimal.valueOf(200)).symbol("600519")
                .matchedLots(matchedLots)
                .strategy(Strategy.builder().id(10L).name("测试").symbol("600519")
                        .user(User.builder().id(1L).username("alice").build()).build())
                .build();
    }

    @Test
    void roundTripOfComputesPathAgainstFifoCostWithoutLookingAhead() {
        // 窗口 09-01..09-05（入场 10.0）：最高 11.0 → MFE +10%；最低 9.6 → MAE −4%。
        // 卖出日之后的 bar 故意放一个 20.0 的极端值 —— 未来函数在这里现形（MFE 不许变 100%）。
        historyFrom("2026-09-01", 10.0, 10.5, 11.0, 9.6, 10.2, 20.0, 20.0, 20.0);
        PaperTrade sell = sellWithLots(
                "[{\"lotId\":1,\"openedDate\":\"2026-09-01\",\"cost\":\"10.0\",\"shares\":\"200\"}]",
                10.4, LocalDate.of(2026, 9, 5));

        Map<String, Object> out = service.roundTripOf(sell);

        assertThat(out).isNotNull();
        assertThat(((Number) out.get("entryPrice")).doubleValue()).isEqualTo(10.0);
        assertThat(((Number) out.get("exitPrice")).doubleValue()).isEqualTo(10.4);
        assertThat(((Number) out.get("roundTripPct")).doubleValue()).isEqualTo(4.0);
        assertThat(((Number) out.get("heldBars")).intValue()).isEqualTo(5);
        assertThat(((Number) out.get("mfePct")).doubleValue()).isEqualTo(10.0);
        assertThat(((Number) out.get("maePct")).doubleValue()).isEqualTo(-4.0);
        // MFE +10% 但落袋只有 4%（不足一半）→ exitEarly；MAE −4% 越过 −3% 线 → deepAdverse
        assertThat(Boolean.TRUE.equals(out.get("exitEarly"))).isTrue();
        assertThat(Boolean.TRUE.equals(out.get("deepAdverse"))).isTrue();
    }

    @Test
    void roundTripOfLegacySellDerivesEntryFromRealizedPnlButNeverFakesPath() {
        // matchedLots 上线前的遗留卖出：能由毛口径恒等式反推入场价 → 有往返收益；
        // 但开仓日不知道 → 不许拿"当日一个点"冒充路径（maePct/mfePct 必须缺席）。
        historyFrom("2026-09-01", 100, 101, 102, 103, 104, 105);
        PaperTrade sell = PaperTrade.builder()
                .side("sell").price(BigDecimal.valueOf(11.0))
                .shares(BigDecimal.valueOf(100))
                .tradeDate(LocalDate.of(2026, 9, 1)).symbol("600519")
                .realizedPnl(BigDecimal.valueOf(100)) // (11.0 − 10.0) × 100
                .strategy(Strategy.builder().id(10L).name("测试")
                        .user(User.builder().id(1L).username("alice").build()).build())
                .build();

        Map<String, Object> out = service.roundTripOf(sell);

        assertThat(out).isNotNull();
        assertThat(((Number) out.get("entryPrice")).doubleValue()).isEqualTo(10.0);
        assertThat(((Number) out.get("roundTripPct")).doubleValue()).isEqualTo(10.0);
        assertThat(out).doesNotContainKey("maePct");
        assertThat(out).doesNotContainKey("mfePct");
        assertThat(out).doesNotContainKey("exitEarly");
    }

    @Test
    @SuppressWarnings("unchecked")
    void strategyExperienceCarriesRoundTripAggregateAndSellRowPath() {
        // 经验包是路径口径的主出口：聚合块 + 明细行都要带上，且 note 必须解释字段
        // ——LLM 读不到口径说明就会自由发挥，数字反而变成噪声。
        // 卖出在 09-05：之后至少还要 5 根 bar 才能走满 5 日对账窗口进 recent（09-10 收盘在列）
        historyFrom("2026-09-01", 10.0, 10.5, 11.0, 9.6, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8);
        Strategy strategy = Strategy.builder().id(10L).name("测试").symbol("600519")
                .user(User.builder().id(1L).username("alice").build()).build();
        when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(10L))
                .thenReturn(List.of(
                        sellWithLots(
                                "[{\"lotId\":1,\"openedDate\":\"2026-09-01\",\"cost\":\"10.0\",\"shares\":\"200\"}]",
                                10.2, LocalDate.of(2026, 9, 5)),
                        trade("BUY", 10.0, LocalDate.of(2026, 9, 1))));

        Map<String, Object> out = service.strategyExperience(strategy, 6);

        Map<String, Object> roundTrips = (Map<String, Object>) out.get("roundTrips");
        assertThat(roundTrips).isNotNull();
        assertThat(((Number) roundTrips.get("count")).intValue()).isEqualTo(1);
        assertThat(((Number) roundTrips.get("avgMfePct")).doubleValue()).isEqualTo(10.0);
        assertThat(((Number) roundTrips.get("avgMaePct")).doubleValue()).isEqualTo(-4.0);
        assertThat(((Number) roundTrips.get("exitEarlyCount")).intValue()).isEqualTo(1);
        List<Map<String, Object>> recent = (List<Map<String, Object>>) out.get("recent");
        Map<String, Object> sellRow = recent.stream()
                .filter(r -> "sell".equals(r.get("side"))).findFirst().orElseThrow();
        assertThat(((Number) sellRow.get("mfePct")).doubleValue()).isEqualTo(10.0);
        Map<String, Object> buyRow = recent.stream()
                .filter(r -> "buy".equals(r.get("side"))).findFirst().orElseThrow();
        assertThat(buyRow).doesNotContainKey("mfePct");
        assertThat(String.valueOf(out.get("note"))).contains("maePct").contains("只用持仓窗口内的");
    }

    private static Long anyLongAny() {
        return org.mockito.ArgumentMatchers.anyLong();
    }
}
