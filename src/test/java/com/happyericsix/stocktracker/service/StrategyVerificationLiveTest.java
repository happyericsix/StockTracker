package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.context.TestPropertySource;
import tools.jackson.databind.JsonNode;

import java.time.LocalDate;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 端到端（**真实行情**）：行情 → 样本外验证 → 客观事实 → 盘后复盘报告。
 *
 * <h3>为什么这条链必须有一次真跑</h3>
 * 单测里每一段都是替身，替身之间"接得上"是我们自己以为的。真跑一次能验到替身验不到的东西：
 * ① Java 发出的请求体（字段名/形状）Python 端点是否真的接受；
 * ② 引擎版本、有效格子数、汇总字段是不是在**真实响应**里（而不是在测试夹具里）；
 * ③ 报告渲染出来的那句"规则证据"引用的是不是**刚才这次**的真实数字。
 *
 * <p>不写数据库：事实走 mock（只捕获），报告走 mock（只捕获文本）。
 * 目的是验证链路与口径，而不是往用户的记忆通道里塞测试数据。
 *
 * <p>Python 数据服务没起来时**跳过**（{@code assumeTrue}）而不是失败：
 * 这条链路依赖外部服务与公网行情，把它做成"必须绿"的用例会让整套测试变得不可信。
 */
@SpringBootTest
@TestPropertySource(properties = {
        // 测试类路径上的 application.properties **完全取代**主配置（同名文件只有一个生效），
        // 所以服务间密钥在测试里默认是空的 —— 不显式给上，客户端就不会带 X-Internal-Token，
        // 真跑起来只会得到一个 401，看起来像"接口坏了"。
        // 这里用的是 application.properties 里写明的开发默认值；生产由 INTERNAL_API_TOKEN 覆盖。
        "internal.api-token=${INTERNAL_API_TOKEN:stock-tracker-internal-2026}",
        "akshare.api.base-url=http://127.0.0.1:8000"})
class StrategyVerificationLiveTest {

    @Autowired
    private StrategyClient realStrategyClient;

    private static final String CONFIG = "{\"schema_version\":\"1.0\",\"name\":\"上穿60日线\","
            + "\"symbol\":\"600519\",\"initial_capital\":100000,"
            + "\"entry\":{\"logic\":\"all\",\"conditions\":["
            + "{\"type\":\"price_cross_ma\",\"window\":60,\"direction\":\"above\"}]},"
            + "\"exit\":{\"logic\":\"any\",\"conditions\":["
            + "{\"type\":\"price_cross_ma\",\"window\":60,\"direction\":\"below\"}]},"
            + "\"risk\":{\"commission_pct\":0.1,\"slippage_pct\":0.1}}";

    private static Strategy strategy() {
        return Strategy.builder().id(999L).name("上穿60日线").symbol("600519")
                .configJson(CONFIG)
                .user(User.builder().id(1L).username("live-itest").email("live@example.com").password("x").build())
                .build();
    }

