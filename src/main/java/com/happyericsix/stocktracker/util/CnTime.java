package com.happyericsix.stocktracker.util;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * A 股业务时间的唯一口径：东八区（Asia/Shanghai）。
 *
 * <h3>为什么必须显式传 ZoneId</h3>
 * 交易日、冷却窗口、同花顺凭证过期这些判定都要跟"北京的日历"对齐；
 * 而 {@code LocalDate.now()} 走 JVM 默认时区 —— 容器默认是 UTC 时，
 * 北京时间 0:00~8:00 之间"今天"会取成昨天（休市判据、模拟盘 tradeDate、
 * "库里最新一条是否早于今天"全部漂移 8 小时）。
 *
 * <p>应用启动时会把 JVM 默认时区也钉成 Asia/Shanghai
 * （见 StocktrackerApplication.main），这里的显式常量是不依赖
 * 启动方式的第二道保险 —— 新代码一律用它，不要再写裸的 now()。
 */
public final class CnTime {

    public static final ZoneId ZONE = ZoneId.of("Asia/Shanghai");

    private CnTime() {
    }

    /** 北京的"今天"。 */
    public static LocalDate today() {
        return LocalDate.now(ZONE);
    }

    /** 北京的"现在"。 */
    public static LocalDateTime now() {
        return LocalDateTime.now(ZONE);
    }
}
