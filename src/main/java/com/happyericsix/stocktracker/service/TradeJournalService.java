package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.dto.TradeJournalResponse;
import com.happyericsix.stocktracker.dto.TradeJournalResponse.Entry;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/**
 * 决策日志聚合：痕迹表（当时说了什么）× 日线收盘（后来发生了什么）→ 对答案。
 *
 * <p><b>为什么值得单独一个 Service</b>：口径是这里最难的部分，不是查询。
 * 三条纪律：
 * <ol>
 *   <li><b>动作优先</b>：同一天既有盘中成交又有日频 skip（already_traded_today）
 *       时按<b>成交</b>那条判定——对答案要对在动作上，"选择不动"只作陪衬；</li>
 *   <li><b>算不出就不判</b>：没有次日收盘价 → pending；行情源整个挂了 →
 *       no_price，绝不把"没数据"算成 miss（那会把系统性风险记成模型失准）；</li>
 *   <li><b>行情是旁路</b>：取日线失败时日志照出、页面照开（只剩 skip 行），
 *       绝不让行情源抖动把日志接口打成 500。</li>
 * </ol>
 */
@Service
public class TradeJournalService {

    private static final Logger log = LoggerFactory.getLogger(TradeJournalService.class);

    /** 最多回看的痕迹条数：防长跑策略把一次请求拖成全表扫描。 */
    private static final int MAX_TRACES = 500;

    private final PaperTradeTraceRepository traceRepository;
    private final StrategyRepository strategyRepository;
    private final UserRepository userRepository;
    private final StockDataGateway stockDataGateway;

    public TradeJournalService(PaperTradeTraceRepository traceRepository,
                               StrategyRepository strategyRepository,
                               UserRepository userRepository,
                               StockDataGateway stockDataGateway) {
        this.traceRepository = traceRepository;
        this.strategyRepository = strategyRepository;
        this.userRepository = userRepository;
        this.stockDataGateway = stockDataGateway;
    }

    public TradeJournalResponse journal(String username, Long strategyId, int days) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
        Strategy strategy = strategyRepository.findByIdAndUserId(strategyId, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        int window = Math.max(1, Math.min(days, 365));
        LocalDate since = CnTime.today().minusDays(window);

        List<PaperTradeTrace> traces = traceRepository.findByStrategyIdOrderByCreatedAtDesc(
                strategy.getId(), PageRequest.of(0, MAX_TRACES));

        // 每个交易日一行；动作（buy/sell）优先于 skip（见类注释纪律 1）
        Map<LocalDate, PaperTradeTrace> byDay = new HashMap<>();
        for (PaperTradeTrace trace : traces) {
            if (trace.getTradeDate() == null || trace.getTradeDate().isBefore(since)) {
                continue;
            }
            PaperTradeTrace existing = byDay.get(trace.getTradeDate());
            boolean existingIsAction = existing != null && isAction(existing.getDecision());
            if (existing == null || (!existingIsAction && isAction(trace.getDecision()))) {
                byDay.put(trace.getTradeDate(), trace);
            }
        }

        // 日线收盘序列（判定的"后来"）。行情失败 → 全部 no_price（纪律 3）
        Map<String, BigDecimal> closeByDate = new HashMap<>();
        List<String> orderedDates = new ArrayList<>();
        try {
            StockHistoryResponse history = stockDataGateway.getStockHistory(
                    strategy.getSymbol(), "daily");
            if (history != null && history.timeSeries() != null) {
                history.timeSeries().entrySet().stream()
                        .filter(e -> e.getKey() != null && e.getValue() != null)
                        .sorted(Comparator.comparing(Map.Entry::getKey))
                        .forEach(e -> {
                            BigDecimal close = parse(e.getValue().close());
                            if (close != null) {
                                closeByDate.put(e.getKey(), close);
                                orderedDates.add(e.getKey());
                            }
                        });
            }
        } catch (Exception e) {
            log.warn("决策日志取日线失败（按 no_price 计）strategyId={}: {}",
                    strategy.getId(), e.getMessage());
        }

        int hits = 0;
        int misses = 0;
        int pending = 0;
        int skips = 0;
        List<Entry> entries = new ArrayList<>();
        // 倒序输出：最近的日子在最上面（用户最关心"最近判断得怎么样"）
        List<LocalDate> sortedDays = new ArrayList<>(byDay.keySet());
        sortedDays.sort(Comparator.reverseOrder());

        for (LocalDate day : sortedDays) {
            PaperTradeTrace trace = byDay.get(day);
            Entry entry = entryFor(trace, day, closeByDate, orderedDates);
            switch (entry.verdict()) {
                case TradeJournalResponse.VERDICT_HIT -> hits++;
                case TradeJournalResponse.VERDICT_MISS -> misses++;
                case TradeJournalResponse.VERDICT_PENDING -> pending++;
                case TradeJournalResponse.VERDICT_SKIP -> skips++;
                default -> { /* no_price 不进任何分母 */ }
            }
            entries.add(entry);
        }

        int evaluated = hits + misses;
        BigDecimal hitRate = evaluated == 0 ? null
                : BigDecimal.valueOf(hits * 100.0 / evaluated).setScale(1, RoundingMode.HALF_UP);

        return new TradeJournalResponse(strategy.getId(), strategy.getName(),
                strategy.getSymbol(), evaluated, hits, misses, pending, skips,
                hitRate, entries);
    }

