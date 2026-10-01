package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.dto.TradeJournalResponse;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Pageable;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 决策日志的**口径**用例（口径是这个功能最难的部分，不是查询）：
 * 动作优先于 skip、次日方向对答案、最近的决策 pending、行情失败 no_price
 * 而不是 500、算不进分母的都不算。
 */
@ExtendWith(MockitoExtension.class)
class TradeJournalServiceTest {

    @Mock
    private PaperTradeTraceRepository traceRepository;
    @Mock
    private StrategyRepository strategyRepository;
    @Mock
    private UserRepository userRepository;
    @Mock
    private StockDataGateway stockDataGateway;
    @InjectMocks
    private TradeJournalService service;

    private final User owner = User.builder().id(7L).username("alice")
            .password("x").email("a@b.c").build();

    private Strategy strategy() {
        return Strategy.builder().id(42L).name("MA cross").symbol("600519")
                .configJson("{}").user(owner).build();
    }

    private PaperTradeTrace trace(String decision, LocalDate date, String skipReason) {
        return PaperTradeTrace.builder()
                .id((long) date.toEpochDay()).user(owner).strategy(strategy())
                .dedupeKey("k-" + date + "-" + decision)
                .settlementKind("daily").trigger("cron").tradeDate(date)
                .symbol("600519").decision(decision).skipReason(skipReason)
                .signal(decision).build();
    }

    private StockHistoryResponse history(Map<String, String> closeByDate) {
        Map<String, StockHistoryResponse.DailyPrice> series = new java.util.LinkedHashMap<>();
        closeByDate.forEach((date, close) -> series.put(date,
                new StockHistoryResponse.DailyPrice(close, close, close, close, "1000")));
        return new StockHistoryResponse(null, series);
    }

    private void stubOwnership() {
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 7L))
                .thenReturn(Optional.of(strategy()));
    }

    @Test
    void rejectsOtherUsersStrategy() {
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(42L, 7L)).thenReturn(Optional.empty());

        assertThrows(IllegalArgumentException.class,
                () -> service.journal("alice", 42L, 90));
    }

    @Test
    void buyJudgedByNextDayDirection() {
        stubOwnership();
        // 09-17 买入，09-18 收盘上涨 → hit；09-18 卖出，09-19 下跌 → hit
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(
                        trace("buy", LocalDate.of(2026, 9, 17), null),
                        trace("sell", LocalDate.of(2026, 9, 18), null)));
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(history(Map.of(
                "2026-09-17", "100.00",
                "2026-09-18", "103.00",
                "2026-09-19", "101.00")));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(2, journal.evaluated());
        assertEquals(2, journal.hits());
        assertEquals(0, journal.misses());
        assertEquals("100.0", journal.hitRate().toString());
        TradeJournalResponse.Entry buy = journal.entries().stream()
                .filter(e -> e.tradeDate().equals(LocalDate.of(2026, 9, 17))).findFirst().orElseThrow();
        assertEquals(3.00, buy.nextChangePct().doubleValue(), 0.0001);
    }

    @Test
    void wrongDirectionCountsAsMissNotSilence() {
        stubOwnership();
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(trace("buy", LocalDate.of(2026, 9, 17), null)));
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(history(Map.of(
                "2026-09-17", "100.00", "2026-09-18", "98.00")));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(1, journal.misses());
        assertEquals(0, journal.hits());
        assertEquals("0.0", journal.hitRate().toString());
    }

    @Test
    void mostRecentDecisionIsPendingNotMiss() {
        // 最后一根 K 线当天做的决策：还没有"后来"，必须是 pending
        stubOwnership();
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(trace("buy", LocalDate.of(2026, 9, 18), null)));
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(history(Map.of(
                "2026-09-17", "100.00", "2026-09-18", "101.00")));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(1, journal.pending());
        assertEquals(0, journal.evaluated());
        assertNull(journal.hitRate(), "没有可判定的样本时不显示胜率，比编一个 0% 诚实");
    }

    @Test
    void actionBeatsSkipOnTheSameDay() {
        // 盘中成交 + 日频 skip(already_traded_today) 同日并存：按成交那条对答案
        stubOwnership();
        PaperTradeTrace daily = trace("skip", LocalDate.of(2026, 9, 17), "already_traded_today");
        daily.setSettlementKind("daily");
        PaperTradeTrace intraday = trace("buy", LocalDate.of(2026, 9, 17), null);
        intraday.setSettlementKind("realtime");
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(daily, intraday));
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(history(Map.of(
                "2026-09-17", "100.00", "2026-09-18", "102.00")));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(1, journal.entries().size());
        assertEquals("buy", journal.entries().get(0).decision());
        assertEquals(1, journal.hits());
        assertEquals(0, journal.skips(), "同日的 skip 只是陪衬，不单独成行");
    }

    @Test
    void priceSourceOutageYieldsNoPriceInsteadOf500() {
        stubOwnership();
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(trace("buy", LocalDate.of(2026, 9, 17), null)));
        when(stockDataGateway.getStockHistory("600519", "daily"))
                .thenThrow(new RuntimeException("python down"));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(TradeJournalResponse.VERDICT_NO_PRICE, journal.entries().get(0).verdict());
        assertEquals(0, journal.evaluated(), "行情挂了不算 miss：那是系统性风险，不是模型失准");
        assertNull(journal.hitRate());
    }

    @Test
    void skipsAreShownButOutsideTheDenominator() {
        stubOwnership();
        when(traceRepository.findByStrategyIdOrderByCreatedAtDesc(eq(42L), any(Pageable.class)))
                .thenReturn(List.of(
                        trace("skip", LocalDate.of(2026, 9, 16), "no_signal"),
                        trace("buy", LocalDate.of(2026, 9, 17), null)));
        when(stockDataGateway.getStockHistory("600519", "daily")).thenReturn(history(Map.of(
                "2026-09-16", "99.00", "2026-09-17", "100.00", "2026-09-18", "101.00")));

        TradeJournalResponse journal = service.journal("alice", 42L, 90);

        assertEquals(1, journal.skips());
        assertEquals(1, journal.evaluated());
        assertEquals(2, journal.entries().size());
        assertEquals("no_signal", journal.entries().stream()
                .filter(e -> "skip".equals(e.decision())).findFirst().orElseThrow().skipReason());
    }
}
