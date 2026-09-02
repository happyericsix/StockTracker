package com.happyericsix.stocktracker.dto;

public class StrategyRequest {
    private String name;
    private String symbol;
    private String configJson;

    public StrategyRequest() {}

    public StrategyRequest(String name, String symbol, String configJson) {
        this.name = name;
        this.symbol = symbol;
        this.configJson = configJson;
    }

    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getConfigJson() { return configJson; }
    public void setConfigJson(String configJson) { this.configJson = configJson; }
}
