package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.SentimentClient;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.MarketSentimentSnapshot;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.MarketSentimentSnapshotRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.MathContext;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

/**
 * 散户情绪的业务编排：代理 Python 聚合接口 + **用快照历史补上参与意愿的对照验证**。
 *
 * <h3>为什么验证拆在两边</h3>
 * 关注指数/评分自带 30 天序列 → Python 当场就能对照（在 sentiment_client 里）；
 * 参与意愿只回 5 天 → 长序列在 {@code market_sentiment_snapshots} 里，
 * 它的验证只能由 Java 拿"快照序列 × 日线收盘"在响应时现算。
 * 两边共用同一套判定语义（秩分桶 + σ 感知门槛），{@link #validateDesire}
 * 与 Python {@code validate_against_price} 是**镜像实现**——改阈值要两边同步，
 * 这是数据在两边各持一半的代价，注释在这里钉住。
 */
@Service
public class MarketSentimentService {

    private static final Logger log = LoggerFactory.getLogger(MarketSentimentService.class);

    /** 与 Python sentiment_client 的门槛一致（改要两边同步）。 */
    static final int MIN_VALIDATION_SAMPLES = 20;
    static final int MIN_BUCKET_SAMPLES = 8;
    static final double MIN_UP_RATIO_DIFF = 0.10;
    static final double Z_SCREEN = 1.96;

    /** 快照任务的标的数上限：自选+策略的并集，防极端情况下任务跑成全市场扫描。 */
    static final int MAX_SNAPSHOT_SYMBOLS = 60;

    private final SentimentClient sentimentClient;
    private final MarketSentimentSnapshotRepository snapshotRepository;
    private final FavoriteStockRepository favoriteStockRepository;
    private final StrategyRepository strategyRepository;
    private final StockDataGateway stockDataGateway;

    public MarketSentimentService(SentimentClient sentimentClient,
                                  MarketSentimentSnapshotRepository snapshotRepository,
                                  FavoriteStockRepository favoriteStockRepository,
                                  StrategyRepository strategyRepository,
                                  StockDataGateway stockDataGateway) {
        this.sentimentClient = sentimentClient;
        this.snapshotRepository = snapshotRepository;
        this.favoriteStockRepository = favoriteStockRepository;
        this.strategyRepository = strategyRepository;
        this.stockDataGateway = stockDataGateway;
    }

    /**
     * 个股情绪聚合：Python 的三指数 + 验证结论，其中**参与意愿的验证**
     * 用本地快照历史重算（Python 只知道最近 5 天，算不了）。
     * Python 不可达时返回 null——情绪是旁路，绝不拖垮页面。
     */
    public JsonNode getSentiment(String symbol) {
        JsonNode data = sentimentClient.getSentiment(symbol);
        if (data == null || !data.path("ok").asBoolean(false)) {
            return data;
        }
        JsonNode payload = data.path("data");
        if (!payload.isObject() || !(payload instanceof ObjectNode editable)) {
            return data;
        }
        try {
            ObjectNode validation = editable.withObject("validation");
            validation.set("desire", mapper().valueToTree(
                    desireValidation(symbol)));
        } catch (Exception e) {
            log.warn("参与意愿快照验证失败（保留 Python 结论）{}: {}", symbol, e.getMessage());
        }
        return data;
    }

    /** Python 侧 5 天窗口算不了的参与意愿验证，用本地快照 × 日线现算。 */
    public Map<String, Object> desireValidation(String symbol) {
        List<MarketSentimentSnapshot> snapshots =
                snapshotRepository.findBySymbolAndTradeDateGreaterThanEqualOrderByTradeDateAsc(
                        symbol, CnTime.today().minusDays(400));
        if (snapshots.size() < MIN_VALIDATION_SAMPLES) {
            return insufficient(snapshots.size(),
                    "参与意愿历史由每日快照累积，累积满 " + MIN_VALIDATION_SAMPLES
                            + " 个交易日后自动验证");
        }
        try {
            StockHistoryResponse history = stockDataGateway.getStockHistory(symbol, "daily");
            Map<String, BigDecimal> closes = new TreeMap<>();
            if (history != null && history.timeSeries() != null) {
                history.timeSeries().forEach((date, price) -> {
                    if (date != null && price != null) {
                        try {
                            BigDecimal close = new BigDecimal(price.close().trim());
                            if (close.signum() > 0) {
                                closes.put(date, close);
                            }
                        } catch (NumberFormatException ignored) {
                            // 脏行跳过：一根坏 K 线不该废掉整个验证
                        }
                    }
                });
            }
            List<String> orderedDates = new ArrayList<>(closes.keySet());
            List<boolean[]> pairs = new ArrayList<>();  // {desire 高位排序键在别处, up}
            List<double[]> raw = new ArrayList<>();     // [desire, upFlag]
            for (MarketSentimentSnapshot snapshot : snapshots) {
                if (snapshot.getDesire() == null) {
                    continue;
                }
                int idx = orderedDates.indexOf(snapshot.getTradeDate().toString());
                if (idx < 0 || idx + 1 >= orderedDates.size()) {
                    continue;
                }
                BigDecimal close = closes.get(orderedDates.get(idx));
                BigDecimal next = closes.get(orderedDates.get(idx + 1));
                if (close != null) {
                    raw.add(new double[]{snapshot.getDesire(), next.compareTo(close) > 0 ? 1 : 0});
                }
            }
            return verdict(raw);
        } catch (Exception e) {
            log.warn("参与意愿验证取日线失败 {}: {}", symbol, e.getMessage());
            return insufficient(0, "日线暂不可用，暂时无法验证");
        }
    }

