package com.happyericsix.stocktracker.dto;

public class ChatSendRequest {
    private String message;

    public ChatSendRequest() {}
    public ChatSendRequest(String message) { this.message = message; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }
}
