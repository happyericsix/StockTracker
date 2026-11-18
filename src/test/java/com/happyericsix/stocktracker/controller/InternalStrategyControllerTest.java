package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.entity.PaperAuthorization;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAuthorizationRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.ExpectationService;
import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.PaperEquityBackfillService;
import com.happyericsix.stocktracker.service.PaperTradingService;
import com.happyericsix.stocktracker.service.TradeJournalService;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.http.ResponseEntity;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 复盘导出的<b>推导字段</b>（2026-11-17）：cashUsedPct / soldSharePct / authorization。
 *
 * <p>这三个字段存在的理由是治"agent 复盘误诊"：助手只看到配置 size_pct 与成交结果时，
 * 只能拿通用常识猜"为什么配 50% 只买了 10%"，把三层夹取、agent 部分卖出这些设计
 * 行为断言成 bug。钉住的正是推导规则与"取不到就不出现"的纪律 —— 这两条任何一条
 * 退化，导出就会重新变回"只有数字没有原因"，误诊通道原样打开。
 */
class InternalStrategyControllerTest {

    private static final String TOKEN = "s3cret-internal";
    private static final String USERNAME = "alice";

    private final PaperTradingService paperTradingService = Mockito.mock(PaperTradingService.class);
    private final PaperAuthorizationRepository authorizationRepository =
            Mockito.mock(PaperAuthorizationRepository.class);

    private InternalStrategyController controller(UserRepository userRepository,
                                                  StrategyRepository strategyRepository) {
        return new InternalStrategyController(userRepository, strategyRepository,
                paperTradingService, Mockito.mock(TradeJournalService.class),
                Mockito.mock(ExpectationService.class), Mockito.mock(JobRunService.class),
                authorizationRepository, Mockito.mock(PaperEquityBackfillService.class), TOKEN);
    }

    /** 用户 + 策略都解析成功的最小桩；服务层默认返回 null（各节 fail-open 置 null）。 */
    private InternalStrategyController workingController() {
        UserRepository userRepository = Mockito.mock(UserRepository.class);
        StrategyRepository strategyRepository = Mockito.mock(StrategyRepository.class);
        User user = User.builder().id(7L).username(USERNAME).build();
        Strategy strategy = Strategy.builder().id(4L).user(user).symbol("002548")
                .name("金新农60日线").configJson("{\"position\":{\"type\":\"percent\",\"size_pct\":50}}")
                .build();
        when(userRepository.findById(7L)).thenReturn(Optional.of(user));
        when(strategyRepository.findByIdAndUserId(4L, 7L)).thenReturn(Optional.of(strategy));
        return controller(userRepository, strategyRepository);
    }

    private static PaperTradeResponse trade(String side, BigDecimal shares, Long traceId) {
        return new PaperTradeResponse(LocalDate.of(2026, 11, 16), "002548", side,
                new BigDecimal("4.22"), shares, shares.multiply(new BigDecimal("4.22")),
                "跌破60日线", null, traceId);
    }

    private static PaperTradeTraceResponse trace(long id, String cashBefore, String cashAfter,
                                                 String sharesBefore) {
        PaperTradeTraceResponse t = new PaperTradeTraceResponse();
        t.setId(id);
        if (cashBefore != null) {
            t.setCashBefore(new BigDecimal(cashBefore));
        }
        if (cashAfter != null) {
            t.setCashAfter(new BigDecimal(cashAfter));
        }
        if (sharesBefore != null) {
            t.setSharesBefore(new BigDecimal(sharesBefore));
        }
        return t;
    }

    @SuppressWarnings("unchecked")
    private Map<String, Object> reviewBody(InternalStrategyController controller) {
        ResponseEntity<Map<String, Object>> resp =
                controller.review(4L, 7L, TOKEN);
        assertEquals(200, resp.getStatusCode().value());
        return resp.getBody();
    }

