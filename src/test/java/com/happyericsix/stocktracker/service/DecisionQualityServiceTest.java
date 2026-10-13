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
    private UserRepository userRepository;
    @Mock
    private StockDataGateway stockDataGateway;

    private DecisionQualityService service;

    @BeforeEach
    void setUp() {
        service = new DecisionQualityService(paperTradeRepository, paperTradeTraceRepository,
                userRepository, stockDataGateway);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(
                User.builder().id(1L).username("alice").build()));
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

    @Test
    @SuppressWarnings("unchecked")
    void buyBeforeRiseCountsAsWinAndSellBeforeFallToo() {
        // 09-01 收 100，随后 101、102、103、104、105（第 5 个交易日 = 105）
        historyFrom("2026-09-01", 100, 101, 102, 103, 104, 105);
        LocalDate d = LocalDate.of(2026, 9, 1);
        when(paperTradeRepository.findByUserIdAndTradeDateGreaterThanEqualOrderByTradeDateDescCreatedAtDesc(
                anyLongAny(), org.mockito.ArgumentMatchers.any(LocalDate.class)))
                .thenReturn(List.of(trade("BUY", 100, d), trade("SELL", 100, d)));

        Map<String, Object> out = service.quality("alice", 5, 60);

        List<Map<String, Object>> bySide = (List<Map<String, Object>>) out.get("bySide");
        // BUY：+5% 对齐收益（win）；SELL：前向 +5%，对齐 = -5%（loss——卖在涨前是错的）
        Map<String, Object> buy = bySide.stream().filter(m -> "buy".equals(m.get("key"))).findFirst().orElseThrow();
        Map<String, Object> sell = bySide.stream().filter(m -> "sell".equals(m.get("key"))).findFirst().orElseThrow();
        assertThat(((Number) buy.get("winRatePct")).doubleValue()).isEqualTo(100.0);
        assertThat(((Number) sell.get("winRatePct")).doubleValue()).isEqualTo(0.0);
        assertThat(((Number) sell.get("avgAlignedPct")).doubleValue()).isEqualTo(-5.0);
    }

    @Test
    @SuppressWarnings("unchecked")
    void tradesWithoutEnoughFutureBarsStayPending() {
        // 只有 3 根 bar：5 日窗口走不满 → pending，不许进胜率
        historyFrom("2026-09-01", 100, 101, 102);
        when(paperTradeRepository.findByUserIdAndTradeDateGreaterThanEqualOrderByTradeDateDescCreatedAtDesc(
                anyLongAny(), org.mockito.ArgumentMatchers.any(LocalDate.class)))
                .thenReturn(List.of(trade("BUY", 100, LocalDate.of(2026, 9, 1))));

        Map<String, Object> out = service.quality("alice", 5, 60);
        assertThat((List<?>) out.get("bySource")).isEmpty();
        List<Map<String, Object>> recent = (List<Map<String, Object>>) out.get("recent");
        assertThat(recent).hasSize(1);
        assertThat(recent.get(0).get("status")).isEqualTo("pending");
    }

    @Test
    @SuppressWarnings("unchecked")
    void statsCoverAllTradesWhileRecentListIsTrimmed() {
        historyFrom("2026-09-01", 100, 101, 102, 103, 104, 105);
        // 25 笔成交：如果统计被"明细只留 20"截断，胜率就会算错
        List<PaperTrade> many = new java.util.ArrayList<>();
        for (int i = 0; i < 25; i++) {
            many.add(trade("BUY", 100, LocalDate.of(2026, 9, 1)));
        }
        when(paperTradeRepository.findByUserIdAndTradeDateGreaterThanEqualOrderByTradeDateDescCreatedAtDesc(
                anyLongAny(), org.mockito.ArgumentMatchers.any(LocalDate.class)))
                .thenReturn(many);

        Map<String, Object> out = service.quality("alice", 5, 60);
        Map<String, Object> agent = ((List<Map<String, Object>>) out.get("bySource")).get(0);
        assertThat(((Number) agent.get("evaluated")).intValue()).isEqualTo(25);
        assertThat((List<?>) out.get("recent")).hasSize(20);
    }

    @Test
    void missingHistoryDegradesToEmpty() {
        when(stockDataGateway.getStockHistory(anyString(), anyString())).thenReturn(null);
        when(paperTradeRepository.findByUserIdAndTradeDateGreaterThanEqualOrderByTradeDateDescCreatedAtDesc(
                anyLongAny(), org.mockito.ArgumentMatchers.any(LocalDate.class)))
                .thenReturn(List.of(trade("BUY", 100, LocalDate.of(2026, 9, 1))));

        Map<String, Object> out = service.quality("alice", 5, 60);
        assertThat((List<?>) out.get("bySource")).isEmpty();
    }

    // ==================== 单策略经验包（P4 经验回流） ====================

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

    private static Long anyLongAny() {
        return org.mockito.ArgumentMatchers.anyLong();
    }
}
