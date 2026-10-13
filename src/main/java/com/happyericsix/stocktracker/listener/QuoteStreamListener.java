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
    private final ObjectMapper mapper = new ObjectMapper();

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
            ArrayNode array = mapper.createArrayNode();
            for (RefreshedPrice price : prices) {
                ObjectNode node = array.addObject();
                node.put("symbol", price.symbol());
                node.put("price", price.currentPrice());
            }
            sseService.pushQuotes(array.toString());
        } catch (Exception e) {
            // 旁路：推送挂了只是前端少跳一次数，绝不影响刷新与结算主流程
            log.warn("quote push failed: {}", e.getMessage());
        }
    }
}
