package com.happyericsix.stocktracker.dto;

public class FavoriteStockRequest {
    private String symbol;
    private Double buyPrice;
    private Integer quantity;


    public FavoriteStockRequest() {}

    public FavoriteStockRequest(String symbol, Double buyPrice, Integer quantity) {
        this.symbol = symbol;
        this.buyPrice = buyPrice;
        this.quantity = quantity;

    }

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public Double getBuyPrice() { return buyPrice; }
    public void setBuyPrice(Double buyPrice) { this.buyPrice = buyPrice; }
    public Integer getQuantity() { return quantity; }
    public void setQuantity(Integer quantity) { this.quantity = quantity; }


}
