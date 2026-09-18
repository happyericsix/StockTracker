package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.service.ExpectationService;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 预期的读视图：一条**到期就能验的承诺**，以及它现在的状态。
 *
 * <h3>为什么读视图要带上"一句人话"和 pending 标志</h3>
 * 界面不该自己解释状态：{@code unmeasurable}（算不出来）与 {@code unmet}（未达成）
 * 是两件完全不同的事（一个是我们的数据不够，一个是策略没做到），
 * 让每个读者各自解读，迟早会有人把前者当成后者。
 * 所以判读与措辞都由 {@link ExpectationService#describe} 给出，界面只负责显示。
 *
 * <p>{@code latest} 为 null（从没登记过）时返回 null —— "没登记"与"登记了但状态未知"
 * 必须能被区分，界面据此显示"还没有人下过承诺"，而不是一个空的承诺。
 */
public record ExpectationResponse(LocalDate registeredAt, String metric, String metricLabel,
                                  BigDecimal threshold, LocalDate deadline, String status,
                                  BigDecimal outcome, LocalDateTime evaluatedAt, boolean pending,
                                  String sentence) {

    public static ExpectationResponse from(ExpectationService.Expectation expectation) {
        if (expectation == null) {
            return null;
        }
        return new ExpectationResponse(
                expectation.registeredAt(),
                expectation.metric(),
                ExpectationService.label(expectation.metric()),
                expectation.threshold(),
                expectation.deadline(),
                expectation.status(),
                expectation.outcome(),
                expectation.evaluatedAt(),
                expectation.isPending(),
                ExpectationService.describe(expectation));
    }
}
