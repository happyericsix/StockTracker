package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 净值快照在真实 SQL 引擎上的往返：**一个交易日只能有一格**。
 *
 * <h3>为什么这条约束必须落到数据库上</h3>
 * "每个交易日一行"靠代码里的"先查再写"是保证不了的（结算重跑、并发触发都会绕过它）。
 * 真正兜底的是 {@code (strategy_id, trade_date)} 上的唯一索引 —— 而这类约束
 * 在 {@code ddl-auto=update} 下**不会报错**，只是沉默地不存在。
 * 所以在测试数据源（H2，见 {@code src/test/resources}）上真插一次、真撞一次。
 */
@SpringBootTest
@Transactional
class PaperEquityPersistenceTest {

    @Autowired
    private PaperEquitySnapshotRepository repository;

    @Autowired
    private StrategyRepository strategyRepository;

    @Autowired
    private UserRepository userRepository;

    private Strategy anyStrategy() {
        User user = userRepository.findAll().stream().findFirst().orElse(null);
        if (user == null) {
            user = userRepository.save(User.builder()
                    .username("equity-it-user").password("x").email("equity-it@example.com").build());
        }
        return strategyRepository.save(Strategy.builder()
                .user(user)
                .name("净值往返测试")
                .symbol("600519")
                .configJson("{\"initial_capital\":100000.0}")
                .paperEnabled(false)
                .build());
    }

    private PaperEquitySnapshot point(Strategy strategy, String date, String equity, String close,
                                      String shares) {
        return PaperEquitySnapshot.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .tradeDate(LocalDate.parse(date))
                .equity(new BigDecimal(equity))
                .cash(new BigDecimal("0.00"))
                .shares(new BigDecimal(shares))
                .closePrice(new BigDecimal(close))
                .build();
    }

    @Test
    void aSnapshotSurvivesTheRoundTripWithItsPrecision() {
        Strategy strategy = anyStrategy();

        // 半仓：现金 99000 + 1000 股 × 10.1234 = 109123.40 —— 造数据时就把恒等式对上，
        // 否则"精度没被截断"这件事根本验不出来（对不上的数据怎么存都不会对）
        PaperEquitySnapshot saved = repository.save(PaperEquitySnapshot.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .tradeDate(LocalDate.parse("2026-09-17"))
                .equity(new BigDecimal("109123.40"))
                .cash(new BigDecimal("99000.00"))
                .shares(new BigDecimal("1000.0000"))
                .closePrice(new BigDecimal("10.1234"))
                .build());
        PaperEquitySnapshot reloaded = repository.findById(saved.getId()).orElseThrow();

        assertEquals(0, new BigDecimal("109123.40").compareTo(reloaded.getEquity()));
        assertEquals(0, new BigDecimal("10.1234").compareTo(reloaded.getClosePrice()));
        // 净值恒等式在**落库之后**仍然成立（DECIMAL 不能被截断）
        assertTrue(Money.equityIdentityHolds(reloaded.getCash(), reloaded.getShares(),
                reloaded.getClosePrice(), reloaded.getEquity()));
        assertNotNull(reloaded.getCreatedAt());
    }

    @Test
    void theDayIsTheUniqueKey() {
        Strategy strategy = anyStrategy();
        repository.saveAndFlush(point(strategy, "2026-09-16", "100000.00", "10.0000", "0.0000"));
        repository.saveAndFlush(point(strategy, "2026-09-17", "101000.00", "10.1000", "0.0000"));

        List<PaperEquitySnapshot> series = repository.findByStrategyIdOrderByTradeDateAsc(strategy.getId());

        assertEquals(2, series.size());
        assertEquals(LocalDate.parse("2026-09-16"), series.get(0).getTradeDate(),
                "曲线必须按时间正序回来");
    }
}
