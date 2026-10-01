package com.happyericsix.stocktracker.client;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.Duration;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * Python 资讯服务（N1 取数 / N2 理解）的客户端。
 *
 * <h3>异常范式：{@code AkshareStockClient} 式降级，而不是 {@code ThsClient} 式抛异常</h3>
 * 判据是"是否需要区分『空结果』与『失败』"（plan §0.4 约定 6）。这里需要区分，而且
 * Python 侧已经把失败**表达成了数据**：`{"ok":false,"error":"中文可读原因"}`。
 * 所以 Java 侧吞掉异常、返回**同形状的降级节点**，让 {@code NewsService} 能同时说清
 * 两件事：「库里有 12 条，只是刷新失败了」（照常返回库里的）与「彻底没结果」。
 * 抛异常会把这两种情况压成同一种 500，用户看到的都是"服务器错误"。
 *
 * <p>注意 Python 的**鉴权失败是另一种形状**：401 {@code {"detail":"unauthorized"}} /
 * 503 {@code {"detail":"internal token not configured; …"}}。这两条被单独翻译成
 * 可操作的中文原因 —— 否则运维只会看到"服务暂时不可用"，而真正的原因是
 * 两边的 {@code INTERNAL_API_TOKEN} 不一致（本地最常见的一类故障）。
 */
@Service
public class NewsClient {

    private static final Logger log = LoggerFactory.getLogger(NewsClient.class);

    /**
     * 抓全市场公告是上千行 + 4 个外部 HTTP 请求，比行情类（15s）慢得多，
     * 但比 LLM/回测（60s）快 —— 取中间值 30s。
     */
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(30);

    /** 与 Python 的 `_news_error` 同一形状：{@code ok=false} + 中文可读原因。 */
    private static final String KEY_OK = "ok";
    private static final String KEY_ERROR = "error";
    private static final String SERVICE_UNAVAILABLE = "资讯服务暂时不可用";

    private final WebClient webClient;
    private final ObjectMapper mapper;

