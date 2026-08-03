package com.happyericsix.stocktracker.dto;

/**
 * 内部服务调用：Python webhook 验证绑定时使用
 * Body: { "qqId": "12345678", "code": "888888" }
 */
public class BindQqRequest {
    private String qqId;
    private String code;

    public BindQqRequest() {}
    public BindQqRequest(String qqId, String code) {
        this.qqId = qqId;
        this.code = code;
    }

    public String getQqId() { return qqId; }
    public void setQqId(String qqId) { this.qqId = qqId; }
    public String getCode() { return code; }
    public void setCode(String code) { this.code = code; }
}