    private Entry entryFor(PaperTradeTrace trace, LocalDate day,
                           Map<String, BigDecimal> closeByDate, List<String> orderedDates) {
        String decision = normalize(trace.getDecision());
        BigDecimal closeAtDecision = closeByDate.get(day.toString());
        BigDecimal nextClose = null;
        BigDecimal nextChangePct = null;

        if (isAction(decision)) {
            int idx = orderedDates.indexOf(day.toString());
            if (idx >= 0 && idx + 1 < orderedDates.size()) {
                nextClose = closeByDate.get(orderedDates.get(idx + 1));
                if (nextClose != null && closeAtDecision != null
                        && closeAtDecision.signum() > 0) {
                    nextChangePct = nextClose.subtract(closeAtDecision)
                            .multiply(BigDecimal.valueOf(100))
                            .divide(closeAtDecision, 2, RoundingMode.HALF_UP);
                }
            }
        }

        return new Entry(day, decision, trace.getSignal(), trace.getSkipReason(),
                trace.getDecisionMode(), closeAtDecision, nextClose, nextChangePct,
                verdictFor(decision, closeAtDecision, nextClose, nextChangePct));
    }

    /**
     * 判定：方向对了 hit，错了 miss；0.00% 的平盘按 miss 计（对 buy 来说
     * "没涨"就不是对了）——口径见 TradeJournalResponse 的诚实边界。
     */
    private String verdictFor(String decision, BigDecimal closeAtDecision,
                              BigDecimal nextClose, BigDecimal nextChangePct) {
        if (!isAction(decision)) {
            return TradeJournalResponse.VERDICT_SKIP;
        }
        if (nextClose == null || nextChangePct == null) {
            // 决策日之后还没有下一根收盘（最近一两天的决策）vs 行情源缺数据：
            // 有基准价说明行情正常 → pending；连基准价都没有 → no_price
            return closeAtDecision == null
                    ? TradeJournalResponse.VERDICT_NO_PRICE
                    : TradeJournalResponse.VERDICT_PENDING;
        }
        boolean up = nextChangePct.signum() > 0;
        return ("buy".equals(decision)) == up
                ? TradeJournalResponse.VERDICT_HIT
                : TradeJournalResponse.VERDICT_MISS;
    }

    private static boolean isAction(String decision) {
        return ExecutionContract.DECISION_BUY.equals(decision)
                || ExecutionContract.DECISION_SELL.equals(decision);
    }

    private static String normalize(String decision) {
        return decision == null ? "" : decision.trim().toLowerCase();
    }

    private static BigDecimal parse(String value) {
        if (value == null || value.isBlank()) {
            return null;
        }
        try {
            BigDecimal parsed = new BigDecimal(value.trim());
            // 0 价是脏数据（停牌/源站异常），当没有处理：当基准价会除零或恒 miss
            return parsed.signum() > 0 ? parsed : null;
        } catch (NumberFormatException e) {
            return null;
        }
    }
}
