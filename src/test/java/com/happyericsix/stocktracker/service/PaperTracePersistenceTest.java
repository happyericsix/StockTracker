package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 痕迹**真的落到了数据库里**吗？（不是"实体看起来对"，而是往返一趟之后还对不对）
 *
 * <h3>为什么必须有这一层</h3>
 * 单测用的是 Mockito 替身，它不会告诉你：{@code DECIMAL} 列会不会被截断、
 * {@code LONGTEXT} 里的中文快照会不会变问号、190 字符的唯一键建不建得起来、
 * 唯一约束冲突时到底是抛异常还是静默插入第二行。
 * 这些恰恰是痕迹这种东西最容易悄悄坏掉的地方 —— 而它坏掉时看起来一切正常。
 *
 * <p><b>跑在哪个库上</b>：测试数据源是 H2 内存库（{@code src/test/resources/application.properties}），
 * 生产是 MySQL。这里证明的是 JPA 映射与约束语义；MySQL 侧的建表由启动时的
 * {@code ddl-auto=update} 负责。两层各自负责自己那一部分，不要互相冒充。
 *
 * <p>用 {@code @Transactional} 包住：写完回滚，不往库里留测试数据。
 */
@SpringBootTest
@Transactional
class PaperTracePersistenceTest {

    @Autowired
    private PaperTraceService paperTraceService;

    @Autowired
    private PaperTradeTraceRepository traceRepository;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private StrategyRepository strategyRepository;

    private Strategy anyStrategy() {
        User user = userRepository.findAll().stream().findFirst().orElse(null);
        if (user == null) {
            user = userRepository.save(User.builder()
                    .username("trace-it-user").password("x").email("trace-it@example.com").build());
        }
        return strategyRepository.save(Strategy.builder()
                .user(user)
                .name("痕迹往返测试")
                .symbol("600519")
                .configJson("{\"initial_capital\":100000.0}")
                .paperEnabled(false)
                .build());
    }

    private static PaperTradeTrace traceFor(Strategy strategy, String decision, String skipReason,
                                            String barTime) {
        return PaperTradeTrace.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .settlementKind(ExecutionContract.SETTLEMENT_REALTIME)
                .trigger(ExecutionContract.TRIGGER_EVENT)
                .tradeDate(LocalDate.of(2026, 9, 17))
                .barTime(barTime)
                .symbol(strategy.getSymbol())
                .decision(decision)
                .skipReason(skipReason)
                .fillBasis(ExecutionContract.FILL_REALTIME_LAST)
                .barClose(new BigDecimal("1277.9600"))
                .cashBefore(new BigDecimal("100000.00"))
                .sharesBefore(new BigDecimal("0.0000"))
                .equityBefore(new BigDecimal("100000.00"))
                .cashAfter(new BigDecimal("100000.00"))
                .sharesAfter(new BigDecimal("0.0000"))
                .equityAfter(new BigDecimal("100000.00"))
                .engineVersion("2a9dce9df73b")
                .adjustMode(ExecutionContract.ADJUST_QFQ)
                .moneyPolicyVersion(Money.POLICY_VERSION)
                // 中文 + 长文本：LONGTEXT 与字符集最容易在这里出问题
                .snapshotJson("{\"schema_version\":1,\"bar\":{\"date\":\"2026-09-17\","
                        + "\"close\":1277.96},\"indicators\":{\"ma_60\":1277.2188},"
                        + "\"extra\":{\"matched_detail\":[{\"type\":\"price_cross_ma\",\"hit\":true}]}}")
                .snapshotSchemaVersion(1)
                .build();
    }

    @Test
    void aTraceSurvivesTheRoundTrip() {
        Strategy strategy = anyStrategy();

        PaperTradeTrace saved = paperTraceService.record(
                traceFor(strategy, ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED,
                        "2026-09-17 10:00:00"));

        assertNotNull(saved);
        assertNotNull(saved.getId());
        PaperTradeTrace reloaded = traceRepository.findById(saved.getId()).orElseThrow();

        assertEquals(ExecutionContract.SKIP_T1_BLOCKED, reloaded.getSkipReason());
        assertEquals(0, new BigDecimal("1277.9600").compareTo(reloaded.getBarClose()),
                "DECIMAL 不能被截断 —— 痕迹是钱的证据");
        assertTrue(reloaded.getSnapshotJson().contains("ma_60"), reloaded.getSnapshotJson());
        assertTrue(reloaded.getTraceHash() != null && reloaded.getTraceHash().length() == 64,
                "防篡改戳必须随行落库");
        assertEquals(1, reloaded.getRepeatCount());
        assertNotNull(reloaded.getCreatedAt());
    }

    @Test
    void aRerunUpdatesTheRowInsteadOfHittingTheUniqueConstraint() {
        // 结算重跑与每 5 分钟的实时路径都会撞上同一个键：
        // 那时候要么是**更新那一行**，要么是唯一约束抛异常 —— 后者会让结算失败
        Strategy strategy = anyStrategy();

        PaperTradeTrace first = paperTraceService.record(
                traceFor(strategy, ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED,
                        "2026-09-17 10:00:00"));
        PaperTradeTrace second = paperTraceService.record(
                traceFor(strategy, ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED,
                        "2026-09-17 10:05:00"));

        assertNotNull(second);
        assertEquals(first.getId(), second.getId(), "同一个键必须落到同一行");
        assertEquals(2, second.getRepeatCount());
        assertEquals(0L, paperTraceService.writeFailures(), "撞唯一键不该被记成写入失败");
    }

    @Test
    void twoDifferentBarsAreTwoRows() {
        Strategy strategy = anyStrategy();

        PaperTradeTrace first = paperTraceService.record(
                traceFor(strategy, ExecutionContract.DECISION_BUY, null, "2026-09-17 10:00:00"));
        PaperTradeTrace second = paperTraceService.record(
                traceFor(strategy, ExecutionContract.DECISION_BUY, null, "2026-09-17 10:05:00"));

        assertNotNull(first);
        assertNotNull(second);
        assertTrue(!first.getId().equals(second.getId()), "不同 bar 是两次不同的决策");
        assertEquals(2, paperTraceService.listForDay(strategy.getId(), LocalDate.of(2026, 9, 17)).size());
    }
}