    public NewsClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                      @Value("${internal.api-token:}") String internalToken,
                      ObjectMapper mapper) {
        WebClient.Builder builder = WebClient.builder()
                .baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8");
        // 必须判空：本地免鉴权时不能带一个空的 X-Internal-Token 头，
        // 那种头会让 Python 侧走"token 配了但不对"的分支（401），比不带头更难排查。
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);
        }
        this.webClient = builder.build();
        this.mapper = mapper;
        log.info("NewsClient initialized, base URL: {}, internal auth: {}",
                baseUrl, internalToken != null && !internalToken.isBlank());
    }

    /**
     * 聚合取数（N1 唯一对外入口）。
     *
     * @param symbol  标的（可空；空 = 关键词/宏观搜索）
     * @param keyword 关键词（可空）
     * @param types   1公告 2媒体 3研报 4舆情；**null = 让 Python 用它自己的默认源集合**
     *                （显式传 4 会让 Python 回一句"社区舆情暂无数据源"的 errors，
     *                那不是错误、只是噪音，默认不请求它就不会有）
     * @param days    时间窗口（天）
     * @param withBody 是否给最近的公告逐条补抓正文（每条约 1~2 次 HTTP）。
     *                 ⚠️ 只在"用户正在看某只股票"时开：东财公告接口只给标题，
     *                 **没有正文时 N2 对公告只能回"信息不足，不判断方向"**
     *                 （真实链路实测：10 条抽查里 6 条公告全部如此）。
     * @param onlyRelevant 是否丢掉"只是提及该股"的媒体条目（大盘综述）。
     *                 个股页开 true —— 那页上每条都该是"关于这只票"的。用户的直接反馈是：
     *                 显示了却不解释，"那 AI 设置的意义是什么"。
     */
    public JsonNode fetch(String symbol, String keyword, List<Integer> types, int days,
                          boolean withBody, boolean onlyRelevant) {
        Map<String, Object> body = new HashMap<>();
        body.put("symbol", symbol == null ? "" : symbol);
        body.put("keyword", keyword == null ? "" : keyword);
        if (types != null && !types.isEmpty()) {
            body.put("types", types);
        }
        body.put("days", days);
        // 驼峰 withBody / onlyRelevant：Python 端点读的就是这两个键
        body.put("withBody", withBody);
        body.put("onlyRelevant", onlyRelevant);
        return post("/api/v1/news/fetch", body);
    }

    /** 兼容旧调用（refresh 路径不需要相关度过滤）。 */
    public JsonNode fetch(String symbol, String keyword, List<Integer> types, int days, boolean withBody) {
        return fetch(symbol, keyword, types, days, withBody, false);
    }

    /**
     * 结构化抽取（N2）。
     *
     * @param items 原始事件结构（键名与 N1 的输出一致，spec §6 的输入侧）
     * @param mode  {@code batch}（5 条一批，便宜）或 {@code deep}
     *              （一条一次，额外出 risks/opportunities；**单次上限 20 条**）
     */
    public JsonNode analyze(List<JsonNode> items, String mode) {
        Map<String, Object> body = new HashMap<>();
        body.put("items", items == null ? List.of() : items);
        body.put("mode", (mode == null || mode.isBlank()) ? "batch" : mode);
        return post("/api/v1/news/analyze", body);
    }

    /**
     * 综合解读：把一批**已结构化**的事件读成一段"这只票现在怎么看"（一次 LLM 调用）。
     *
     * <p>与 {@link #analyze} 的分工：那个逐条分类，这个做综合。用户的原始反馈是
     * "你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法" ——
     * 逐条标签回答不了"那合起来呢"。
     *
     * @param items 已带分析字段的事件（Python 端只读结论字段，不重贴原文）
     * @param quote 最新行情（可空），用于判断"信息面与股价表现是否一致"
     */
    public JsonNode synthesize(String symbol, String name, Map<String, Object> quote,
                              List<JsonNode> items) {
        Map<String, Object> body = new HashMap<>();
        body.put("symbol", symbol == null ? "" : symbol);
        body.put("name", name == null ? "" : name);
        if (quote != null && !quote.isEmpty()) {
            body.put("quote", quote);
        }
        body.put("items", items == null ? List.of() : items);
        return post("/api/v1/news/synthesize", body);
    }

    /** 响应里是否 {@code ok=true}；降级节点、null 都是 false。 */
    public static boolean isOk(JsonNode response) {
        return response != null && response.path(KEY_OK).asBoolean(false);
    }
    /** 降级节点里的中文原因（没有时给一句通用文案）。 */
    public static String errorOf(JsonNode response) {
        if (response == null) {
            return SERVICE_UNAVAILABLE;
        }
        String error = response.path(KEY_ERROR).asText("");
        return error.isBlank() ? SERVICE_UNAVAILABLE : error;
    }

    private JsonNode post(String uri, Object body) {
        try {
            JsonNode node = webClient.post().uri(uri).bodyValue(body)
                    .retrieve().bodyToMono(JsonNode.class)
                    .timeout(REQUEST_TIMEOUT)
                    .block();
            return node == null ? degraded(SERVICE_UNAVAILABLE) : node;
        } catch (WebClientResponseException e) {
            log.warn("News service error for {}: HTTP {} {}", uri, e.getStatusCode(), e.getResponseBodyAsString());
            return degraded(reasonFor(e));
        } catch (Exception e) {
            // 超时/连接被拒都走这里。降级而不是抛出：一次取数失败不该让搜索接口整个挂掉。
            log.warn("News service exception for {}: {}", uri, e.getMessage());
            return degraded(SERVICE_UNAVAILABLE);
        }
    }

    /** 把 Python 的两种 HTTP 失败形状翻译成可操作的原因（见类注释）。 */
    private String reasonFor(WebClientResponseException e) {
        int status = e.getStatusCode().value();
        if (status == 401) {
            return "资讯服务鉴权失败（Java 的 internal.api-token 与 Python 的 INTERNAL_API_TOKEN 不一致）";
        }
        if (status == 503) {
            return "资讯服务未配置内部令牌（Python 侧 INTERNAL_API_TOKEN 为空）";
        }
        return SERVICE_UNAVAILABLE;
    }

    private JsonNode degraded(String reason) {
        return mapper.createObjectNode().put(KEY_OK, false).put(KEY_ERROR, reason);
    }
}
