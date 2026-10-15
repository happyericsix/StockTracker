package com.happyericsix.stocktracker.listener;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.SseEmitterService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.util.List;

/**
 * 行情快车道的一环：每次价格刷新 → 把最新价推给所有连着行情通道的前端。
 *
 * <h3>为什么是同步监听</h3>
 * 序列化一小段 JSON + 给个位数的本地连接 send，都是内存操作（毫秒级），
 * 没有必要进 settlementExecutor 与模拟盘结算排队 —— 那反而会把"推送"
 * 拖到结算（最长 60s 的 Python 调用）后面，快车道就不快了。
 *
 * <h3>推送的形状刻意最小</h3>
 * 只推 {@code [{symbol, price}]}：涨跌幅由前端用自己手里的昨收算
 * （自选卡上本来就有 previousClose），不必在链路上多带一份会漂移的衍生值。
 */
@Component
public class QuoteStreamListener {

    private static final Logger log = LoggerFactory.getLogger(QuoteStreamListener.class);

    private final SseEmitterService sseService;

    public QuoteStreamListener(SseEmitterService sseService) {
        this.sseService = sseService;
    }

    @EventListener
    public void onPricesRefreshed(PricesRefreshedEvent event) {
        try {
            List<RefreshedPrice> prices = event.prices();
            if (prices == null || prices.isEmpty()) {
                return;
            }
            sseService.pushQuotes(toJson(prices));
        } catch (Exception e) {
            // 旁路：推送挂了只是前端少跳一次数，绝不影响刷新与结算主流程
            log.warn("quote push failed: {}", e.getMessage());
        }
    }

    /**
     * 推送形状的唯一权威定义（{@code [{symbol, price}]}）。
     * 焦点快车道（FocusQuoteStreamJob，3 秒一发）与这里共用 —— 改协议必须两处一起变，
     * 收敛成一个方法就是为了让人找不到"只改一处"的机会。
     */
    public static String toJson(List<RefreshedPrice> prices) throws Exception {
        ArrayNode array = MAPPER.createArrayNode();
        for (RefreshedPrice price : prices) {
            ObjectNode node = array.addObject();
            node.put("symbol", price.symbol());
            node.put("price", price.currentPrice());
        }
        return array.toString();
    }

    private static final ObjectMapper MAPPER = new ObjectMapper();
}
