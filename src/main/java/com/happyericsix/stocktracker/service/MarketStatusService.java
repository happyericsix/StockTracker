package com.happyericsix.stocktracker.service;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.happyericsix.stocktracker.client.AkshareStockClient;
import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.time.LocalDate;
import java.time.ZoneId;

/**
 * 市场状态（今天开不开市、休到哪天、这个价是哪一天的）。
 *
 * <h3>它解决的是什么</h3>
 * 用户的原话：*"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"*。
 * 休市日腾讯行情返回的仍然是上一交易日的收盘价与涨跌幅，前端**没有任何本地信息**
 * 能分辨这一点，所以必须有一个外部事实来源。这个类就是那个来源的入口。
 *
 * <h3>为什么自己缓存，而不是 {@code @Cacheable}</h3>
 * <ul>
 *   <li>缓存的键只有一个（"现在"），不是一个集合 —— 走 Spring Cache 只是多一层抽象；</li>
 *   <li>TTL 只有 60 秒（见下），而 {@code RedisCacheConfig} 里各具名缓存的最小 TTL 是 30 秒、
 *       默认 30 秒，为一个"随分钟变化"的值专门加一个具名缓存配置不划算；</li>
 *   <li>Redis 未启用时（{@code spring.cache.type} 不是 redis）这段逻辑照常工作，
 *       而交易日历是**行情与资讯两条链路都要用**的东西，不该依赖可选组件。</li>
 * </ul>
 *
 * <h3>TTL 为什么是 60 秒</h3>
 * 这个值里带**盘中阶段**（未开盘 / 交易中 / 午间休市 / 已收盘）。TTL 太长会让
 * "已收盘"在收盘后一直挂着、或"交易中"在 15:00 之后还显示 —— 那正是本功能要消灭的
 * 那类"界面在说谎"。60 秒内的偏差用户感知不到，而 Python 侧的日历缓存是 12 小时，
 * 所以这 60 秒一次的成本只是**一次本机 HTTP 往返**。
 */
@Service
public class MarketStatusService {

    private static final Logger log = LoggerFactory.getLogger(MarketStatusService.class);

    /** 缓存键：只有"当前"这一个状态，不需要按标的区分。 */
    private static final String CACHE_KEY = "current";

    private static final Duration CACHE_TTL = Duration.ofSeconds(60);

    /**
     * 与 Python 侧一致固定 UTC+8（中国无夏令时）。
     * 用 JVM 默认时区的话，容器里配成 UTC 就会在每天 08:00–16:00 之外给出错误的"今天"
     * —— 而那恰好是 A 股的全部交易时间。
     */
    private static final ZoneId CN_ZONE = ZoneId.of("Asia/Shanghai");

    private final AkshareStockClient client;

    private final Cache<String, MarketStatusResponse> cache = Caffeine.newBuilder()
            .expireAfterWrite(CACHE_TTL)
            .maximumSize(4)
            .build();

    public MarketStatusService(AkshareStockClient client) {
        this.client = client;
    }

    /**
     * 当前市场状态。**永不返回 null、永不抛异常** —— 调用方（行情卡、个股页）
     * 只是想在界面上说明白"这是哪天的数据"，不该因为这个功能拿不到而整页失败。
     *
     * <p>Python 侧拿不到时返回一条 {@link #unknown()} 状态：它明确写着
     * `known=false` 与"交易日历不可用"，于是前端**不会**退回"把休市日的收盘价
     * 当成今天"的老行为，而是照实说不知道。
     */
    public MarketStatusResponse current() {
        MarketStatusResponse cached = cache.getIfPresent(CACHE_KEY);
        if (cached != null) {
            return cached;
        }
        MarketStatusResponse fetched = null;
        try {
            fetched = client.getMarketStatus();
        } catch (Exception e) {
            // client 内部已经吞掉了所有异常并返回 null，这里只是最后一道保险：
            // 这个方法是页面渲染路径上的附加信息，它出问题不该让行情消失。
            log.warn("获取市场状态失败（按未知处理）：{}", e.toString());
        }
        MarketStatusResponse result = fetched != null ? fetched : unknown();
        cache.put(CACHE_KEY, result);
        return result;
    }

    /**
     * 日历不可用时的兜底状态。
     *
     * <p>刻意**不猜**"今天是交易日"：那是把一个不确定的事说成确定的。
     * 只有"今天几号、周几"是确定的（Java 自己能算），其余全部留空，
     * 并让 {@code note} 说明原因。
     */
    private MarketStatusResponse unknown() {
        LocalDate today = LocalDate.now(CN_ZONE);
        String[] weekdays = {"周一", "周二", "周三", "周四", "周五", "周六", "周日"};
        return new MarketStatusResponse(
                today.toString(),
                weekdays[today.getDayOfWeek().getValue() - 1],
                false,
                false,
                "",
                "",
                "none",
                // tradingDay 给 null（不是 false）：false 是"确认休市"，
                // 而这里的真相是"不知道"。前端据此只显示"暂时无法确认"。
                null,
                "unknown",
                "状态未知",
                "",
                "",
                "",
                0,
                "",
                "",
                "交易日历暂时不可用，无法确认今天是否开市（行情可能仍是上一交易日的收盘价）");
    }
}
