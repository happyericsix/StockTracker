package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 组合层（v1）：把"每条策略各自一条曲线"叠成"这个人全部模拟盘的总视图"。
 *
 * <h3>为什么必须有这一层</h3>
 * 单标的策略看各自曲线时，"三条策略同一天全在跌"这件事是看不见的 ——
 * 而那正是组合风险的定义。这里给出三样东西：
 * ① 总净值与各策略占比；② 策略间日收益的相关性（同涨同跌 = 伪分散）；
 * ③ **单日回撤熔断**：全部模拟盘总净值相对上一收盘的回撤超过阈值时，
 * 挡住所有策略的新开仓（平仓不受影响）。
 *
 * <p>熔断的判定口径：当前总净值 vs 最近一档收盘快照的总净值。
 * 收盘结算（15:30）之后基准自动前移，所以"熔断"天然只在盘中成立 ——
 * 隔夜跳空亏出来的坑不会被盘中熔断追着打（那是"当日"语义，不是"历史最大回撤"）。
 */
@Service
public class PortfolioService {
    private static final Logger log = LoggerFactory.getLogger(PortfolioService.class);

    /** 相关性至少需要几个共同交易日：再少就是两个随机数的相关系数。 */
    private static final int MIN_CORR_POINTS = 6;

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;

    /** 单日总回撤熔断阈值（百分比）。0 或负数 = 关闭。 */
    @Value("${paper.portfolio-halt-pct:5.0}")
    private double haltThresholdPct;

