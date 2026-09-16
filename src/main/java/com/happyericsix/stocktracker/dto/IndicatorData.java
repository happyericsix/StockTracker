package com.happyericsix.stocktracker.dto;

import java.io.Serializable;

/**
 * 技术指标快照：一次评估所需的全部指标值
 * 字段命名与 Python 服务 /api/v1/indicators/{symbol} 返回的 indicators.* 对齐
 *
 * 来源：StockService.getIndicators() 从 Python akshare 服务拉取（已缓存 5 分钟）
 * 实现 Serializable：Redis 缓存使用 JdkSerializationRedisSerializer，被缓存对象必须可序列化。
 */
public class IndicatorData implements Serializable {

    private static final long serialVersionUID = 1L;
    private Double rsi;            // 14 日 RSI，0~100
    private Double macdDif;        // MACD 快线
    private Double macdDea;        // MACD 慢线（信号线）
    private Double macdHist;       // MACD 柱状值
    private Double bollUpper;      // 布林带上轨
    private Double bollMiddle;     // 布林带中轨
    private Double bollLower;      // 布林带下轨
    private Double ma5;
    private Double ma20;

    public IndicatorData() {}

    public Double getRsi() { return rsi; }
    public void setRsi(Double rsi) { this.rsi = rsi; }

    public Double getMacdDif() { return macdDif; }
    public void setMacdDif(Double macdDif) { this.macdDif = macdDif; }

    public Double getMacdDea() { return macdDea; }
    public void setMacdDea(Double macdDea) { this.macdDea = macdDea; }

    public Double getMacdHist() { return macdHist; }
    public void setMacdHist(Double macdHist) { this.macdHist = macdHist; }

    public Double getBollUpper() { return bollUpper; }
    public void setBollUpper(Double bollUpper) { this.bollUpper = bollUpper; }

    public Double getBollMiddle() { return bollMiddle; }
    public void setBollMiddle(Double bollMiddle) { this.bollMiddle = bollMiddle; }

    public Double getBollLower() { return bollLower; }
    public void setBollLower(Double bollLower) { this.bollLower = bollLower; }

    public Double getMa5() { return ma5; }
    public void setMa5(Double ma5) { this.ma5 = ma5; }

    public Double getMa20() { return ma20; }
    public void setMa20(Double ma20) { this.ma20 = ma20; }

    /** 指标是否可用（至少有 RSI） */
    public boolean isUsable() {
        return rsi != null;
    }
}
