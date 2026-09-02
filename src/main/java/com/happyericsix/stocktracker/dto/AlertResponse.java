package com.happyericsix.stocktracker.dto;

import java.time.LocalDateTime;

public class AlertResponse {
    private Long id;
    private String symbol;
    /** 股票名称（服务端解析填充，解析失败时回退为代码） */
    private String name;
    private String conditionType;
    private Double threshold;
    private Boolean enabled;
    private Boolean armed;
    private LocalDateTime lastEvaluatedAt;
    private Integer cooldownMinutes;
    private Double resetRatio;
    private Integer reArmHours;
    private Double highWatermark;  // v3 跟踪止盈专用

    public AlertResponse() {}
    public AlertResponse(Long id, String symbol, String conditionType, Double threshold, Boolean enabled) {
        this.id = id; this.symbol = symbol; this.conditionType = conditionType;
        this.threshold = threshold; this.enabled = enabled;
    }
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getConditionType() { return conditionType; }
    public void setConditionType(String conditionType) { this.conditionType = conditionType; }
    public Double getThreshold() { return threshold; }
    public void setThreshold(Double threshold) { this.threshold = threshold; }
    public Boolean getEnabled() { return enabled; }
    public void setEnabled(Boolean enabled) { this.enabled = enabled; }
    public Boolean getArmed() { return armed; }
    public void setArmed(Boolean armed) { this.armed = armed; }
    public LocalDateTime getLastEvaluatedAt() { return lastEvaluatedAt; }
    public void setLastEvaluatedAt(LocalDateTime lastEvaluatedAt) { this.lastEvaluatedAt = lastEvaluatedAt; }
    public Integer getCooldownMinutes() { return cooldownMinutes; }
    public void setCooldownMinutes(Integer cooldownMinutes) { this.cooldownMinutes = cooldownMinutes; }
    public Double getResetRatio() { return resetRatio; }
    public void setResetRatio(Double resetRatio) { this.resetRatio = resetRatio; }
    public Integer getReArmHours() { return reArmHours; }
    public void setReArmHours(Integer reArmHours) { this.reArmHours = reArmHours; }
    public Double getHighWatermark() { return highWatermark; }
    public void setHighWatermark(Double highWatermark) { this.highWatermark = highWatermark; }
}
