package com.happyericsix.stocktracker.client;

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
                          ObjectMapper mapper) {
        this.webClient = WebClient.builder().baseUrl(baseUrl).build();
        this.mapper = mapper;
    }

    public JsonNode validateStrategy(String configJson) {
        return post("/api/v1/strategies/validate", java.util.Map.of("strategy_json", raw(configJson)));
    }

    public JsonNode backtestStrategy(String configJson) {
        return post("/api/v1/strategies/backtest", java.util.Map.of("strategy_json", raw(configJson)));
    }

    public JsonNode getModelDiagnostic(String symbol) {
        return get("/api/v1/agent/diagnostic/" + symbol);
    }

    public JsonNode evaluateBar(String configJson, String symbol, String date, JsonNode position) {
        var body = new HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("date", date);
        if (position != null) body.put("position", position);
        return post("/api/v1/strategies/evaluate-bar", body);
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