    public PortfolioService(StrategyRepository strategyRepository,
                            PaperAccountRepository paperAccountRepository,
                            PaperEquitySnapshotRepository paperEquitySnapshotRepository) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
    }

    /** 熔断是否生效（新开仓要不要挡）。任何内部失败都返回 false —— 组合读挂了不该停掉交易。 */
    public boolean haltsNewOpens(Long userId) {
        try {
            double drawdown = dayDrawdownPct(userId);
            return haltThresholdPct > 0 && drawdown <= -haltThresholdPct;
        } catch (Exception e) {
            log.warn("portfolio halt check failed (fail-open): {}", e.getMessage());
            return false;
        }
    }

    /** 组合总览（Paper 页面板用）。空仓/没开过模拟盘就返回空标记，前端整块隐藏。 */
    public Map<String, Object> portfolio(Long userId) {
        Map<String, Object> out = new LinkedHashMap<>();
        List<Strategy> strategies = strategiesOf(userId);
        List<Map<String, Object>> rows = new ArrayList<>();
        Sum total = new Sum();
        Sum baseline = new Sum();
        int active = 0;

        // 每策略：当前净值 + 作为"昨日基准"的最近一档收盘快照
        for (Strategy s : strategies) {
            PaperAccount account = paperAccountRepository.findByStrategyId(s.getId()).orElse(null);
            if (account == null || account.getLastEvalAt() == null) {
                continue; // 没评估过的账户只有初始资金，不进组合口径（与总览页同一判定）
            }
            active++;
            double equity = account.getEquity() == null ? 0.0 : account.getEquity().doubleValue();
            double initial = account.getInitialCapital() == null ? 0.0
                    : account.getInitialCapital().doubleValue();
            total.add(equity);
            baseline.add(latestBaseline(s.getId(), initial));

            Map<String, Object> row = new LinkedHashMap<>();
            row.put("id", s.getId());
            row.put("name", s.getName());
            row.put("symbol", s.getSymbol());
            row.put("equity", Money.amount(account.getEquity()));
            row.put("initialCapital", Money.amount(account.getInitialCapital()));
            row.put("pnlPct", initial > 0 ? round((equity / initial - 1.0) * 100.0) : null);
            row.put("decisionMode", ExecutionContract.normalizeDecisionMode(s.getDecisionMode()));
            rows.add(row);
        }

        out.put("activeStrategies", active);
        out.put("totalEquity", total.value == 0.0 ? null : round(total.value));
        out.put("totalBaseline", baseline.value == 0.0 ? null : round(baseline.value));
        out.put("dayDrawdownPct", baseline.value > 0
                ? round((total.value / baseline.value - 1.0) * 100.0) : null);
        out.put("haltThresholdPct", haltThresholdPct);
        out.put("haltActive", haltsNewOpens(userId));
        out.put("strategies", rows);
        out.put("correlations", correlations(strategies));
        return out;
    }

    /** 相邻快照的日收益 → Pearson 相关。样本不足/零方差返回 null（前端显示"—"）。 */
    private List<Map<String, Object>> correlations(List<Strategy> strategies) {
        List<Map<String, Object>> out = new ArrayList<>();
        List<Map<LocalDate, Double>> returns = new ArrayList<>();
        List<Strategy> withData = new ArrayList<>();
        for (Strategy s : strategies) {
            Map<LocalDate, Double> r = dailyReturns(s.getId());
            if (!r.isEmpty()) {
                returns.add(r);
                withData.add(s);
            }
        }
        for (int i = 0; i < withData.size(); i++) {
            for (int j = i + 1; j < withData.size(); j++) {
                Double rho = pearson(returns.get(i), returns.get(j));
                if (rho == null) {
                    continue;
                }
                Map<String, Object> item = new HashMap<>();
                item.put("a", withData.get(i).getName());
                item.put("b", withData.get(j).getName());
                item.put("rho", round(rho));
                out.add(item);
            }
        }
        return out;
    }

    /** 快照净值 → 日收益率（按交易日对齐，跳过缺失日）。 */
    private Map<LocalDate, Double> dailyReturns(Long strategyId) {
        List<PaperEquitySnapshot> series =
                paperEquitySnapshotRepository.findByStrategyIdOrderByTradeDateAsc(strategyId);
        Map<LocalDate, Double> out = new LinkedHashMap<>();
        Double prev = null;
        for (PaperEquitySnapshot snap : series) {
            double equity = snap.getEquity() == null ? 0.0 : snap.getEquity().doubleValue();
            if (prev != null && prev > 0 && equity > 0) {
                out.put(snap.getTradeDate(), equity / prev - 1.0);
            }
            prev = equity;
        }
        return out;
    }

    private Double pearson(Map<LocalDate, Double> xs, Map<LocalDate, Double> ys) {
        List<Double> x = new ArrayList<>();
        List<Double> y = new ArrayList<>();
        for (Map.Entry<LocalDate, Double> e : xs.entrySet()) {
            Double v = ys.get(e.getKey());
            if (v != null) {
                x.add(e.getValue());
                y.add(v);
            }
        }
        if (x.size() < MIN_CORR_POINTS) {
            return null;
        }
        double mx = x.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        double my = y.stream().mapToDouble(Double::doubleValue).average().orElse(0);
        double cov = 0, vx = 0, vy = 0;
        for (int i = 0; i < x.size(); i++) {
            double dx = x.get(i) - mx;
            double dy = y.get(i) - my;
            cov += dx * dy;
            vx += dx * dx;
            vy += dy * dy;
        }
        if (vx <= 0 || vy <= 0) {
            return null; // 一条不动曲线与谁的相关系数都没有意义
        }
        return cov / Math.sqrt(vx * vy);
    }

    /** 当前总回撤（%）：正=涨，负=跌。基准 = 各策略最近收盘快照之和。 */
    private double dayDrawdownPct(Long userId) {
        double current = 0;
        double baseline = 0;
        for (Strategy s : strategiesOf(userId)) {
            PaperAccount account = paperAccountRepository.findByStrategyId(s.getId()).orElse(null);
            if (account == null || account.getLastEvalAt() == null || account.getEquity() == null) {
                continue;
            }
            current += account.getEquity().doubleValue();
            baseline += latestBaseline(s.getId(),
                    account.getInitialCapital() == null ? 0.0
                            : account.getInitialCapital().doubleValue());
        }
        return baseline > 0 ? (current / baseline - 1.0) * 100.0 : 0.0;
    }

    /** 策略的基准净值：最近一档快照（没有快照 = 从未收盘结算过，用初始资金）。 */
    private double latestBaseline(Long strategyId, double initialCapital) {
        List<PaperEquitySnapshot> latest = paperEquitySnapshotRepository
                .findByStrategyIdOrderByTradeDateDesc(strategyId,
                        org.springframework.data.domain.PageRequest.of(0, 1));
        if (latest.isEmpty() || latest.get(0).getEquity() == null) {
            return initialCapital;
        }
        return latest.get(0).getEquity().doubleValue();
    }

    private List<Strategy> strategiesOf(Long userId) {
        return strategyRepository.findByUserIdOrderByUpdatedAtDesc(userId);
    }

    private static double round(double v) {
        return Math.round(v * 100.0) / 100.0;
    }

    /** 简单累加器（组合口径展示两位小数，不值得为此铺 BigDecimal 全套）。 */
    private static final class Sum {
        double value = 0.0;

        void add(double v) {
            value += v;
        }
    }
}
