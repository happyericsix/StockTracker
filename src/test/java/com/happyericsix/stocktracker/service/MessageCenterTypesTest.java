package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

/**
 * 消息中心"全部/模拟盘/预警"三分 tab 的类型口径语义（2026-11-12 二轮）。
 *
 * <p>用户实跑反馈驱动："全部消息被预警淹没、模拟盘一点声音都没有"——
 * 这里的每条语义都有产品含义，改坏了 tab 就名存实亡：
 * <ul>
 *   <li>type 多类型（模拟盘 tab）：PAPER_SETTLE,STRATEGY_SENTINEL,STRATEGY_REVIEW；</li>
 *   <li>excludeType（全部 tab 排除 ALERT）：高频预警只活在预警 tab；</li>
 *   <li>口径外的类型一律剔除（不破例放行——口径外放行等于收回窄白做）；</li>
 *   <li>PAPER_SETTLE 必须在口径内（模拟盘每日结算的声音是这次修订的核心目的）。</li>
 * </ul>
 */
class MessageCenterTypesTest {

    @Test
    void paperSettleIsInCenterTypes() {
        // 模拟盘结算进消息中心与未读角标：不交易也要说话
        assertThat(MessageService.centerTypes(null, null)).contains("PAPER_SETTLE");
    }

    @Test
    void allTabExcludesAlertOnly() {
        List<String> types = MessageService.centerTypes(null, "ALERT");
        assertThat(types).doesNotContain("ALERT");
        assertThat(types).contains("PAPER_SETTLE", "STRATEGY_SENTINEL", "STRATEGY_REVIEW", "NEWS_ALERT");
    }

    @Test
    void commaSeparatedTypeList() {
        List<String> types = MessageService.centerTypes("PAPER_SETTLE,STRATEGY_SENTINEL,STRATEGY_REVIEW", null);
        assertThat(types).containsExactlyInAnyOrder("PAPER_SETTLE", "STRATEGY_SENTINEL", "STRATEGY_REVIEW");
    }

    @Test
    void typeOutsideCenterIsRejectedNotLeaked() {
        // CHAT_BOT 想借 type 参数混进消息中心：口径外 → 空（返回空页而不是破例放行）
        assertThat(MessageService.centerTypes("CHAT_BOT", null)).isEmpty();
        assertThat(MessageService.centerTypes("ALERT,CHAT_BOT", null))
                .containsExactly("ALERT");
    }

    @Test
    void excludeIgnoredWhenTypePresent() {
        // 显式 type 优先：预警 tab 的 type=ALERT 不被 excludeType 干扰
        assertThat(MessageService.centerTypes("ALERT", "ALERT")).containsExactly("ALERT");
    }

    @Test
    void blankInputsMeanFullScope() {
        assertThat(MessageService.centerTypes("", " "))
                .containsExactlyElementsOf(MessageService.centerTypes(null, null));
    }
}
