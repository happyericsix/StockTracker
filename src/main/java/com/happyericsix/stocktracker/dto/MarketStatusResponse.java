package com.happyericsix.stocktracker.dto;

import java.io.Serializable;

/**
 * 市场状态：今天开不开市、休到哪天、这个价是哪一天的。
 *
 * <p><b>为什么要有这一层</b>：休市日腾讯行情照样返回上一交易日的收盘价，
 * 前端只看行情自身<b>没有任何办法</b>分辨"交易中的实时价"与"周五的收盘价"。
 * 交易日历是唯一的外部事实来源。
 *
 * <p>字段全部为字符串/布尔/整数，与 Python 侧 {@code trading_calendar.status()}
 * 的键名一一对应（camelCase，Jackson 直接按 record 组件名绑定）。
 * 刻意不引入 {@code LocalDate}：这里是**展示口径**的契约，来源是 Python 的字符串，
 * 多一层类型转换只会多一处"日期解析失败就整块降级"的风险；
 * 需要做日期运算的地方在前端（它本来就要按用户时区显示）。
 *
 * <p>{@code note} 是 Python 已经拼好的中文句子，前端直接渲染即可 ——
 * 让两侧各拼一半文案，是"同一件事两种说法"的开始。
 *
 * @param date                 今天（Asia/Shanghai），yyyy-MM-dd
 * @param weekday              今天星期几（中文：周一…周日）
 * @param known                交易日历是否覆盖今天。为 false 时下面的判断按"工作日近似"，**必须在界面上说清**
 * @param calendarCovered      日历覆盖范围是否包含今天（与 known 同源，便于前端单独引用）
 * @param calendarFrom         日历覆盖起点
 * @param calendarTo           日历覆盖终点
 * @param calendarSource       日历来源：akshare / cache / none —— 用来解释"为什么说不知道"
 * @param tradingDay           今天是否交易日
 * @param phase                盘中阶段：pre_open / auction / morning / noon_break / afternoon / post_close / closed
 * @param phaseLabel           阶段的中文说法
 * @param quoteDate            行情里的"最新价"属于哪一个交易日
 * @param previousTradingDay   严格早于今天的最近一个交易日
 * @param nextTradingDay       严格晚于今天的最近一个交易日
 * @param restDays             包含今天在内的本轮连续休市天数（今天开市时为 0）
 * @param restFrom             本轮休市区间的第一天
 * @param restTo               本轮休市区间的最后一天
 * @param note                 直接给用户看的一句话
 */
public record MarketStatusResponse(
        String date,
        String weekday,
        Boolean known,
        Boolean calendarCovered,
        String calendarFrom,
        String calendarTo,
        String calendarSource,
        Boolean tradingDay,
        String phase,
        String phaseLabel,
        String quoteDate,
        String previousTradingDay,
        String nextTradingDay,
        Integer restDays,
        String restFrom,
        String restTo,
        String note) implements Serializable {
    private static final long serialVersionUID = 1L;
}