    /** 秩分桶 + σ 感知门槛（与 Python validate_against_price 镜像）。 */
    private Map<String, Object> verdict(List<double[]> raw) {
        if (raw.size() < MIN_VALIDATION_SAMPLES) {
            return insufficient(raw.size(), "可配对样本不足");
        }
        raw.sort((a, b) -> Double.compare(a[0], b[0]));
        int mid = raw.size() / 2;
        List<double[]> low = raw.subList(0, mid);
        List<double[]> high = raw.subList(mid, raw.size());
        if (low.size() < MIN_BUCKET_SAMPLES || high.size() < MIN_BUCKET_SAMPLES) {
            return insufficient(raw.size(), "分桶后样本过少");
        }
        double highUp = low(high);
        double lowUp = low(low);
        double diff = highUp - lowUp;
        double noise = Math.sqrt(0.5 * (1.0 / high.size() + 1.0 / low.size()));
        double threshold = Math.max(MIN_UP_RATIO_DIFF, Z_SCREEN * noise);

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("samples", raw.size());
        result.put("high", bucket(high.size(), highUp));
        result.put("low", bucket(low.size(), lowUp));
        result.put("up_ratio_diff", BigDecimal.valueOf(diff).setScale(3, RoundingMode.HALF_UP));
        if (Math.abs(diff) >= threshold) {
            String side = diff > 0 ? "高" : "低";
            result.put("verdict", "初步显示有信息量：参与意愿" + side
                    + "组的次日上涨比例高出 " + Math.round(Math.abs(diff) * 100) + " 个百分点"
                    + "（超过小样本噪声门槛 " + Math.round(threshold * 100) + "pt；"
                    + "近似筛查，非严格检验，不构成买卖依据）");
        } else {
            result.put("verdict", "未见显著关系（该信号在本票上暂时没有预测力）");
        }
        return result;
    }

    private static double low(List<double[]> rows) {
        return rows.stream().mapToDouble(r -> r[1]).average().orElse(0);
    }

    private static Map<String, Object> bucket(int n, double upRatio) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("n", n);
        map.put("up_ratio", BigDecimal.valueOf(upRatio).setScale(3, RoundingMode.HALF_UP));
        return map;
    }

    private static Map<String, Object> insufficient(int samples, String note) {
        Map<String, Object> map = new LinkedHashMap<>();
        map.put("samples", samples);
        map.put("verdict", "样本不足");
        map.put("note", note);
        return map;
    }

    /**
     * 每日快照：自选 ∪ 策略标的，逐个拉最新情绪并按**上游给的最后交易日**落一行。
     * 用上游的交易日而不是"今天"做幂等键——休市日跑任务会把周五的数据
     * 盖成周六的日期，序列就错位了。
     */
    public int snapshotAll() {
        List<String> symbols = new ArrayList<>();
        favoriteStockRepository.findAll().forEach(f -> symbols.add(f.getStockSymbol()));
        strategyRepository.findAll().forEach(s -> {
            if (s.getSymbol() != null && !s.getSymbol().isBlank()) {
                symbols.add(s.getSymbol());
            }
        });
        List<String> unique = symbols.stream().distinct()
                .filter(s -> s != null && !s.isBlank())
                .limit(MAX_SNAPSHOT_SYMBOLS).toList();
        if (unique.isEmpty()) {
            return 0;
        }
        int saved = 0;
        for (String symbol : unique) {
            try {
                if (snapshotOne(symbol)) {
                    saved++;
                }
            } catch (Exception e) {
                log.warn("情绪快照失败 {}: {}", symbol, e.getMessage());
            }
        }
        log.info("情绪快照完成：{}/{} 个标的", saved, unique.size());
        return saved;
    }

    private boolean snapshotOne(String symbol) {
        JsonNode data = sentimentClient.getSentiment(symbol);
        if (data == null || !data.path("ok").asBoolean(false)) {
            return false;
        }
        JsonNode payload = data.path("data");
        LocalDate tradeDate = latestSeriesDate(payload);
        if (tradeDate == null) {
            return false;
        }
        MarketSentimentSnapshot snapshot = snapshotRepository
                .findBySymbolAndTradeDate(symbol, tradeDate)
                .orElseGet(() -> MarketSentimentSnapshot.builder()
                        .symbol(symbol).tradeDate(tradeDate).build());
        JsonNode desire = payload.path("desire");
        if (desire.path("latest").isNumber()) {
            snapshot.setDesire(desire.path("latest").asDouble());
        }
        if (desire.path("avg5").isNumber()) {
            snapshot.setDesireAvg5(desire.path("avg5").asDouble());
        }
        if (payload.path("focus").path("latest").isNumber()) {
            snapshot.setFocus(payload.path("focus").path("latest").asDouble());
        }
        if (payload.path("score").path("latest").isNumber()) {
            snapshot.setScore(payload.path("score").path("latest").asDouble());
        }
        snapshotRepository.save(snapshot);
        return true;
    }

    /** 三条序列里最新的日期 = 上游认定的最后交易日（快照幂等键用它）。 */
    private static LocalDate latestSeriesDate(JsonNode payload) {
        String latest = "";
        for (String key : List.of("desire", "focus", "score")) {
            JsonNode series = payload.path(key).path("series");
            if (series.isArray() && series.size() > 0) {
                String date = series.get(series.size() - 1).path("date").asText("");
                if (date.compareTo(latest) > 0) {
                    latest = date;
                }
            }
        }
        try {
            return latest.isEmpty() ? null : LocalDate.parse(latest);
        } catch (Exception e) {
            return null;
        }
    }

    private static tools.jackson.databind.ObjectMapper mapper() {
        return new tools.jackson.databind.ObjectMapper();
    }
}
