package com.happyericsix.stocktracker.exception;

public class FavoriteAlreadyExistsException extends RuntimeException {
    public FavoriteAlreadyExistsException(String stockSymbol) {
        super("该股票已在自选中: " + stockSymbol);
    }
}
