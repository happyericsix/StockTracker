package com.happyericsix.stocktracker.dto;

import java.io.Serializable;

/**
 * 技术指标快照：一次预警评估所需的指标值。
 *
 * 字段命名与 Python 服务 {@code /api/v1/indicators/{symbol}} 返回的 {@code indicators.*} 对齐。
 * 来源：{@code StockService.getIndicators()} 从 Python 服务拉取（缓存 5 分钟）。
 *
 * <p><b>只有两个字段</b>，因为只有它们有消费者：
 * <ul>
 *   <li>{@code rsi} —— RSI 超买 / 超卖两个评估器</li>
 *   <li>{@code macdHist} —— MACD 金叉 / 死叉两个评估器</li>
 * </ul>
 * 这里原先还有 {@code macdDif}/{@code macdDea}/{@code bollUpper}/{@code bollMiddle}/
 * {@code bollLower}/{@code ma5}/{@code ma20} 七个字段，全项目没有任何读取点，而
 * {@code StockPriceRefreshJob} 每 5 分钟就会为每只自选股拉一次完整载荷 ——
 * 等于持续计算并缓存没人要的值。Python 侧已同步停止返回它们（见 quant_model.analyze_stock）。
 * 需要新指标时，两处一起加，不要只加一边。
 *
 * <p>实现 Serializable：Redis 缓存使用 JdkSerializationRedisSerializer，被缓存对象必须可序列化。
 * serialVersionUID 固定为 1L，因此增删字段对已存在的缓存条目是兼容的
 * （启动时 CacheClearRunner 也会清空全部缓存）。
 */
public class IndicatorData implements Serializable {

    private static final long serialVersionUID = 1L;
    private Double rsi;            // 14 日 RSI，0~100
    private Double macdHist;       // MACD 柱状值

    public IndicatorData() {}

    public Double getRsi() { return rsi; }
    public void setRsi(Double rsi) { this.rsi = rsi; }

    public Double getMacdHist() { return macdHist; }
    public void setMacdHist(Double macdHist) { this.macdHist = macdHist; }
}
