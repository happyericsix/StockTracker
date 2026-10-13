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

    public JsonNode backtestStrategy(String configJson) {
        return post("/api/v1/strategies/backtest", java.util.Map.of("strategy_json", raw(configJson)));
    }

    /**
     * 轻量 DSL 校验（不取数、不回测）：策略编辑保存前调用，把改坏的规则挡在落库之前。
     * 返回 {"valid":true} 或 {"valid":false,"error":"..."}；校验服务本身不可用会抛异常，
     * 由调用方决定 fail-open/fail-closed（编辑路径是 fail-closed：校验不了就不保存）。
     */
    public JsonNode validateConfig(String configJson) {
        return post("/api/v1/strategies/validate-config", java.util.Map.of("strategy_json", raw(configJson)));
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
        // 1 分钟线（2026-10-08 快车道，原 5 分钟）：止损/信号的盘中响应从"最多迟 5 分钟"
        // 收紧到"最多迟 1 分钟 + 45s 节流"。barTime 去重保证同一根 bar 不会重复结算。
        body.put("period", "1");
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
        return agentDecide(configJson, symbol, date, position, lastDecisionAt, null);
    }

    /**
     * 带新闻证据包的委员会决策（agent 互通 P3）：newsBriefing 由 Java 从库里
     * 组装（NewsBriefingService.committeeBriefing，只读无 LLM），Python 侧塞进
     * evidence 给 8 个角色共用。null / 空包不带这个键 —— 委员会照旧只看行情，
     * 信息面缺失不是错误（fail-open）。
     */
    public JsonNode agentDecide(String configJson, String symbol, String date, JsonNode position,
                                String lastDecisionAt, JsonNode newsBriefing) {
        return agentDecide(configJson, symbol, date, position, lastDecisionAt, newsBriefing, null);
    }

    /**
     * 带经验包的委员会决策（agent 互通 P4，经验回流）：experience 是这条策略
     * **自己过去的成交成绩单**（DecisionQualityService.strategyExperience，只读无 LLM）——
     * 委员会开会前先读自己的病历，5 日对账发现过的错误模式才能进下一次决策。
     * null / 空包不带这个键（fail-open，与 news_briefing 同一条纪律）。
     */
    public JsonNode agentDecide(String configJson, String symbol, String date, JsonNode position,
                                String lastDecisionAt, JsonNode newsBriefing, JsonNode experience) {
        return agentDecide(configJson, symbol, date, position, lastDecisionAt,
                newsBriefing, experience, null);
    }

    /**
     * 全量证据版（P5，组合上下文，参考 TradingAgents 的 PortfolioContext）：
     * portfolio 是本策略现金/持仓 + 该用户全部在跑策略的总敞口 —— 委员会评估
     * 集中度与仓位时以此为准。三个证据包都可为 null，null/空不带键（fail-open）。
     * 三个可选包都从这一版走：签名组合的增长到此为止，调用方统一用全量签名。
     */
    public JsonNode agentDecide(String configJson, String symbol, String date, JsonNode position,
                                String lastDecisionAt, JsonNode newsBriefing, JsonNode experience,
                                JsonNode portfolio) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("date", date);
        body.put(ExecutionContract.SETTLEMENT_KIND_FIELD, ExecutionContract.SETTLEMENT_DAILY);
        if (lastDecisionAt != null && !lastDecisionAt.isBlank()) {
            body.put("last_decision_at", lastDecisionAt);
        }
        if (position != null) body.put("position", position);
        if (newsBriefing != null && !newsBriefing.isEmpty()) body.put("news_briefing", newsBriefing);
        if (experience != null && !experience.isEmpty()) body.put("experience", experience);
        if (portfolio != null && !portfolio.isEmpty()) body.put("portfolio", portfolio);
        // 委员会是 8 次**串行** LLM 调用（每个角色一次），60s 的通用超时会在半途掐断：
        // 异常上抛后当天既无痕迹也无净值快照，"为什么今天没结算"只存在于日志。
        // 这里单独放宽到 240s（8 次 × 每次 15~30s 的量级），其余端点维持 60s。
        return post("/api/v1/agent/decide", body, Duration.ofSeconds(240));
    }

    /**
     * **盘中盯盘交易员**：量化事件触发的轻量判断（1 次 LLM 调用，秒级）。
     *
     * <p>与 {@link #agentDecide}（全委会，8 角色串行、240s）的分工：全委会只在日线结算
     * 开会划授权边界；盯盘交易员在盘中的事件（急涨急跌/量能异常/穿越均线/逼近止损线/
     * 规则信号）发生时被叫起来，在授权边界内给出 hold/buy/sell/partial_sell/flag。
     *
     * <p>超时 15s：盯盘的价值就在"几秒内给判断"，等不起 —— 超时按 degraded 处理，
     * 结算侧回落"规则+授权"路径，盘中不因模型慢而瘫痪。
     *
     * @param events        事件列表（evaluate-bar realtime 响应里带回来的，原样透传）
     * @param position      当前持仓（可空）
     * @param authorization 委员会授权边界（可空：模型按最保守处理）
     */
    public JsonNode intradayWatch(String configJson, String symbol, JsonNode events,
                                  JsonNode position, JsonNode authorization) {
        return intradayWatch(configJson, symbol, events, position, authorization, null);
    }

    /**
     * 带 news_alert 的盯盘（agent 互通 P3）：newsShock 是 Java 检测到的
     * "该标的近 24h 出现 high impact 且已判方向的新闻"，作为 news_shock 事件
     * 触发本次盯盘并注入决策上下文。null = 无新闻冲击（原行为）。
     */
    public JsonNode intradayWatch(String configJson, String symbol, JsonNode events,
                                  JsonNode position, JsonNode authorization, JsonNode newsAlert) {
        return intradayWatch(configJson, symbol, events, position, authorization, newsAlert, null);
    }

    /**
     * 带经验包的盯盘（agent 互通 P4，经验回流）：experience 是这条策略自己的
     * 成交成绩单（轻量版），盯盘交易员判断"这次事件值不值得动手"前先看自己的
     * 过往战绩。null = 不带（fail-open）。
     */
    public JsonNode intradayWatch(String configJson, String symbol, JsonNode events,
                                  JsonNode position, JsonNode authorization, JsonNode newsAlert,
                                  JsonNode experience) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("events", events);
        if (position != null) body.put("position", position);
        if (authorization != null) body.put("authorization", authorization);
        if (newsAlert != null) body.put("news_alert", newsAlert);
        if (experience != null && !experience.isEmpty()) body.put("experience", experience);
        return post("/api/v1/agent/intraday-decide", body, Duration.ofSeconds(15));
    }

    /**
     * 周反思（P2，经验回流的定性半环，参考 TradingAgents 的 memory log 循环）：
     * 把本周战绩/成交/委员会过程整体发给 Python 做一次 LLM 反思，产出 ≤3 条结构化教训。
     * 60s 超时（1 次调用）；失败/空教训由调用方按无操作处理（反思是旁路，不影响复盘主链路）。
     */
    public JsonNode weeklyReflection(JsonNode payload) {
        return post("/api/v1/agent/weekly-reflection", payload, Duration.ofSeconds(60));
    }

    private JsonNode raw(String json) {        try { return mapper.readTree(json); }
        catch (Exception e) { return mapper.createObjectNode(); }
    }

    private JsonNode post(String uri, Object body) {
        return post(uri, body, Duration.ofSeconds(60));
    }

    private JsonNode post(String uri, Object body, java.time.Duration timeout) {
        try {
            return webClient.post().uri(uri).bodyValue(body)
                    .retrieve().bodyToMono(JsonNode.class)
                    .timeout(timeout)
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
