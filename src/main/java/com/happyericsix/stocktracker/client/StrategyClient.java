package com.happyericsix.stocktracker.client;

import com.happyericsix.stocktracker.service.ExecutionContract;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.Duration;
import java.util.HashMap;

@Service
public class StrategyClient {
    private static final Logger log = LoggerFactory.getLogger(StrategyClient.class);

    private final WebClient webClient;
    private final ObjectMapper mapper;

    public StrategyClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                          @Value("${internal.api-token:}") String internalToken,
                          ObjectMapper mapper) {
        WebClient.Builder builder = WebClient.builder().baseUrl(baseUrl);
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
        this.mapper = mapper;
    }

    public JsonNode validateStrategy(String configJson) {
        return post("/api/v1/strategies/validate", java.util.Map.of("strategy_json", raw(configJson)));
    }

    public JsonNode backtestStrategy(String configJson) {
        return post("/api/v1/strategies/backtest", java.util.Map.of("strategy_json", raw(configJson)));
    }

    /**
     * 多标的 × 多时段的样本外验证 + 成本归因（确定性，不调模型）。
     *
     * <p>为什么 Java 也要有这条路：单个标的的单段回测只能说明"这段行情里数字是这样"，
     * 而"这条规则到底行不行"必须换票换段才答得出来。盘后复盘报告要用同一份结果，
     * 所以口径（复权、分段、区间）全部由端点回传，Java 不自己组装。
     *
     * @param configJson 策略 JSON
     * @param symbols    要验证的标的（空则用策略自带的 symbol）
     * @param segments   切成几段（端点会按预热根数自动减少段数并说明）
     * @param startDate  起点 YYYY-MM-DD，可空
     * @param endDate    终点 YYYY-MM-DD，可空
     */
    public JsonNode backtestMatrix(String configJson, java.util.List<String> symbols, int segments,
                                   String startDate, String endDate) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        if (symbols != null && !symbols.isEmpty()) body.put("symbols", symbols);
        body.put("segments", segments);
        if (startDate != null && !startDate.isBlank()) body.put("start_date", startDate);
        if (endDate != null && !endDate.isBlank()) body.put("end_date", endDate);
        return post("/api/v1/strategies/backtest-matrix", body);
    }

    public JsonNode getModelDiagnostic(String symbol) {
        return get("/api/v1/agent/diagnostic/" + symbol);
    }

    public JsonNode evaluateBar(String configJson, String symbol, String date, JsonNode position) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("date", date);
        // 声明结算类型：它决定成交价口径（daily → close），而口径必须随结果一起落库。
        body.put(ExecutionContract.SETTLEMENT_KIND_FIELD, ExecutionContract.SETTLEMENT_DAILY);
        if (position != null) body.put("position", position);
        return post("/api/v1/strategies/evaluate-bar", body);
    }

    public JsonNode evaluateBarRealtime(String configJson, String symbol, JsonNode position) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("period", "5");
        // 实时结算用盘中最新价成交（realtime_last）：它与回测口径**不同**，
        // 所以更要显式声明，否则两条路径的记录会混在一起且无法区分。
        body.put(ExecutionContract.SETTLEMENT_KIND_FIELD, ExecutionContract.SETTLEMENT_REALTIME);
        if (position != null) body.put("position", position);
        return post("/api/v1/strategies/evaluate-bar", body);
    }

    /**
     * **agent 决策**：多角色委员会辩论后给出决策（响应形状与 {@code evaluate-bar} 一致）。
     *
     * <p>为什么复用同一个响应形状：结算侧只需要换一个调用地址，**执行路径一行不改** ——
     * 整手取整、5 元佣金下限、印花税、T+1、涨跌停挡单、DECIMAL 记账全部照旧。
     * 模型决定**要不要动**，代码决定**怎么动**。
     *
     * <p>agent 走日线结算：委员会是每日一次的节奏（一次约 8 次 LLM 调用），
     * 不像实时路径那样每 5 分钟触发。**但并不是每天都真的开会** ——
     * `lastDecisionAt` 交给 Python 侧的召集门控判断"是否有值得开会的理由"，
     * 没有就零成本地跳过（记 `agent_no_new_information`，与"模型决定不动"分开）。
     *
     * @param lastDecisionAt 上次**真的召集了委员会**的日子（YYYY-MM-DD），可为 null
     *                       （null = 从没开过会 → 门控会按"该开会"处理）
     */
    public JsonNode agentDecide(String configJson, String symbol, String date, JsonNode position,
                                String lastDecisionAt) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("date", date);
        body.put(ExecutionContract.SETTLEMENT_KIND_FIELD, ExecutionContract.SETTLEMENT_DAILY);
        if (lastDecisionAt != null && !lastDecisionAt.isBlank()) {
            body.put("last_decision_at", lastDecisionAt);
        }
        if (position != null) body.put("position", position);
        return post("/api/v1/agent/decide", body);
    }

    private JsonNode raw(String json) {
        try { return mapper.readTree(json); }
        catch (Exception e) { return mapper.createObjectNode(); }
    }

    private JsonNode post(String uri, Object body) {
        try {
            return webClient.post().uri(uri).bodyValue(body)
                    .retrieve().bodyToMono(JsonNode.class)
                    .timeout(Duration.ofSeconds(60))
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("Strategy service error for {}: HTTP {} {}", uri, e.getStatusCode(), e.getResponseBodyAsString());
            throw e;
        }
    }

    private JsonNode get(String uri) {
        try {
            return webClient.get().uri(uri)
                    .retrieve().bodyToMono(JsonNode.class)
                    .timeout(Duration.ofSeconds(60))
                    .block();
        } catch (WebClientResponseException e) {
            log.warn("Strategy service error for {}: HTTP {} {}", uri, e.getStatusCode(), e.getResponseBodyAsString());
            throw e;
        }
    }
}
