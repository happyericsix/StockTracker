package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.StrategyHealthEvent;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MemoryFactRepository;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyHealthEventRepository;
import com.happyericsix.stocktracker.service.StrategyService.VerificationSummary;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 策略哨兵的**口径**用例：什么时候说话、什么时候闭嘴。
 * 防骚扰（冷却）和宁缺毋滥（缺基准不判）是这个功能最容易坏掉的两条纪律。
 */
@ExtendWith(MockitoExtension.class)
class StrategyHealthWatchServiceTest {

    @Mock private StrategyHealthEventRepository healthEventRepository;
    @Mock private PaperTradeRepository paperTradeRepository;
    @Mock private PaperEquitySnapshotRepository equitySnapshotRepository;
    @Mock private MemoryFactRepository memoryFactRepository;
    @Mock private NewsEventRepository newsEventRepository;
    @Mock private MessageService messageService;
    @Mock private StrategyService strategyService;

    private StrategyHealthWatchService watch;
    private Strategy strategy;
    private PaperAccount account;
    private final LocalDate today = LocalDate.of(2026, 10, 8);

    @BeforeEach
    void setUp() {
        watch = new StrategyHealthWatchService(healthEventRepository, paperTradeRepository,
                equitySnapshotRepository, memoryFactRepository, newsEventRepository,
                strategyService);
        // @Value 在纯单测里不会注入，手动给默认值（与 application.properties 一致）
        ReflectionTestUtils.setField(watch, "idleDaysThreshold", 10);
        ReflectionTestUtils.setField(watch, "drawdownBreachMultiplier", 1.2);
        ReflectionTestUtils.setField(watch, "newsCredibilityMin", 70);
        ReflectionTestUtils.setField(watch, "cooldownIdleDays", 10);
        ReflectionTestUtils.setField(watch, "cooldownDrawdownDays", 5);
        ReflectionTestUtils.setField(watch, "cooldownVerificationDays", 7);
        ReflectionTestUtils.setField(watch, "cooldownNewsDays", 3);
        ReflectionTestUtils.setField(watch, "cooldownConcentrationDays", 14);

        User user = new User();
        ReflectionTestUtils.setField(user, "id", 29L);
        strategy = new Strategy();
        ReflectionTestUtils.setField(strategy, "id", 10L);
        ReflectionTestUtils.setField(strategy, "user", user);
        ReflectionTestUtils.setField(strategy, "symbol", "600519");
        ReflectionTestUtils.setField(strategy, "name", "茅台RSI超卖买入+止盈止损");

        account = new PaperAccount();
        ReflectionTestUtils.setField(account, "createdAt", LocalDateTime.of(2026, 9, 20, 16, 0));

        // 通用桩：默认不在冷却期（没有历史事件）
        lenient().when(healthEventRepository
                .findTop3ByStrategyIdAndEventTypeOrderByCreatedAtDesc(any(), anyString()))
                .thenReturn(List.of());
        lenient().when(messageService.saveReport(any(), anyString(), anyString(), anyString(), anyString()))
                .thenReturn(null);
        // 通用桩：没有成交、没有回测事实、没有资讯（各用例再覆写）
        lenient().when(paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(any()))
                .thenReturn(List.of());
        lenient().when(equitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(any()))
                .thenReturn(List.of());
        lenient().when(memoryFactRepository.findActiveByKey(any(), anyString(), anyString()))
                .thenReturn(List.of());
        lenient().when(newsEventRepository.findTimeline(any(), any(), any()))
                .thenReturn(List.of());
    }