    @Test
    void buyCarriesCashUsedPctFromTrace() {
        InternalStrategyController controller = workingController();
        // 买入 9727（含费）时现金 100000 → 9.7%：解释"配置 50% 为何只买了 10%"的那个数
        when(paperTradingService.getTrades(USERNAME, 4L)).thenReturn(List.of(
                trade("BUY", new BigDecimal("2300"), 11L)));
        when(paperTradingService.getTraces(eq(USERNAME), eq(4L), anyInt()))
                .thenReturn(List.of(trace(11L, "100000.00", "90273.40", null)));

        List<Map<String, Object>> trades = (List<Map<String, Object>>) reviewBody(controller).get("trades");

        assertEquals(0, new BigDecimal("9.7").compareTo(
                (BigDecimal) trades.get(0).get("cashUsedPct")));
        assertFalse(trades.get(0).containsKey("soldSharePct"), "买入行不该出现卖出比例");
    }

    @Test
    void sellCarriesSoldSharePctFromTrace() {
        InternalStrategyController controller = workingController();
        // 2300 股里卖 1100 → 47.8%：agent size_fraction 部分卖出的对账依据
        when(paperTradingService.getTrades(USERNAME, 4L)).thenReturn(List.of(
                trade("SELL", new BigDecimal("1100"), 12L)));
        when(paperTradingService.getTraces(eq(USERNAME), eq(4L), anyInt()))
                .thenReturn(List.of(trace(12L, "90273.40", "94574.40", "2300")));

        List<Map<String, Object>> trades = (List<Map<String, Object>>) reviewBody(controller).get("trades");

        assertEquals(0, new BigDecimal("47.8").compareTo(
                (BigDecimal) trades.get(0).get("soldSharePct")));
        assertFalse(trades.get(0).containsKey("cashUsedPct"), "卖出行不该出现买入比例");
    }

    /**
     * 没有对应痕迹（早于痕迹功能/已滑出窗口）→ 推导字段整个不出现。
     * 填 0 会被读成"全额动用"，填 null 会被当缺数硬答：键不在才是"没有依据"。
     */
    @Test
    void tradeWithoutTraceOmitsDerivedFieldsEntirely() {
        InternalStrategyController controller = workingController();
        when(paperTradingService.getTrades(USERNAME, 4L)).thenReturn(List.of(
                trade("BUY", new BigDecimal("2300"), null),
                trade("SELL", new BigDecimal("1100"), 99L)));
        when(paperTradingService.getTraces(eq(USERNAME), eq(4L), anyInt()))
                .thenReturn(List.of());

        List<Map<String, Object>> trades = (List<Map<String, Object>>) reviewBody(controller).get("trades");

        assertFalse(trades.get(0).containsKey("cashUsedPct"));
        assertFalse(trades.get(1).containsKey("soldSharePct"));
    }

    @Test
    void authorizationExportedWhenPresent() {
        InternalStrategyController controller = workingController();
        PaperAuthorization auth = PaperAuthorization.builder()
                .maxPositionPct(new BigDecimal("0.10"))
                .intradayExecution(true).allowOpen(true).allowClose(true)
                .build();
        when(authorizationRepository.findLatest(4L)).thenReturn(Optional.of(auth));

        Map<String, Object> body = reviewBody(controller);
        Map<String, Object> authorization = (Map<String, Object>) body.get("authorization");

        assertEquals(new BigDecimal("0.10"), authorization.get("maxPositionPct"));
        assertEquals(true, authorization.get("intradayExecution"));
    }

    /** rule 模式的策略没有授权记录：authorization 节为 null，而不是空对象。 */
    @Test
    void authorizationNullWhenAbsent() {
        InternalStrategyController controller = workingController();
        when(authorizationRepository.findLatest(anyLong())).thenReturn(Optional.empty());

        assertNull(reviewBody(controller).get("authorization"));
    }

    @Test
    void rejectsMissingToken() {
        InternalStrategyController controller = workingController();

        assertEquals(401, controller.review(4L, 7L, null).getStatusCode().value());
        assertEquals(401, controller.review(4L, 7L, "wrong").getStatusCode().value());
    }
}
