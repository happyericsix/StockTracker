package com.happyericsix.stocktracker.dto;

import lombok.Builder;
import java.io.Serializable;

@Builder
public record StockResponse(
        String symbol,
        String name,
        String price,
        String lastUpdated,
        // 涨跌方向，来自 Python 数据源（akshare/腾讯行情）解析出的「昨收」「涨跌额」「涨跌幅」。
        // 前端据此按 A 股约定给价格上色（红涨绿跌）；同时必须把带符号的数值显示出来作第二提示通道，
        // 不能让颜色单独承载含义。取不到时为 null，前端按中性色处理、不猜方向。
        String previousClose,
        String change,
        String changePercent) implements Serializable {
    private static final long serialVersionUID = 1L;
}