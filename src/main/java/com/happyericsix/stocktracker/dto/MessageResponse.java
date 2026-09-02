package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.Message;

import java.time.LocalDateTime;

public class MessageResponse {
    private Long id;
    private String type;
    private String content;
    private String relatedSymbol;
    /** 关联股票名称（服务端解析填充，解析失败时回退为代码） */
    private String relatedSymbolName;
    /** 仅 type=ALERT 时有值：关联的预警 ID */
    private Long alertId;
    /** 仅 type=ALERT 时有值：JSON 字符串，含 triggerPrice/triggerValue/threshold/conditionType */
    private String metadata;
    private Boolean read;
    private LocalDateTime createdAt;

    public MessageResponse() {}

    public MessageResponse(Long id, String type, String content, String relatedSymbol,
                           Long alertId, String metadata, Boolean read, LocalDateTime createdAt) {
        this.id = id;
        this.type = type;
        this.content = content;
        this.relatedSymbol = relatedSymbol;
        this.alertId = alertId;
        this.metadata = metadata;
        this.read = read;
        this.createdAt = createdAt;
    }

    /** 实体转响应，聊天与消息服务共用，避免重复映射 */
    public static MessageResponse from(Message m) {
        return new MessageResponse(m.getId(), m.getType(), m.getContent(),
                m.getRelatedSymbol(), m.getAlertId(), m.getMetadata(),
                m.getRead(), m.getCreatedAt());
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getType() { return type; }
    public void setType(String type) { this.type = type; }
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    public String getRelatedSymbol() { return relatedSymbol; }
    public void setRelatedSymbol(String relatedSymbol) { this.relatedSymbol = relatedSymbol; }
    public String getRelatedSymbolName() { return relatedSymbolName; }
    public void setRelatedSymbolName(String relatedSymbolName) { this.relatedSymbolName = relatedSymbolName; }
    public Long getAlertId() { return alertId; }
    public void setAlertId(Long alertId) { this.alertId = alertId; }
    public String getMetadata() { return metadata; }
    public void setMetadata(String metadata) { this.metadata = metadata; }
    public Boolean getRead() { return read; }
    public void setRead(Boolean read) { this.read = read; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
