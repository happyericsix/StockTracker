package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.Strategy;

import java.time.LocalDateTime;

public class StrategyResponse {
    private Long id;
    private String name;
    private String symbol;
    private String configJson;
    private Boolean paperEnabled;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime lastBacktestAt;

    public StrategyResponse() {}

    public StrategyResponse(Long id, String name, String symbol, String configJson,
                            Boolean paperEnabled, LocalDateTime createdAt,
                            LocalDateTime updatedAt, LocalDateTime lastBacktestAt) {
        this.id = id;
        this.name = name;
        this.symbol = symbol;
        this.configJson = configJson;
        this.paperEnabled = paperEnabled;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
        this.lastBacktestAt = lastBacktestAt;
    }

    public static StrategyResponse from(Strategy entity) {
        return new StrategyResponse(
                entity.getId(),
                entity.getName(),
                entity.getSymbol(),
                entity.getConfigJson(),
                entity.getPaperEnabled(),
                entity.getCreatedAt(),
                entity.getUpdatedAt(),
                entity.getLastBacktestAt()
        );
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getConfigJson() { return configJson; }
    public void setConfigJson(String configJson) { this.configJson = configJson; }
    public Boolean getPaperEnabled() { return paperEnabled; }
    public void setPaperEnabled(Boolean paperEnabled) { this.paperEnabled = paperEnabled; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(LocalDateTime updatedAt) { this.updatedAt = updatedAt; }
    public LocalDateTime getLastBacktestAt() { return lastBacktestAt; }
    public void setLastBacktestAt(LocalDateTime lastBacktestAt) { this.lastBacktestAt = lastBacktestAt; }
}
