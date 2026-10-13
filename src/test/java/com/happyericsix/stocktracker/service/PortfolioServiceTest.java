package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.mockito.junit.jupiter.MockitoSettings;
import org.mockito.quality.Strictness;
import org.springframework.test.util.ReflectionTestUtils;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 组合层的数学与熔断判定。
 *
 * 重点钉三件事：相关性算的是"日收益"而不是净值；熔断的基准是**最近收盘快照**
 * （不是历史最高点——那是最大回撤，不是单日回撤）；组合读挂了 fail-open。
 */
@ExtendWith(MockitoExtension.class)
@MockitoSettings(strictness = Strictness.LENIENT)
class PortfolioServiceTest {

    @Mock
    private StrategyRepository strategyRepository;
    @Mock
    private PaperAccountRepository paperAccountRepository;
    @Mock
    private PaperEquitySnapshotRepository paperEquitySnapshotRepository;

    private PortfolioService service;

    @BeforeEach
    void setUp() {
        service = new PortfolioService(strategyRepository, paperAccountRepository,
                paperEquitySnapshotRepository);
        ReflectionTestUtils.setField(service, "haltThresholdPct", 5.0);
    }

    private static Strategy strategy(long id, String name) {
        return Strategy.builder().id(id).name(name).symbol("600519").build();
    }

    private static PaperAccount account(double equity, double initial, String lastEval) {
        PaperAccount a = new PaperAccount();
        a.setEquity(BigDecimal.valueOf(equity));
        a.setInitialCapital(BigDecimal.valueOf(initial));
        a.setLastEvalAt(lastEval == null ? null : java.time.LocalDateTime.of(2026, 10, 7, 15, 0));
        return a;
    }

    private static PaperEquitySnapshot snap(LocalDate d, double equity) {
        PaperEquitySnapshot s = new PaperEquitySnapshot();
        s.setTradeDate(d);
        s.setEquity(BigDecimal.valueOf(equity));
        return s;
    }

    @Test
    void haltTriggersWhenEquityDropsFivePercentBelowLastClose() {
        Strategy s = strategy(1L, "A");
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L)).thenReturn(List.of(s));
        // 昨收 100 → 现价 94：回撤 -6% ≤ -5% → 熔断
        when(paperAccountRepository.findByStrategyId(1L))
                .thenReturn(Optional.of(account(94.0, 100.0, "today")));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(eq(1L), any()))
                .thenReturn(List.of(snap(LocalDate.of(2026, 10, 6), 100.0)));

        assertThat(service.haltsNewOpens(9L)).isTrue();
    }

    @Test
    void haltDoesNotTriggerInsideThreshold() {
        Strategy s = strategy(1L, "A");
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L)).thenReturn(List.of(s));
        when(paperAccountRepository.findByStrategyId(1L))
                .thenReturn(Optional.of(account(96.0, 100.0, "today")));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(eq(1L), any()))
                .thenReturn(List.of(snap(LocalDate.of(2026, 10, 6), 100.0)));

        assertThat(service.haltsNewOpens(9L)).isFalse();
    }

    @Test
    void haltFailsOpenWhenPortfolioReadBlowsUp() {
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L))
                .thenThrow(new RuntimeException("db down"));

        assertThat(service.haltsNewOpens(9L)).isFalse();
    }

    @Test
    @SuppressWarnings("unchecked")
    void correlationOfIdenticalSeriesIsOne() {
        Strategy a = strategy(1L, "A");
        Strategy b = strategy(2L, "B");
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L)).thenReturn(List.of(a, b));
        when(paperAccountRepository.findByStrategyId(1L))
                .thenReturn(Optional.of(account(100, 100, "today")));
        when(paperAccountRepository.findByStrategyId(2L))
                .thenReturn(Optional.of(account(100, 100, "today")));
        // 完全同涨同跌的七日序列 → 相关系数 ≈ 1（伪分散的教科书样本）
        double[] equity = {100, 101, 100.5, 102, 101.5, 103, 102.5};
        List<PaperEquitySnapshot> series = new java.util.ArrayList<>();
        LocalDate d = LocalDate.of(2026, 9, 25);
        for (double v : equity) {
            series.add(snap(d, v));
            d = d.plusDays(1);
        }
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(series);
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(2L)).thenReturn(series);
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(any(), any()))
                .thenReturn(List.of(snap(LocalDate.of(2026, 10, 6), 100)));

        Map<String, Object> out = service.portfolio(9L);
        List<Map<String, Object>> corr = (List<Map<String, Object>>) out.get("correlations");
        assertThat(corr).hasSize(1);
        assertThat(((Number) corr.get(0).get("rho")).doubleValue()).isCloseTo(1.0, org.assertj.core.data.Offset.offset(0.01));
    }

    @Test
    @SuppressWarnings("unchecked")
    void tooFewPointsYieldsNoCorrelation() {
        Strategy a = strategy(1L, "A");
        Strategy b = strategy(2L, "B");
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L)).thenReturn(List.of(a, b));
        when(paperAccountRepository.findByStrategyId(1L))
                .thenReturn(Optional.of(account(100, 100, "today")));
        when(paperAccountRepository.findByStrategyId(2L))
                .thenReturn(Optional.of(account(100, 100, "today")));
        List<PaperEquitySnapshot> three = List.of(
                snap(LocalDate.of(2026, 9, 25), 100),
                snap(LocalDate.of(2026, 9, 28), 101),
                snap(LocalDate.of(2026, 9, 29), 100.5));
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(1L)).thenReturn(three);
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(2L)).thenReturn(three);
        when(paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateDesc(any(), any()))
                .thenReturn(List.of(snap(LocalDate.of(2026, 10, 6), 100)));

        Map<String, Object> out = service.portfolio(9L);
        assertThat((List<?>) out.get("correlations")).isEmpty();
    }

    @Test
    @SuppressWarnings("unchecked")
    void unevaluatedAccountsStayOutOfThePortfolio() {
        Strategy fresh = strategy(1L, "刚开没评估");
        when(strategyRepository.findByUserIdOrderByUpdatedAtDesc(9L)).thenReturn(List.of(fresh));
        when(paperAccountRepository.findByStrategyId(1L))
                .thenReturn(Optional.of(account(100, 100, null))); // lastEvalAt = null

        Map<String, Object> out = service.portfolio(9L);
        assertThat(((Number) out.get("activeStrategies")).intValue()).isZero();
        assertThat(out.get("totalEquity")).isNull();
    }
}
