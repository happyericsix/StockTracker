package com.happyericsix.stocktracker.dto;

import tools.jackson.databind.JsonNode;

public class ModelDiagnosticResponse {
    private String symbol;
    private JsonNode risk;
    private JsonNode modelStatus;
    private JsonNode modelConsensus;
    private String disclaimer;

    public ModelDiagnosticResponse() {}

    public ModelDiagnosticResponse(String symbol, JsonNode risk, JsonNode modelStatus,
                                   JsonNode modelConsensus, String disclaimer) {
        this.symbol = symbol;
        this.risk = risk;
        this.modelStatus = modelStatus;
        this.modelConsensus = modelConsensus;
        this.disclaimer = disclaimer;
    }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public JsonNode getRisk() { return risk; }
    public void setRisk(JsonNode risk) { this.risk = risk; }
    public JsonNode getModelStatus() { return modelStatus; }
    public void setModelStatus(JsonNode modelStatus) { this.modelStatus = modelStatus; }
    public JsonNode getModelConsensus() { return modelConsensus; }
    public void setModelConsensus(JsonNode modelConsensus) { this.modelConsensus = modelConsensus; }
    public String getDisclaimer() { return disclaimer; }
    public void setDisclaimer(String disclaimer) { this.disclaimer = disclaimer; }
}
