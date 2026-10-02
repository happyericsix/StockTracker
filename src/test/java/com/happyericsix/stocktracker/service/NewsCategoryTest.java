package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 文件夹分类的关键词规则：钉住"判据能讲出道理"的那一批边界。
 */
class NewsCategoryTest {

    @Test
    void macroKeywordsBeatDefaultFolders() {
        assertEquals(NewsCategory.MACRO,
                NewsCategory.classify("SH600519", "央行宣布降息0.25个百分点", "LPR 下调"));
        assertEquals(NewsCategory.POLICY,
                NewsCategory.classify(null, "证监会发布减持新规", "监管指引"));
        assertEquals(NewsCategory.GEO,
                NewsCategory.classify(null, "G7 会议讨论对华关税", "制裁清单"));
    }

    @Test
    void marketFeedWithoutSymbolFallsBackToFlash() {
        assertEquals(NewsCategory.MARKET_FLASH,
                NewsCategory.classify(null, "两市成交额突破两万亿", "毫无关键词命中"));
    }

    @Test
    void stockEventsFallBackToCompany() {
        assertEquals(NewsCategory.COMPANY,
                NewsCategory.classify("SH600519", "公司召开股东大会", "例行通知"));
    }

    @Test
    void earningsAndIndustryDetectedForSymbolEvents() {
        assertEquals(NewsCategory.EARNINGS,
                NewsCategory.classify("SH600519", "公司发布业绩预增公告", "净利润同比增长"));
        assertEquals(NewsCategory.INDUSTRY,
                NewsCategory.classify("SH600519", "白酒行业动销回暖", "产业链库存下降"));
    }

    @Test
    void titleWeightedDoubleThanContent() {
        // 标题权重×2 是 _score_content 的纪律，这里只钉"标题命中即可分类"
        assertEquals(NewsCategory.POLICY,
                NewsCategory.classify("SZ000001", "交易所发布新规", "内容平平无奇"));
    }

    @Test
    void opinionKeywordsDetectedBeforeMacro() {
        assertEquals(NewsCategory.OPINION,
                NewsCategory.classify(null, "股吧人气榜：赛力斯居首", "人气榜前十"));
    }

    @Test
    void isValidRejectsUnknownCategory() {
        assertTrue(NewsCategory.isValid("公司动态"));
        assertFalse(NewsCategory.isValid("科技前沿"));
        assertFalse(NewsCategory.isValid(null));
    }
}
