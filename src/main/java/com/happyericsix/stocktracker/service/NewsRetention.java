package com.happyericsix.stocktracker.service;

import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

import java.util.Map;

/**
 * 资讯保留期（"时效踢除"的规则表）。
 *
 * <h3>为什么按分类定、而不是一刀切</h3>
 * 不同信息的半衰期本来就不同：快讯过期即噪音（3 天），宏观/政策有后续
 * 跟踪价值（30 天），而公告与研报是法定披露和深度参考（180 天）。
 * 一刀切的窗口要么把公告过早删掉，要么让快讯堆成噪音。
 *
 * <h3>信源级别优先于分类</h3>
 * 公告（level 1）和研报（level 3）无论关键词把它们分进哪个文件夹，
 * 都按披露类保留期（默认 180 天）处理 —— 分类管"放哪个文件夹"，
 * 保留期管"留多久"，两件事的正交在这里体现。
 */
@Component
public class NewsRetention {

    private final int flashDays;
    private final int macroDays;
    private final int policyDays;
    private final int geoDays;
    private final int companyDays;
    private final int earningsDays;
    private final int industryDays;
    private final int defaultDays;
    /** 公告/研报（披露与深度参考类） */
    private final int disclosureDays;

    public NewsRetention(
            @Value("${news.retention.flash-days:3}") int flashDays,
            @Value("${news.retention.macro-days:30}") int macroDays,
            @Value("${news.retention.policy-days:30}") int policyDays,
            @Value("${news.retention.geo-days:30}") int geoDays,
            @Value("${news.retention.company-days:30}") int companyDays,
            @Value("${news.retention.earnings-days:30}") int earningsDays,
            @Value("${news.retention.industry-days:30}") int industryDays,
            @Value("${news.retention.default-days:30}") int defaultDays,
            @Value("${news.retention.disclosure-days:180}") int disclosureDays) {
        this.flashDays = flashDays;
        this.macroDays = macroDays;
        this.policyDays = policyDays;
        this.geoDays = geoDays;
        this.companyDays = companyDays;
        this.earningsDays = earningsDays;
        this.industryDays = industryDays;
        this.defaultDays = defaultDays;
        this.disclosureDays = disclosureDays;
    }

    /** 浏览流里需要单独设保留期的分类 → 天数；不在表里的用 defaultDays */
    public Map<String, Integer> categoryDays() {
        return Map.of(
                NewsCategory.MARKET_FLASH, flashDays,
                NewsCategory.MACRO, macroDays,
                NewsCategory.POLICY, policyDays,
                NewsCategory.GEO, geoDays,
                NewsCategory.COMPANY, companyDays,
                NewsCategory.EARNINGS, earningsDays,
                NewsCategory.INDUSTRY, industryDays);
    }

    public int disclosureDays() {
        return disclosureDays;
    }

    public int defaultDays() {
        return defaultDays;
    }
}
