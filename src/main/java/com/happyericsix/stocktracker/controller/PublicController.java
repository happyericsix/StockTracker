package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.service.ExecutionContract;
import com.happyericsix.stocktracker.service.PaperEquitySeries;
import com.happyericsix.stocktracker.service.Money;
import org.springframework.data.domain.PageRequest;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 匿名只读分享视图（/share/{token}）。
 *
 * <h3>安全边界（这里最容易错，所以先写）</h3>
 * <ul>
 *   <li>只按 token 取数：**不经过** username/userId 的任何关联查询，
 *       响应里也绝不出现用户名/邮箱/手机号 —— 分享的是策略，不是人；</li>
 *   <li>只读：这个控制器没有任何写方法，也不接受任何参数化的过滤条件；</li>
 *   <li>成交明细只给最近 10 笔的日期/方向/价格/股数 —— 与"看一眼这条策略跑得怎么样"
 *       无关的字段（traceId、reason 长文）一律不给。</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/v1/public")
public class PublicController {

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private final PaperTradeRepository paperTradeRepository;

    public PublicController(StrategyRepository strategyRepository,
                            PaperAccountRepository paperAccountRepository,
                            PaperEquitySnapshotRepository paperEquitySnapshotRepository,
                            PaperTradeRepository paperTradeRepository) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
        this.paperTradeRepository = paperTradeRepository;
    }

    @GetMapping("/strategies/{token}")
    public Result<Map<String, Object>> sharedStrategy(@PathVariable String token) {
        Strategy strategy = strategyRepository.findByShareToken(token).orElse(null);
        if (strategy == null) {
            // 不区分"不存在"与"已停止分享"：对外部世界两者是同一件事
            return Result.error(404, "链接不存在或已失效");
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("name", strategy.getName());
        out.put("symbol", strategy.getSymbol());
        out.put("decisionMode", ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
        out.put("createdAt", strategy.getCreatedAt() == null ? null : strategy.getCreatedAt().toLocalDate().toString());
        out.put("paperEnabled", Boolean.TRUE.equals(strategy.getPaperEnabled()));

        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
        if (account != null && account.getLastEvalAt() != null) {
            Map<String, Object> paper = new LinkedHashMap<>();
            paper.put("initialCapital", Money.amount(account.getInitialCapital()));
            paper.put("equity", Money.amount(account.getEquity()));
            List<PaperEquitySnapshot> series = paperEquitySnapshotRepository
                    .findByStrategyIdOrderByTradeDateAsc(strategy.getId());
            if (!series.isEmpty()) {
                paper.put("summary", PaperEquitySeries.summarize(series));
            }
            List<PaperTrade> trades = paperTradeRepository
                    .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId());
            paper.put("tradeCount", trades.size());
            paper.put("recentTrades", trades.stream().limit(10).map(t -> {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("date", t.getTradeDate() == null ? null : t.getTradeDate().toString());
                row.put("side", t.getSide());
                row.put("price", Money.price(t.getPrice()));
                row.put("shares", Money.price(t.getShares()));
                return row;
            }).toList());
            out.put("paper", paper);
        }
        return Result.success(out);
    }
}
