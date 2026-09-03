package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.ModelDiagnosticResponse;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import tools.jackson.databind.JsonNode;

import java.time.LocalDateTime;
import java.util.List;
import java.util.stream.Collectors;

@Service
public class StrategyService {

    private static final Logger log = LoggerFactory.getLogger(StrategyService.class);

    private final StrategyRepository strategyRepository;
    private final StrategyClient strategyClient;
    private final UserRepository userRepository;

    public StrategyService(StrategyRepository strategyRepository,
                           StrategyClient strategyClient,
                           UserRepository userRepository) {
        this.strategyRepository = strategyRepository;
        this.strategyClient = strategyClient;
        this.userRepository = userRepository;
    }

    @Transactional
    public StrategyResponse createStrategy(String username, StrategyRequest request) {
        User user = getUser(username);
        Strategy strategy = Strategy.builder()
                .name(request.getName())
                .symbol(request.getSymbol())
                .configJson(request.getConfigJson())
                .user(user)
                .paperEnabled(false)
                .build();

        strategy = strategyRepository.save(strategy);
        log.info("User {} created strategy id={}", username, strategy.getId());
        return StrategyResponse.from(strategy);
    }

    public List<StrategyResponse> listStrategies(String username) {
        User user = getUser(username);
        return strategyRepository.findByUserIdOrderByUpdatedAtDesc(user.getId()).stream()
                .map(StrategyResponse::from)
                .collect(Collectors.toList());
    }

    @Transactional
    public StrategyResponse updateStrategy(String username, Long id, StrategyRequest request) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        if (request.getName() != null && !request.getName().isBlank()) {
            strategy.setName(request.getName());
        }
        if (request.getSymbol() != null && !request.getSymbol().isBlank()) {
            strategy.setSymbol(request.getSymbol());
        }
        if (request.getConfigJson() != null && !request.getConfigJson().isBlank()) {
            strategy.setConfigJson(request.getConfigJson());
        }

        strategy = strategyRepository.save(strategy);
        log.info("User {} updated strategy id={}", username, id);
        return StrategyResponse.from(strategy);
    }

    @Transactional
    public void deleteStrategy(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));
        strategyRepository.delete(strategy);
        log.info("User {} deleted strategy id={}", username, id);
    }

    @Transactional
    public JsonNode runBacktest(String username, Long id) {
        User user = getUser(username);
        Strategy strategy = strategyRepository.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        JsonNode result = strategyClient.backtestStrategy(strategy.getConfigJson());
        strategy.setLastBacktestAt(LocalDateTime.now());
        strategyRepository.save(strategy);
        log.info("User {} ran backtest for strategy id={}", username, id);
        return result;
    }

    public ModelDiagnosticResponse getModelDiagnostic(String username, Long id) {
        Strategy strategy = strategyRepository.findByIdAndUserId(id, getUser(username).getId())
                .orElseThrow(() -> new IllegalArgumentException("策略不存在"));

        JsonNode node = strategyClient.getModelDiagnostic(strategy.getSymbol());
        String symbol = node.hasNonNull("symbol") ? node.get("symbol").asText() : strategy.getSymbol();
        return new ModelDiagnosticResponse(
                symbol,
                node.get("risk"),
                node.get("model_status"),
                node.get("model_consensus"),
                node.hasNonNull("disclaimer") ? node.get("disclaimer").asText() : ""
        );
    }

    @Transactional
    public StrategyResponse createFromAgent(String username, JsonNode strategyJson) {
        User user = getUser(username);
        JsonNode safeJson = strategyJson != null ? strategyJson : null;

        String name = textOr(safeJson == null ? null : safeJson.get("name"), "策略");
        String symbol = textOr(safeJson == null ? null : safeJson.get("symbol"), "");
        String configJson = safeJson == null ? "{}" : safeJson.toString();

        Strategy strategy = Strategy.builder()
                .name(name)
                .symbol(symbol)
                .configJson(configJson)
                .user(user)
                .paperEnabled(false)
                .build();

        strategy = strategyRepository.save(strategy);
        log.info("User {} created strategy id={} from agent", username, strategy.getId());
        return StrategyResponse.from(strategy);
    }

    private String textOr(JsonNode node, String fallback) {
        if (node == null || node.isNull()) {
            return fallback;
        }
        String text = node.asText();
        return (text == null || text.isBlank()) ? fallback : text;
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }
}
