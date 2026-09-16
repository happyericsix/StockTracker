package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.dto.PaperAccountResponse;
import com.happyericsix.stocktracker.dto.PaperTradeResponse;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.support.TransactionTemplate;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ObjectNode;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.stream.Collectors;

@Service
public class PaperTradingService {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingService.class);

    private static final double DEFAULT_INITIAL_CAPITAL = 100000.0;
    private static final double DEFAULT_COMMISSION_PCT = 0.1;
    private static final double DEFAULT_SLIPPAGE_PCT = 0.1;
    /** 卖出印花税（A 股，单边卖出；以最新法规为准） */
    private static final double DEFAULT_STAMP_TAX_PCT = 0.05;
    private static final double DEFAULT_MIN_COMMISSION_YUAN = 5.0;
    private static final String DEFAULT_POSITION_TYPE = "full";
    private static final double DEFAULT_SIZE_PCT = 100.0;

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;
    /** 客观事实写入（W1）。允许为 null：单测没有替身，且"记不进记忆"不该影响结算。 */
    private final MemoryFactService memoryFactService;
    private final MemoryService memoryService;
    private final ObjectMapper mapper = new ObjectMapper();
    private final TransactionTemplate transactionTemplate;

    public PaperTradingService(StrategyRepository strategyRepository,
                               PaperAccountRepository paperAccountRepository,
                               PaperTradeRepository paperTradeRepository,
                               StrategyClient strategyClient,
                               UserRepository userRepository,
                               PlatformTransactionManager transactionManager,
                               MemoryFactService memoryFactService,
                               MemoryService memoryService) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.transactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    /**
     * 不包 @Transactional：evaluateRealtimeStrategy 内部会阻塞调用 Python（最长 60s），
     * 若包在长事务里会占住数据库连接；改为各 repository 写操作走自身短事务。
     */
    public PaperAccountResponse startPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);

        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseGet(PaperAccount::new);
        initializeAccount(account, strategy);

        strategy.setPaperEnabled(true);
        account = paperAccountRepository.save(account);
        strategyRepository.save(strategy);

        PaperAccount evaluated = evaluateRealtimeStrategy(strategyId);
        if (evaluated != null) {
            account = evaluated;
        }

        log.info("User {} started paper trading for strategy id={}", username, strategyId);
        return PaperAccountResponse.from(account);
    }

    @Transactional
    public void stopPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);
        strategy.setPaperEnabled(false);
        strategyRepository.save(strategy);
        log.info("User {} stopped paper trading for strategy id={}", username, strategyId);
    }

    public void evaluateDaily() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        LocalDate today = LocalDate.now();
        for (Strategy strategy : strategies) {
            try {
                Long strategyId = strategy.getId();
                transactionTemplate.execute(status -> {
                    evaluateStrategy(strategyId, today);
                    return null;
                });
            } catch (Exception e) {
                log.error("Paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    public void evaluateRealtime() {
        List<Strategy> strategies = strategyRepository.findByPaperEnabledTrue();
        for (Strategy strategy : strategies) {
            try {
                Long strategyId = strategy.getId();
                transactionTemplate.execute(status -> {
                    evaluateRealtimeStrategy(strategyId);
                    return null;
                });
            } catch (Exception e) {
                log.error("Realtime paper settlement failed for strategy id={}", strategy.getId(), e);
            }
        }
    }

    public PaperAccountResponse getAccount(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("模拟账户不存在"));
        return PaperAccountResponse.from(account);
    }

    public List<PaperTradeResponse> getTrades(String username, Long strategyId) {
        User user = getUser(username);
        loadStrategy(user, strategyId);
        return paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategyId).stream()
                .map(PaperTradeResponse::from)
                .collect(Collectors.toList());
    }

    private PaperAccount evaluateStrategy(Long strategyId, LocalDate today) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        if (paperTradeRepository.existsByStrategyIdAndTradeDate(strategy.getId(), today)) {
            log.debug("Paper settlement already ran for strategy id={} on {}", strategy.getId(), today);
            return null;
        }

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }

        JsonNode position = buildPosition(account);
        JsonNode result = strategyClient.evaluateBar(
                strategy.getConfigJson(), strategy.getSymbol(), today.toString(), position);
        if (result == null) {
            log.warn("Strategy evaluateBar returned null for strategy id={}", strategy.getId());
            return account;
        }
        // 休市/数据未出：Python 端明确标记请求日无 bar 时，禁止用旧 bar 冒充当日成交
        if (result.has("bar_date_missing") && result.get("bar_date_missing").asBoolean(false)) {
            log.warn("Paper settlement skipped for strategy id={} on {}: bar missing (休市/数据未出)",
                    strategy.getId(), today);
            return account;
        }

        PaperAccount settled = applyBarResult(strategy, account, result, today, null);
        recordPaperFacts(strategy, settled, today);
        return settled;
    }

    /**
     * 把当天的模拟盘结算记成**客观事实**（W1）。
     *
     * <h3>为什么只在"每日结算"里记，不在实时结算里记</h3>
     * {@link #evaluateRealtimeStrategy} 由价格刷新事件触发，一天可能跑几十上百次。
     * 在那里记事实的后果不是"多几条"，而是<b>把取代链冲垮</b>：
     * 同一个键上一天出现上百个值，历史链会变得没法读，
     * "净值什么时候真的变了"这个问题的答案就被噪声淹掉了。
     * 事实的粒度应该是"一个有意义的观测点"（收盘结算），不是"每一次内部计算"。
     *
     * <h3>为什么失败不入库也不影响结算</h3>
     * 与 {@link StrategyService} 的回测路径同一条纪律：结算的结果已经算出来了，
     * 记忆写不进去只是少了一份记录，绝不能让它回滚掉真实的成交与持仓。
     */
    private void recordPaperFacts(Strategy strategy, PaperAccount account, LocalDate tradeDate) {
        try {
            if (memoryFactService == null || memoryService == null || strategy == null
                    || account == null || strategy.getUser() == null) {
                return;
            }
            String subject = ObjectiveFactKeys.strategySubject(strategy.getId());
            if (subject.isBlank()) {
                return;
            }
            LocalDateTime at = LocalDateTime.of(tradeDate, java.time.LocalTime.of(15, 0));
            List<MemoryFactRequest> facts = new ArrayList<>();
            addFact(facts, subject, ObjectiveFactKeys.PAPER_EQUITY, account.getEquity(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_CASH, account.getCash(), at);
            addFact(facts, subject, ObjectiveFactKeys.PAPER_SHARES, account.getShares(), at);
            // 收益率的口径只该有一处：初始本金是账户自己的字段，不让调用方各算一遍
            Double initial = account.getInitialCapital();
            Double equity = account.getEquity();
            if (initial != null && initial != 0.0 && equity != null) {
                addFact(facts, subject, ObjectiveFactKeys.PAPER_RETURN_PCT,
                        (equity - initial) / initial * 100.0, at);
            }
            addFact(facts, subject, ObjectiveFactKeys.PAPER_LAST_EVAL_AT,
                    at.withNano(0).toString(), at);
            if (facts.isEmpty()) {
                return;
            }
            memoryFactService.recordObjective(strategy.getUser().getId(),
                    memoryService.currentSessionKey(strategy.getUser().getId()), facts);
        } catch (Exception e) {
            log.warn("模拟盘客观事实入账失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
        }
    }

    private static void addFact(List<MemoryFactRequest> facts, String subject, String predicate,
                                Object value, LocalDateTime dataAsOf) {
        MemoryFactRequest fact = ObjectiveFactKeys.observation(subject, predicate, value, dataAsOf);
        if (fact != null) {
            facts.add(fact);
        }
    }

    private PaperAccount evaluateRealtimeStrategy(Long strategyId) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }

        JsonNode position = buildPosition(account);
        JsonNode result = strategyClient.evaluateBarRealtime(
                strategy.getConfigJson(), strategy.getSymbol(), position);
        if (result == null) {
            log.warn("Realtime evaluateBar returned null for strategy id={}", strategy.getId());
            return account;
        }

        String barTime = textOr(result.get("bar_time"), "");
        if (barTime.isBlank()) {
            log.debug("Realtime evaluateBar missing bar_time for strategy id={}", strategy.getId());
            return account;
        }
        if (barTime.equals(account.getLastBarTime())) {
            log.debug("Realtime bar already settled for strategy id={} at {}", strategy.getId(), barTime);
            return account;
        }
        account.setLastBarTime(barTime);
        // 刻意**不**在这里写客观事实：本方法由每次价格刷新触发，
        // 在这里记会把取代链冲成一天上百个值（见 recordPaperFacts 的说明）。
        return applyBarResult(strategy, account, result, LocalDate.now(), barTime);
    }

    private PaperAccount applyBarResult(Strategy strategy, PaperAccount account,
                                        JsonNode result, LocalDate tradeDate, String barTime) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        JsonNode risk = config.get("risk");
        double commission = doubleOr(field(risk, "commission_pct"), DEFAULT_COMMISSION_PCT) / 100.0;
        double slippage = doubleOr(field(risk, "slippage_pct"), DEFAULT_SLIPPAGE_PCT) / 100.0;
        // A 股卖出印花税（0.05%）与整手规则：科创板 688/689 起购 200 股，其余默认 100 股/手
        double stampTax = doubleOr(field(risk, "stamp_tax_pct"), DEFAULT_STAMP_TAX_PCT) / 100.0;
        double lot = doubleOr(field(risk, "lot_size"), defaultLotFor(strategy.getSymbol()));

        JsonNode positionConfig = config.get("position");
        String positionType = textOr(field(positionConfig, "type"), DEFAULT_POSITION_TYPE);
        double sizePct = doubleOr(field(positionConfig, "size_pct"), DEFAULT_SIZE_PCT) / 100.0;

        String signal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        double price = doubleOr(result.get("price"), 0.0);
        String reason = joinMatchedConditions(result.get("matched_conditions"));
        double cash = account.getCash() == null ? 0.0 : account.getCash();

        PaperTrade trade = null;
        if ("buy".equals(signal) && !isHolding(account)) {
            double fill = price * (1.0 + slippage);
            if (fill <= 0.0) {
                log.warn("Invalid fill price for buy on strategy id={}", strategy.getId());
                return account;
            }

            double budget = "percent".equals(positionType) ? cash * sizePct : cash;
            if (budget <= 0.0) {
                log.warn("No cash available for buy on strategy id={}", strategy.getId());
                return account;
            }

            // 整手取整：按 (预算 / (成交价*(1+佣金)*手数)) 向下取整手
            double shares = Math.floor(budget / (fill * (1.0 + commission) * lot)) * lot;
            while (shares > 0) {
                double fee = commissionFee(shares * fill, commission);
                if (shares * fill + fee <= cash) {
                    break;
                }
                shares -= lot;
            }
            if (shares <= 0.0) {
                log.warn("Not enough cash for even one lot on strategy id={} (fill={})", strategy.getId(), fill);
                return account;
            }
            double fee = commissionFee(shares * fill, commission);
            double cashAfter = cash - shares * fill - fee;
            if (cashAfter < 0.0) {
                log.warn("Buy would exceed cash on strategy id={}", strategy.getId());
                return account;
            }
            account.setShares(shares);
            account.setAvgCost(fill);
            account.setCash(cashAfter);
            account.setHighWatermark(fill);
            account.setLastBuyBar(barTime);   // T+1 同交易日卖出守卫（realtime 按 bar 时间判断）
            trade = buildTrade(strategy, account, tradeDate, "BUY", fill, shares, reason);
        } else if ("sell".equals(signal) && isHolding(account)) {
            // T+1：A 股当日买入当日不可卖（barTime 带日期时按自然日判断）
            boolean t1Blocked = barTime != null && account.getLastBuyBar() != null
                    && sameTradingDay(barTime, account.getLastBuyBar());
            double fill = price * (1.0 - slippage);
            if (t1Blocked) {
                log.debug("Paper: T+1 blocks sell on same trading day (bar {}) for strategy id={}",
                        barTime, strategy.getId());
            } else if (fill <= 0.0) {
                log.warn("Invalid fill price for sell on strategy id={}", strategy.getId());
            } else {
                double shares = account.getShares();
                double fee = commissionFee(shares * fill, commission);
                double stampAmount = shares * fill * stampTax;
                double cashAfter = cash + shares * fill - fee - stampAmount;
                account.setCash(cashAfter);
                account.setShares(0.0);
                account.setAvgCost(0.0);
                account.setHighWatermark(0.0);
                account.setLastBuyBar(null);
                trade = buildTrade(strategy, account, tradeDate, "SELL", fill, shares, reason);
            }
        }

        updateEquityAndHighWatermark(account, price);
        account.setLastPrice(price);
        account.setLastSignal(signal);
        account.setLastEvalAt(LocalDateTime.now());

        if (trade != null) {
            paperTradeRepository.save(trade);
        }
        account = paperAccountRepository.save(account);

        strategy.setLastPaperEvalAt(account.getLastEvalAt());
        strategyRepository.save(strategy);
        return account;
    }

    private void initializeAccount(PaperAccount account, Strategy strategy) {
        JsonNode config = parseConfig(strategy.getConfigJson());
        double initialCapital = doubleOr(config.get("initial_capital"), DEFAULT_INITIAL_CAPITAL);

        if (account.getUser() == null) {
            account.setUser(strategy.getUser());
        }
        if (account.getStrategy() == null) {
            account.setStrategy(strategy);
        }
        if (account.getInitialCapital() == null) {
            account.setInitialCapital(initialCapital);
        }
        if (account.getCash() == null) {
            account.setCash(account.getInitialCapital());
        }
        if (account.getEquity() == null) {
            account.setEquity(account.getCash());
        }
        if (account.getShares() == null) {
            account.setShares(0.0);
        }
        if (account.getAvgCost() == null) {
            account.setAvgCost(0.0);
        }
        if (account.getHighWatermark() == null) {
            account.setHighWatermark(0.0);
        }
    }

    private JsonNode buildPosition(PaperAccount account) {
        if (!isHolding(account)) {
            return null;
        }
        ObjectNode position = mapper.createObjectNode();
        position.put("entry_price", account.getAvgCost() == null ? 0.0 : account.getAvgCost());
        position.put("shares", account.getShares() == null ? 0.0 : account.getShares());
        position.put("high_watermark", account.getHighWatermark() == null ? 0.0 : account.getHighWatermark());
        return position;
    }

    private void updateEquityAndHighWatermark(PaperAccount account, double price) {
        double shares = account.getShares() == null ? 0.0 : account.getShares();
        double cash = account.getCash() == null ? 0.0 : account.getCash();
        account.setEquity(cash + shares * price);

        if (shares > 0.0) {
            double currentWatermark = account.getHighWatermark() == null
                    ? (account.getAvgCost() == null ? price : account.getAvgCost())
                    : account.getHighWatermark();
            if (price > currentWatermark) {
                account.setHighWatermark(price);
            }
        }
    }

    private PaperTrade buildTrade(Strategy strategy, PaperAccount account, LocalDate tradeDate,
                                  String side, double fill, double shares, String reason) {
        return PaperTrade.builder()
                .user(strategy.getUser())
                .strategy(strategy)
                .account(account)
                .tradeDate(tradeDate)
                .symbol(strategy.getSymbol())
                .side(side)
                .price(fill)
                .shares(shares)
                .amount(shares * fill)
                .reason(reason)
                .build();
    }

    private boolean isHolding(PaperAccount account) {
        return account.getShares() != null && account.getShares() > 0.0;
    }

    /** A 股佣金：费率>0 时按 max(成交额*费率, 5元) 收取；费率=0（测试配置）不收 */
    private double commissionFee(double turnover, double commission) {
        if (commission <= 0.0 || turnover <= 0.0) {
            return 0.0;
        }
        return Math.max(turnover * commission, DEFAULT_MIN_COMMISSION_YUAN);
    }

    /** A 股整手：科创板 688/689 起购 200 股/手，其余 100 股/手（可用 risk.lot_size 覆盖） */
    private double defaultLotFor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        return (s.startsWith("688") || s.startsWith("689")) ? 200.0 : 100.0;
    }

    /** bar 时间形如 "yyyy-MM-dd HH:mm:ss"，按自然日前 10 位判断是否同一交易日 */
    private static boolean sameTradingDay(String barA, String barB) {
        if (barA == null || barB == null || barA.length() < 10 || barB.length() < 10) {
            return false;
        }
        return barA.substring(0, 10).equals(barB.substring(0, 10));
    }

    private JsonNode parseConfig(String configJson) {
        try {
            if (configJson == null || configJson.isBlank()) {
                return mapper.createObjectNode();
            }
            return mapper.readTree(configJson);
        } catch (Exception e) {
            log.warn("Failed to parse strategy config JSON: {}", e.getMessage());
            return mapper.createObjectNode();
        }
    }

    private double doubleOr(JsonNode node, double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return node.asDouble();
    }

    private String textOr(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return text == null || text.isBlank() ? fallback : text;
    }

    private JsonNode field(JsonNode node, String name) {
        return node == null ? null : node.get(name);
    }

    private String joinMatchedConditions(JsonNode node) {
        if (node == null || !node.isArray()) {
            return "";
        }
        List<String> conditions = new ArrayList<>();
        for (JsonNode condition : node) {
            if (!condition.isNull()) {
                conditions.add(condition.asText());
            }
        }
        return String.join(",", conditions);
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }

    private Strategy loadStrategy(User user, Long strategyId) {
        return strategyRepository.findByIdAndUserId(strategyId, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
    }
}
