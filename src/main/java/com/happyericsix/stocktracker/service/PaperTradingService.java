package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
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
    private static final String DEFAULT_POSITION_TYPE = "full";
    private static final double DEFAULT_SIZE_PCT = 100.0;

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;
    private final ObjectMapper mapper = new ObjectMapper();
    private final TransactionTemplate transactionTemplate;

    public PaperTradingService(StrategyRepository strategyRepository,
                               PaperAccountRepository paperAccountRepository,
                               PaperTradeRepository paperTradeRepository,
                               StrategyClient strategyClient,
                               UserRepository userRepository,
                               PlatformTransactionManager transactionManager) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
        this.transactionTemplate.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
    }

    @Transactional
    public PaperAccountResponse startPaper(String username, Long strategyId) {
        User user = getUser(username);
        Strategy strategy = loadStrategy(user, strategyId);

        PaperAccount account = paperAccountRepository.findByStrategyId(strategyId)
                .orElseGet(PaperAccount::new);
        initializeAccount(account, strategy);

        strategy.setPaperEnabled(true);
        account = paperAccountRepository.save(account);
        strategyRepository.save(strategy);
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
        return paperTradeRepository.findByStrategyIdOrderByTradeDateDesc(strategyId).stream()
                .map(PaperTradeResponse::from)
                .collect(Collectors.toList());
    }

    private void evaluateStrategy(Long strategyId, LocalDate today) {
        Strategy strategy = strategyRepository.findById(strategyId)
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        if (paperTradeRepository.existsByStrategyIdAndTradeDate(strategy.getId(), today)) {
            log.debug("Paper settlement already ran for strategy id={} on {}", strategy.getId(), today);
            return;
        }

        var existingAccount = paperAccountRepository.findByStrategyId(strategy.getId());
        PaperAccount account = existingAccount.orElseGet(PaperAccount::new);
        boolean isNewAccount = existingAccount.isEmpty();
        initializeAccount(account, strategy);
        if (isNewAccount) {
            account = paperAccountRepository.save(account);
        }

        JsonNode config = parseConfig(strategy.getConfigJson());
        JsonNode risk = config.get("risk");
        double commission = doubleOr(field(risk, "commission_pct"), DEFAULT_COMMISSION_PCT) / 100.0;
        double slippage = doubleOr(field(risk, "slippage_pct"), DEFAULT_SLIPPAGE_PCT) / 100.0;

        JsonNode positionConfig = config.get("position");
        String positionType = textOr(field(positionConfig, "type"), DEFAULT_POSITION_TYPE);
        double sizePct = doubleOr(field(positionConfig, "size_pct"), DEFAULT_SIZE_PCT) / 100.0;

        JsonNode position = buildPosition(account);
        JsonNode result = strategyClient.evaluateBar(
                strategy.getConfigJson(), strategy.getSymbol(), today.toString(), position);
        if (result == null) {
            log.warn("Strategy evaluateBar returned null for strategy id={}", strategy.getId());
            return;
        }

        String signal = textOr(result.get("signal"), "").toLowerCase(Locale.ROOT);
        double price = doubleOr(result.get("price"), 0.0);
        String reason = joinMatchedConditions(result.get("matched_conditions"));
        double cash = account.getCash() == null ? 0.0 : account.getCash();

        PaperTrade trade = null;
        if ("buy".equals(signal) && !isHolding(account)) {
            double fill = price * (1.0 + slippage);
            if (fill <= 0.0) {
                log.warn("Invalid fill price for buy on strategy id={}", strategy.getId());
                return;
            }

            double budget = "percent".equals(positionType) ? cash * sizePct : cash;
            if (budget <= 0.0) {
                log.warn("No cash available for buy on strategy id={}", strategy.getId());
                return;
            }

            double shares = budget * (1.0 - commission) / fill;
            if (shares <= 0.0) {
                log.warn("Invalid share quantity for buy on strategy id={}", strategy.getId());
                return;
            }

            double cashAfter = cash - shares * fill * (1.0 + commission);
            account.setShares(shares);
            account.setAvgCost(fill);
            account.setCash(cashAfter);
            account.setHighWatermark(fill);
            trade = buildTrade(strategy, account, today, "BUY", fill, shares, reason);
        } else if ("sell".equals(signal) && isHolding(account)) {
            double fill = price * (1.0 - slippage);
            if (fill <= 0.0) {
                log.warn("Invalid fill price for sell on strategy id={}", strategy.getId());
                return;
            }

            double shares = account.getShares();
            double cashAfter = cash + shares * fill * (1.0 - commission);
            account.setCash(cashAfter);
            account.setShares(0.0);
            account.setAvgCost(0.0);
            account.setHighWatermark(0.0);
            trade = buildTrade(strategy, account, today, "SELL", fill, shares, reason);
        }

        updateEquityAndHighWatermark(account, price);

        if (trade != null) {
            paperTradeRepository.save(trade);
        }
        paperAccountRepository.save(account);
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