    @Test
    void theWholeChainCitesRealNumbers() {
        // 先用真实客户端探一次：服务不通就跳过；**服务通了但没授权则必须失败** ——
        // 那是配置错误（密钥/请求头不对），跳过它等于把"整条链路实际走不通"藏起来。
        JsonNode probe;
        try {
            probe = realStrategyClient.backtestMatrix(CONFIG, List.of("600519"), 1,
                    LocalDate.now().minusYears(1).toString(), LocalDate.now().toString());
        } catch (org.springframework.web.reactive.function.client.WebClientResponseException e) {
            if (e.getStatusCode().value() == 401 || e.getStatusCode().value() == 403) {
                throw new AssertionError("Python 数据服务拒绝了这次调用（HTTP "
                        + e.getStatusCode().value() + "）：服务间密钥不一致。"
                        + "检查 internal.api-token 与 python-data-service/.env 的 INTERNAL_API_TOKEN", e);
            }
            throw e;
        } catch (Exception e) {
            assumeTrue(false, "Python 数据服务不可用，跳过端到端验证: " + e.getMessage());
            return;
        }
        assumeTrue(probe != null && probe.hasNonNull("valid") && probe.get("valid").asBoolean(false),
                "Python 数据服务没有返回有效结果，跳过端到端验证");

        // ---------- ① 真实验证 → 客观事实（事实走替身，只捕获） ----------
        MemoryFactService factService = mock(MemoryFactService.class);
        MemoryService memoryService = mock(MemoryService.class);
        when(memoryService.currentSessionKey(1L)).thenReturn("1:live");
        StrategyService verification = new StrategyService(
                mock(StrategyRepository.class), realStrategyClient,
                mock(UserRepository.class), factService, memoryService);

        Strategy strategy = strategy();
        JsonNode result = verification.verifyMatrix(strategy.getUser(), strategy);
        assertNotNull(result, "真实验证必须返回结果");

        ArgumentCaptor<List<MemoryFactRequest>> factsCaptor = ArgumentCaptor.forClass(List.class);
        verify(factService).recordObjective(eq(1L), anyString(), factsCaptor.capture());
        Map<String, String> facts = new HashMap<>();
        factsCaptor.getValue().forEach(fact -> facts.put(fact.getPredicate(), fact.getObject()));

        int validCells = Integer.parseInt(facts.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT));
        assertTrue(validCells > 0, "真实行情下应当有可用格子，实际 facts=" + facts);
        assertTrue(facts.containsKey(ObjectiveFactKeys.VERIFY_ENGINE_VERSION),
                "验证结论必须带上引擎版本（口径）：" + facts);

        // ---------- ② 从事实读回结论 ----------
        MemoryFactService readingFacts = mock(MemoryFactService.class);
        when(readingFacts.activeFactValues(eq(1L), eq("strategy:999"), anyList())).thenReturn(facts);
        StrategyService reading = new StrategyService(mock(StrategyRepository.class), realStrategyClient,
                mock(UserRepository.class), readingFacts, memoryService);
        StrategyService.VerificationSummary summary = reading.latestVerification(1L, 999L);

        assertNotNull(summary);
        assertTrue(summary.hasSample());
        assertEquals(validCells, summary.validCells());

        // ---------- ③ 盘后复盘报告引用这些真实数字 ----------
        PaperTraceService traceService = mock(PaperTraceService.class);
        PaperTradeTrace blocking = PaperTradeTrace.builder()
                .id(1L).strategy(strategy).settlementKind(ExecutionContract.SETTLEMENT_DAILY)
                .trigger(ExecutionContract.TRIGGER_CRON).tradeDate(LocalDate.now())
                .symbol("600519").decision(ExecutionContract.DECISION_SKIP)
                .skipReason(ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT)
                .fillBasis(ExecutionContract.FILL_CLOSE).repeatCount(1)
                .build();
        when(traceService.listForDay(eq(999L), any(LocalDate.class))).thenReturn(List.of(blocking));

        MessageService messageService = mock(MessageService.class);
        PaperReviewReportService report = new PaperReviewReportService(traceService,
                mock(com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository.class),
                reading, messageService);

        PaperAccount account = PaperAccount.builder().id(1L)
                .initialCapital(new java.math.BigDecimal("100000.00"))
                .cash(new java.math.BigDecimal("100000.00"))
                .shares(java.math.BigDecimal.ZERO)
                .equity(new java.math.BigDecimal("100000.00")).build();
        report.composeDailyReport(strategy, account, LocalDate.now());

        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(any(), eq(PaperReviewReportService.TYPE_PAPER_REPORT),
                eq("paper_daily:999:" + LocalDate.now()), eq("600519"), content.capture());

        String text = content.getValue();
        System.out.println("=== 真实端到端生成的复盘报告 ===\n" + text);
        // 报告必须同时引用**痕迹**与**真实验证结论**
        assertTrue(text.contains(PaperTradeTraceResponse.sentenceFor(
                ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT)), text);
        assertTrue(text.contains(validCells + " 个有效格子里"), text);
        assertTrue(text.contains(facts.get(ObjectiveFactKeys.VERIFY_ENGINE_VERSION)), text);
    }
}
