package com.happyericsix.stocktracker.dto;

import java.util.List;

/**
 * 资讯搜索请求（plan N3 的 body 形状）。
 *
 * <p>默认值写在字段上而不是散在 service 里：{@code days=7 / page=0 / size=20} 是
 * 端点契约的一部分，请求体里省略某个键时必须与显式传默认值**等价**。
 */
public class NewsSearchRequest {

    /** 标的（可空）：如 600519 / SH600519；空 = 按关键词或全市场搜 */
    private String symbol;

    /** 关键词（可空）：命中标题或正文 */
    private String keyword;

    /** 1公告 2媒体 3研报 4舆情；空 = 让 Python 用它自己的默认源集合（1/2/3） */
    private List<Integer> types;

    /** 时间窗口（天） */
    private Integer days = 7;

    /** {@code mine} = 只看与我的自选相关的事件；空 = 全市场 */
    private String scope;

    private Integer page = 0;
    private Integer size = 20;

    public NewsSearchRequest() {}

    public NewsSearchRequest(String symbol, String keyword, List<Integer> types,
                             Integer days, String scope, Integer page, Integer size) {
        this.symbol = symbol;
        this.keyword = keyword;
        this.types = types;
        this.days = days;
        this.scope = scope;
        this.page = page;
        this.size = size;
    }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getKeyword() { return keyword; }
    public void setKeyword(String keyword) { this.keyword = keyword; }
    public List<Integer> getTypes() { return types; }
    public void setTypes(List<Integer> types) { this.types = types; }
    public Integer getDays() { return days; }
    public void setDays(Integer days) { this.days = days; }
    public String getScope() { return scope; }
    public void setScope(String scope) { this.scope = scope; }
    public Integer getPage() { return page; }
    public void setPage(Integer page) { this.page = page; }
    public Integer getSize() { return size; }
    public void setSize(Integer size) { this.size = size; }
}