    @Test
    void idleStreakFiresAfterThresholdFlatDays() {
        when(equitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L))
                .thenReturn(flatSnapshots(today, 12));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertTrue(fired.stream().anyMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_IDLE_STREAK)));
    }

    @Test
    void idleStreakSilentWhenHolding() {
        // 持仓中的日子不算空转：拿着不动是策略的正常状态
        List<PaperEquitySnapshot> snaps = flatSnapshots(today, 12);
        snaps.forEach(s -> ReflectionTestUtils.setField(s, "shares", new BigDecimal("100")));
        when(equitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L)).thenReturn(snaps);

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertTrue(fired.stream().noneMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_IDLE_STREAK)));
    }

    @Test
    void cooldownSuppressesSecondFire() {
        when(equitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(10L))
                .thenReturn(flatSnapshots(today, 12));
        // 3 天前刚触发过同类型事件：冷却 10 天内不再说话
        when(healthEventRepository.findTop3ByStrategyIdAndEventTypeOrderByCreatedAtDesc(
                eq(10L), eq(StrategyHealthEvent.TYPE_IDLE_STREAK)))
                .thenReturn(List.of(StrategyHealthEvent.builder()
                        .strategyId(10L).eventType(StrategyHealthEvent.TYPE_IDLE_STREAK)
                        .createdAt(LocalDateTime.now().minusDays(3)).build()));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertTrue(fired.isEmpty());
        verify(healthEventRepository, never()).save(any(StrategyHealthEvent.class));
    }

    @Test
    void drawdownBreachNeedsBacktestBaseline() {
        // 宁缺毋滥：没有回测最大回撤这个基准，实盘回撤再深也不判
        ReflectionTestUtils.setField(account, "highWatermark", new BigDecimal("200000"));
        ReflectionTestUtils.setField(account, "equity", new BigDecimal("100000")); // -50% 回撤

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertTrue(fired.stream().noneMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_DRAWDOWN_BREACH)));
    }

    // ==================== 满仓单票（concentration_full，2026-10-12 用户实例） ====================

    @Test
    void concentrationFullFiresWhenPositionDominatesEquity() {
        // 用户实例复刻：14600 股 × 6.88 = 100,448 市值、现金 667 —— 占比 99.3%。
        // 阈值 95% 就是为这种"整手规则法定零头"设的：卡 100% 会让满仓永远漏判
        ReflectionTestUtils.setField(account, "shares", new BigDecimal("14600"));
        ReflectionTestUtils.setField(account, "lastPrice", new BigDecimal("6.88"));
        ReflectionTestUtils.setField(account, "cash", new BigDecimal("667.49"));
        ReflectionTestUtils.setField(account, "equity", new BigDecimal("101115.49"));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertTrue(fired.stream().anyMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_CONCENTRATION_FULL)), "满仓必须被点名");
    }

    @Test
    void concentrationFullSilentBelowThresholdOrFlat() {
        // 半仓：不说话 —— 仓位配置是用户的自由，哨兵只观察极端集中
        ReflectionTestUtils.setField(account, "shares", new BigDecimal("7000"));
        ReflectionTestUtils.setField(account, "lastPrice", new BigDecimal("6.88"));
        ReflectionTestUtils.setField(account, "cash", new BigDecimal("51840"));
        ReflectionTestUtils.setField(account, "equity", new BigDecimal("100000"));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);
        assertTrue(fired.stream().noneMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_CONCENTRATION_FULL)));

        // 空仓：满仓不成立
        ReflectionTestUtils.setField(account, "shares", BigDecimal.ZERO);
        ReflectionTestUtils.setField(account, "equity", new BigDecimal("100000"));
        fired = watch.checkStrategy(strategy, account, today);
        assertTrue(fired.stream().noneMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_CONCENTRATION_FULL)));
    }

    @Test
    void drawdownBreachFiresBeyondBacktestLimit() {
        ReflectionTestUtils.setField(account, "highWatermark", new BigDecimal("200000"));
        ReflectionTestUtils.setField(account, "equity", new BigDecimal("180000")); // -10%
        com.happyericsix.stocktracker.entity.MemoryFact fact =
                new com.happyericsix.stocktracker.entity.MemoryFact();
        ReflectionTestUtils.setField(fact, "factValue", "-6.61");
        when(memoryFactRepository.findActiveByKey(29L,
                ObjectiveFactKeys.strategySubject(10L),
                ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT))
                .thenReturn(List.of(fact));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        // -10% > 6.61 × 1.2 = 7.93% → 应触发
        assertTrue(fired.stream().anyMatch(e -> e.getEventType()
                .equals(StrategyHealthEvent.TYPE_DRAWDOWN_BREACH)));
    }

    @Test
    void verificationFailedOnlyWithSampleAndNegativeExcess() {
        // 没有样本 = "还不能判断"，不是失败
        when(strategyService.latestVerification(29L, 10L)).thenReturn(
                new VerificationSummary("2026-10-04", 0, 0, null, null, "v1"));
        assertTrue(watch.checkStrategy(strategy, account, today).stream().noneMatch(
                e -> e.getEventType().equals(StrategyHealthEvent.TYPE_VERIFICATION_FAILED)));

        when(strategyService.latestVerification(29L, 10L)).thenReturn(
                new VerificationSummary("2026-10-04", 24, 6, -2.5, 0.4, "v1"));
        assertTrue(watch.checkStrategy(strategy, account, today).stream().anyMatch(
                e -> e.getEventType().equals(StrategyHealthEvent.TYPE_VERIFICATION_FAILED)));
    }

    @Test
    void newsOnlyCountsHighImpactCredibleDirectional() {
        NewsEvent ok = new NewsEvent();
        ReflectionTestUtils.setField(ok, "title", "重大利好公告");
        ReflectionTestUtils.setField(ok, "impactLevel", "high");
        ReflectionTestUtils.setField(ok, "direction", "利好");
        ReflectionTestUtils.setField(ok, "credibility", 85);
        ReflectionTestUtils.setField(ok, "plainSummary", "已解读");
        NewsEvent lowImpact = new NewsEvent();
        ReflectionTestUtils.setField(lowImpact, "title", "低影响");
        ReflectionTestUtils.setField(lowImpact, "impactLevel", "low");
        ReflectionTestUtils.setField(lowImpact, "direction", "利好");
        ReflectionTestUtils.setField(lowImpact, "credibility", 85);
        ReflectionTestUtils.setField(lowImpact, "plainSummary", "已解读");
        NewsEvent neutral = new NewsEvent();
        ReflectionTestUtils.setField(neutral, "title", "中性");
        ReflectionTestUtils.setField(neutral, "impactLevel", "high");
        ReflectionTestUtils.setField(neutral, "direction", "中性");
        ReflectionTestUtils.setField(neutral, "credibility", 85);
        ReflectionTestUtils.setField(neutral, "plainSummary", "已解读");
        when(newsEventRepository.findTimeline(any(), any(), any()))
                .thenReturn(List.of(ok, lowImpact, neutral));

        List<StrategyHealthEvent> fired = watch.checkStrategy(strategy, account, today);

        assertEquals(1, fired.stream().filter(
                e -> e.getEventType().equals(StrategyHealthEvent.TYPE_NEWS_MATERIAL)).count());
    }

    /** 生成 n 个空仓净值快照（最近 n 个交易日，隔一天一个的近似即可）。 */
    private List<PaperEquitySnapshot> flatSnapshots(LocalDate end, int n) {
        List<PaperEquitySnapshot> snaps = new java.util.ArrayList<>();
        for (int i = n; i >= 1; i--) {
            PaperEquitySnapshot s = new PaperEquitySnapshot();
            ReflectionTestUtils.setField(s, "tradeDate", end.minusDays(i));
            ReflectionTestUtils.setField(s, "shares", BigDecimal.ZERO);
            ReflectionTestUtils.setField(s, "equity", new BigDecimal("200000"));
            snaps.add(s);
        }
        return snaps;
    }
}
