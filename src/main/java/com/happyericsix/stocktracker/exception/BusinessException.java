package com.happyericsix.stocktracker.exception;

/**
 * 业务异常，用于返回明确的错误信息给客户端
 */
public class BusinessException extends RuntimeException {
    private final int code;

    public BusinessException(int code, String message) {
        super(message);
        this.code = code;
    }

    public int getCode() { return code; }
}
