package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import org.junit.jupiter.api.Test;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 痕迹的写入策略：**降噪、去重、防篡改戳**，以及"写不进去也不能影响结算"。
 *
 * <h3>为什么这些用例必须存在</h3>
 * 痕迹系统最危险的失败不是"没写"，而是"写得看起来很正常"：
 * 把每次实时检查都写成一行（噪声淹没结论）、重复结算写出两行同一天（对账对不上）、
 * 或者写失败时把异常抛回结算路径（为了记录而丢掉真实成交）。
 * 这三件事都不会报错，只会让痕迹慢慢变得不可信 —— 所以逐条钉住。
 */
class PaperTraceServiceTest {

    private final PaperTradeTraceRepository repository = mock(PaperTradeTraceRepository.class);
    private final PaperTraceService service = new PaperTraceService(repository);

    private static PaperTradeTrace trace(String settlementKind, String decision, String skipReason) {
        Strategy strategy = Strategy.builder().id(10L).name("s").symbol("600519").build();
        return PaperTradeTrace.builder()
                .strategy(strategy)
                .settlementKind(settlementKind)
                .trigger(ExecutionContract.TRIGGER_CRON)
                .tradeDate(LocalDate.of(2026, 9, 17))
                .barTime("2026-09-17 10:00:00")
                .symbol("600519")
                .decision(decision)
                .skipReason(skipReason)
                .repeatCount(1)
                .build();
    }

    // ==================== 1. 降噪：什么值得留痕 ====================

