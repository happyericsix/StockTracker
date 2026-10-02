package com.happyericsix.stocktracker.service;

import java.util.List;
import java.util.Map;

/**
 * 资讯雷达的"文件夹"分类：封闭枚举 + 关键词规则分类器。
 *
 * <h3>两套清单，一张表</h3>
 * 个股流用：公司动态 / 业绩财务 / 行业关联 / 宏观经济 / 政策监管
 * 大盘流用：市场快讯 / 宏观经济 / 政策监管 / 地缘政治
 * 共用枚举（前端文件夹顺序也按这里的声明序渲染），个股流不出现"地缘政治"
 * 是因为关键词不命中自然就不会有这个文件夹，不需要两套类型系统。
 *
 * <h3>为什么规则分类器放在 Java 而不是 Python</h3>
 * 落库（upsert）与解读回写（applyAnalysis）都在 Java 侧，分类的**主战场**是落库
 * 那一刻；放 Python 意味着"存量回填"要跨服务调一次。规则只有十几行，
 * 放在使用它的地方，比"逻辑上一个家、物理上两处副本"更不容易漂移。
 *
 * <h3>与 AI 分类的分工</h3>
 * 规则打初值（保证每条都有文件夹可归），解读管线返回的 category 优先覆写 ——
 * 错分的代价只是一条进错文件夹，所以规则刻意保持"短词表、能讲出道理"，
 * 不追求覆盖率（这同 news_credibility 的词表纪律）。
 */
public final class NewsCategory {

    public static final String MARKET_FLASH = "市场快讯";
    public static final String MACRO = "宏观经济";
    public static final String POLICY = "政策监管";
    public static final String GEO = "地缘政治";
    public static final String OPINION = "舆论热度";
    public static final String COMPANY = "公司动态";
    public static final String EARNINGS = "业绩财务";
    public static final String INDUSTRY = "行业关联";

    /** 前端文件夹的渲染顺序（"最重要的问题在前"） */
    public static final List<String> ORDER = List.of(
            COMPANY, EARNINGS, INDUSTRY, MACRO, POLICY, GEO, OPINION, MARKET_FLASH);

    private static final Map<String, List<String>> KEYWORDS = Map.of(
            MACRO, List.of("央行", "降息", "加息", "lpr", "gdp", "cpi", "ppi", "通胀",
                    "国债", "汇率", "美联储", "社融", "m2", "pmi", "经济数据", "货币政", "财新中国制造业"),
            POLICY, List.of("证监会", "上交所", "深交所", "交易所", "新规", "监管", "处罚",
                    "立案", "调查", "ipo", "印花税", "国务院", "发改委", "财政部", "征求意见",
                    "指引", "管理办法", "退市"),
            GEO, List.of("关税", "制裁", "贸易战", "地缘", "军事", "冲突", "大选",
                    "g7", "g20", "中美", "国际关系", "外交部", "联合国"),
            OPINION, List.of("人气榜", "股吧", "热度", "千股千评", "散户情绪"),
            EARNINGS, List.of("业绩", "年报", "季报", "财报", "预增", "预亏", "预盈",
                    "净利润", "营收", "营业收入", "分红", "派息", "业绩快报", "盈利"),
            INDUSTRY, List.of("行业", "板块", "产业链", "产能", "涨价", "跌价", "下游",
                    "上游", "竞品", "同业"));

    private NewsCategory() {
    }

    /** 是否属于封闭枚举（解读管线回写的 category 先过这道门再落库） */
    public static boolean isValid(String category) {
        return category != null && ORDER.contains(category);
    }

    /**
     * 关键词规则分类。
     *
     * <p>顺序有意义：宏观/政策/地缘先判（它们的关键词最特异），
     * 无标的时默认"市场快讯"（大盘流的兜底文件夹），有标的时
     * 再看业绩/行业，最后落"公司动态"（个股流的兜底）。
     */
    public static String classify(String symbol, String title, String content) {
        String text = (safe(title) + " " + safe(title) + " " + safe(content)).toLowerCase();
        // 舆论词（人气榜/股吧）比宏观政策词更特异，先判，避免"股吧里聊降息"被归进宏观
        for (String category : List.of(OPINION, MACRO, POLICY, GEO)) {
            if (hits(text, KEYWORDS.get(category))) {
                return category;
            }
        }
        if (symbol == null || symbol.isBlank()) {
            return MARKET_FLASH;
        }
        if (hits(text, KEYWORDS.get(EARNINGS))) {
            return EARNINGS;
        }
        if (hits(text, KEYWORDS.get(INDUSTRY))) {
            return INDUSTRY;
        }
        return COMPANY;
    }

    private static boolean hits(String text, List<String> keywords) {
        if (keywords == null) {
            return false;
        }
        for (String keyword : keywords) {
            if (text.contains(keyword)) {
                return true;
            }
        }
        return false;
    }

    private static String safe(String value) {
        return value == null ? "" : value;
    }
}
