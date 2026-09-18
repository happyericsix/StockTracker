package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.service.ExecutionContract;

import java.time.LocalDate;
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
    /**
     * 决策来源（{@code rule} / {@code agent}）与生效日。
     *
     * <p>必须出现在读视图里：净值曲线的含义**从生效日起分成两段**，
     * 界面和报告都得能说出"这段曲线是谁做出来的"。只写在后端字段里、
     * 不返回给调用方，等于让每个读者自己猜。
     */
    private String decisionMode;
    private LocalDate decisionModeSince;

    public StrategyResponse() {}

    public StrategyResponse(Long id, String name, String symbol, String configJson,
                            Boolean paperEnabled, LocalDateTime createdAt,
                            LocalDateTime updatedAt, LocalDateTime lastBacktestAt,
                            String decisionMode, LocalDate decisionModeSince) {
        this.id = id;
        this.name = name;
        this.symbol = symbol;
        this.configJson = configJson;
        this.paperEnabled = paperEnabled;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
        this.lastBacktestAt = lastBacktestAt;
        this.decisionMode = decisionMode;
        this.decisionModeSince = decisionModeSince;
    }

    public static StrategyResponse from(Strategy entity) {
        if (entity == null) {
            return null;
        }
        return new StrategyResponse(
                entity.getId(),
                entity.getName(),
                entity.getSymbol(),
                entity.getConfigJson(),
                entity.getPaperEnabled(),
                entity.getCreatedAt(),
                entity.getUpdatedAt(),
                entity.getLastBacktestAt(),
                // 归一化后再出去：存量策略的 null 在界面与报告里都是"规则"这一路，
                // 不能让两种写法在调用方那里变成两种含义。
                ExecutionContract.normalizeDecisionMode(entity.getDecisionMode()),
                entity.getDecisionModeSince()
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
    public String getDecisionMode() { return decisionMode; }
    public void setDecisionMode(String decisionMode) { this.decisionMode = decisionMode; }
    public LocalDate getDecisionModeSince() { return decisionModeSince; }
    public void setDecisionModeSince(LocalDate decisionModeSince) { this.decisionModeSince = decisionModeSince; }
}