    @Test
    void dailySettlementAlwaysLeavesExactlyOneKindOfRow() {
        // 日线那天的结论永远值得留一行 —— 包括"什么都没做"
        assertTrue(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.DECISION_SKIP,
                        ExecutionContract.SKIP_RULE_NOT_MET)));
        assertTrue(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.DECISION_BUY, null)));
    }

    @Test
    void realtimeRuleNotMetIsNotTraced() {
        // 实时路径每 5 分钟一次：全都留痕就是 48 行/日/策略，结论会被噪声淹没
        assertFalse(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SKIP,
                        ExecutionContract.SKIP_RULE_NOT_MET)));
    }

    @Test
    void warmupIsNoiseLikeRuleNotMet() {
        // "指标还没算出来"与"规则没成立"一样：没有状态变化，重复出现没有信息量
        assertFalse(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SKIP,
                        ExecutionContract.SKIP_WARMUP)));
    }

    @Test
    void blockingSkipsAreAlwaysTraced() {
        for (String reason : new String[]{
                ExecutionContract.SKIP_LIMIT_BLOCKED,
                ExecutionContract.SKIP_T1_BLOCKED,
                ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT,
                ExecutionContract.SKIP_DATA_UNAVAILABLE,
                ExecutionContract.SKIP_NO_BAR,
                ExecutionContract.SKIP_MARKET_CLOSED}) {
            assertTrue(PaperTraceService.shouldTrace(
                            trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SKIP, reason)),
                    "阻塞型跳过必须留痕：" + reason);
        }
    }

    @Test
    void anUnknownReasonIsTreatedAsBlocking() {
        // 出现不认识的原因，正是最该看见的时候；判成"不阻塞"会让它从痕迹里彻底消失
        assertTrue(PaperTraceService.isBlocking("unknown_whatever"));
        assertTrue(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SKIP, "unknown_whatever")));
    }

    @Test
    void aFillIsAlwaysTraced() {
        assertTrue(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_BUY, null)));
        assertTrue(PaperTraceService.shouldTrace(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SELL, null)));
    }

    // ==================== 2. 去重键：同一件事的定义只有一处 ====================

    @Test
    void dailyKeyIgnoresTheDecisionSoARerunCannotDuplicate() {
        String first = PaperTraceService.dedupeKey(
                trace(ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.DECISION_SKIP,
                        ExecutionContract.SKIP_DATA_UNAVAILABLE));
        String second = PaperTraceService.dedupeKey(
                trace(ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.DECISION_BUY, null));

        assertEquals(first, second, "同一个交易日的两次结算必须落在同一个键上（后者覆盖前者）");
        assertEquals("daily:10:2026-09-17", first);
    }

    @Test
    void aFillKeyIncludesTheBarSoADifferentBarIsADifferentDecision() {
        PaperTradeTrace first = trace(ExecutionContract.SETTLEMENT_REALTIME,
                ExecutionContract.DECISION_BUY, null);
        PaperTradeTrace second = trace(ExecutionContract.SETTLEMENT_REALTIME,
                ExecutionContract.DECISION_BUY, null);
        second.setBarTime("2026-09-17 10:05:00");

        assertNotEquals(PaperTraceService.dedupeKey(first), PaperTraceService.dedupeKey(second));
    }

    @Test
    void aBlockingSkipKeyCollapsesRepeatsWithinTheDay() {
        // 一天的"T+1 挡着"只写一行；每 5 分钟一行会让痕迹变成流水账
        String key = PaperTraceService.dedupeKey(
                trace(ExecutionContract.SETTLEMENT_REALTIME, ExecutionContract.DECISION_SKIP,
                        ExecutionContract.SKIP_T1_BLOCKED));
        assertEquals("realtime:10:2026-09-17:t1_blocked", key);
    }

    // ==================== 3. 写入：重复累加、失败不影响结算 ====================

    @Test
    void aRepeatUpdatesTheRowInsteadOfAddingAnother() {
        PaperTradeTrace existing = trace(ExecutionContract.SETTLEMENT_REALTIME,
                ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED);
        existing.setId(7L);
        existing.setRepeatCount(3);
        existing.setCreatedAt(java.time.LocalDateTime.of(2026, 9, 17, 9, 35));
        when(repository.findByDedupeKey(any())).thenReturn(Optional.of(existing));
        when(repository.save(any(PaperTradeTrace.class))).thenAnswer(i -> i.getArgument(0));

        PaperTradeTrace incoming = trace(ExecutionContract.SETTLEMENT_REALTIME,
                ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_T1_BLOCKED);
        PaperTradeTrace saved = service.record(incoming);

        assertNotNull(saved);
        assertEquals(7L, saved.getId(), "重复出现必须落到同一行");
        assertEquals(4, saved.getRepeatCount(), "次数要累加");
        assertEquals(java.time.LocalDateTime.of(2026, 9, 17, 9, 35), saved.getCreatedAt(),
                "首次发生的时间不能被覆盖 —— 那是「什么时候开始挡住的」的答案");
        verify(repository, times(1)).save(any(PaperTradeTrace.class));
    }

    @Test
    void aRepeatOverwritesTheConclusionSoTheLastWordWins() {
        // 首次可能是"取数不可用"，当天稍后成功结算：读者要看到的是成功那次的结论
        PaperTradeTrace existing = trace(ExecutionContract.SETTLEMENT_DAILY,
                ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_DATA_UNAVAILABLE);
        existing.setId(7L);
        when(repository.findByDedupeKey(any())).thenReturn(Optional.of(existing));
        when(repository.save(any(PaperTradeTrace.class))).thenAnswer(i -> i.getArgument(0));

        PaperTradeTrace incoming = trace(ExecutionContract.SETTLEMENT_DAILY,
                ExecutionContract.DECISION_BUY, null);
        PaperTradeTrace saved = service.record(incoming);

        assertEquals(ExecutionContract.DECISION_BUY, saved.getDecision());
        assertNull(saved.getSkipReason(), "上一次的失败原因必须被清掉，否则会挂着一条不存在的失败");
    }

    @Test
    void aWriteFailureNeverThrows() {
        when(repository.findByDedupeKey(any())).thenThrow(new RuntimeException("db down"));

        PaperTradeTrace saved = service.record(
                trace(ExecutionContract.SETTLEMENT_DAILY, ExecutionContract.DECISION_BUY, null));

        assertNull(saved, "写不进去只能返回 null：结算已经算完了，痕迹不该回滚它");
        assertEquals(1L, service.writeFailures(), "失败必须被计数 —— 悄悄不写痕迹比没有痕迹更危险");
    }

    @Test
    void aSkippedTraceIsNotPersistedAtAll() {
        service.record(trace(ExecutionContract.SETTLEMENT_REALTIME,
                ExecutionContract.DECISION_SKIP, ExecutionContract.SKIP_RULE_NOT_MET));
        verify(repository, times(0)).save(any(PaperTradeTrace.class));
    }

    // ==================== 4. 防篡改戳 ====================

    @Test
    void theHashIsStableForTheSameValues() {
        PaperTradeTrace first = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1000.00"));
        PaperTradeTrace second = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1000.00"));

        assertEquals(PaperTraceService.traceHash(first), PaperTraceService.traceHash(second));
        assertEquals(64, PaperTraceService.traceHash(first).length(), "sha256 十六进制");
    }

    @Test
    void theHashChangesWhenTheMoneyChanges() {
        PaperTradeTrace first = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1000.00"));
        PaperTradeTrace second = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1100.00"));

        assertNotEquals(PaperTraceService.traceHash(first), PaperTraceService.traceHash(second),
                "钱变了戳必须变，否则对账时它没有意义");
    }

    @Test
    void theHashIgnoresFieldsThatAreNotPartOfTheConclusion() {
        // 只取"结论所依赖的值"：把整行 JSON 拿来算，字段顺序/空值写法一变戳就变，失去对账价值
        PaperTradeTrace first = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1000.00"));
        PaperTradeTrace second = moneyTrace(new BigDecimal("10.00"), new BigDecimal("1000.00"));
        second.setSnapshotJson("{\"bar\":{\"close\":10.0}}");

        assertEquals(PaperTraceService.traceHash(first), PaperTraceService.traceHash(second));
    }

    private static PaperTradeTrace moneyTrace(BigDecimal price, BigDecimal equityAfter) {
        PaperTradeTrace trace = trace(ExecutionContract.SETTLEMENT_DAILY,
                ExecutionContract.DECISION_BUY, null);
        trace.setBarClose(price);
        trace.setCashAfter(new BigDecimal("0.00"));
        trace.setSharesAfter(new BigDecimal("1000.0000"));
        trace.setEquityAfter(equityAfter);
        trace.setEngineVersion("2a9dce9df73b");
        trace.setAdjustMode(ExecutionContract.ADJUST_QFQ);
        trace.setMoneyPolicyVersion(Money.POLICY_VERSION);
        return trace;
    }

    // ==================== 5. 每个原因都得有人话 ====================

    @Test
    void everyClosedSetReasonHasAHumanSentence() {
        // 少一句的后果：痕迹里出现一个没人翻译的原因，读者只能回去读代码 ——
        // 而"不看代码能明白为什么"正是这一层存在的理由
        for (String reason : ExecutionContract.SKIP_REASONS) {
            String sentence = com.happyericsix.stocktracker.dto.PaperTradeTraceResponse.sentenceFor(reason);
            assertNotNull(sentence, reason);
            assertNotEquals(reason, sentence, "「" + reason + "」还没有人话翻译");
        }
    }

    @Test
    void anUnknownReasonStillProducesAReadableSentence() {
        String sentence = com.happyericsix.stocktracker.dto.PaperTradeTraceResponse
                .sentenceFor("unknown_gap_up_limit");
        assertTrue(sentence.contains("未登记"), sentence);
    }
}
