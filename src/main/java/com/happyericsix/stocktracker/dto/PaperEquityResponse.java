package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.service.PaperEquitySeries;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 净值曲线：**点 + 由同一份点算出来的汇总**。
 *
 * <h3>为什么把汇总和点放在同一个响应里</h3>
 * 汇总（回撤、空仓比例、相对买入持有的超额）由 {@link PaperEquitySeries} 从同一批点算出。
 * 如果前端自己算一遍，就会出现"界面上写着 -8%，报告里写着 -7.8%"这种事 ——
 * 用户没法判断哪个是真的，最后谁都不信。算法只有一处，接口只是把它送出去。
 */
public class PaperEquityResponse {

    /** 曲线上的一个交易日。 */
    public record Point(LocalDate tradeDate, BigDecimal equity, BigDecimal cash,
                        BigDecimal shares, BigDecimal closePrice, boolean holding) {
        public static Point from(PaperEquitySnapshot entity) {
            boolean holding = entity.getShares() != null && entity.getShares().signum() > 0;
            return new Point(entity.getTradeDate(), entity.getEquity(), entity.getCash(),
                    entity.getShares(), entity.getClosePrice(), holding);
        }
    }

    private List<Point> points;
    /** 汇总口径：只有一天时 returnPct 为 0、超额为 null（谈不上"期间"）。 */
    private PaperEquitySeries.Summary summary;

    public PaperEquityResponse() {
    }

    public PaperEquityResponse(List<Point> points, PaperEquitySeries.Summary summary) {
        this.points = points;
        this.summary = summary;
    }

    public List<Point> getPoints() { return points; }
    public void setPoints(List<Point> points) { this.points = points; }
    public PaperEquitySeries.Summary getSummary() { return summary; }
    public void setSummary(PaperEquitySeries.Summary summary) { this.summary = summary; }
}
