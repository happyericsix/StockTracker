package com.happyericsix.stocktracker.dto;

/**
 * 聊天发送请求。{@code focus} 是可选的"聚焦上下文"（agent 互通 P2，2026-10-09）：
 * 用户从资讯雷达点"问 AI"进入聚焦对话时，前端在**聚焦期间的每条消息**上都带它。
 *
 * <p>当前唯一形状：{@code {"type":"news_event","eventId":321}} —— Python 侧会把
 * 这条新闻的事实（标题/来源/方向/摘要/可信度）注入 agent 的 system 上下文，
 * 用户即可围绕这一条报道追问，agent 可再调用新闻工具深挖。
 *
 * <p>后端刻意无状态：不带 focus 就是普通聊天（同一端点、同一会话），
 * "退出聚焦"只是前端不再带这个字段 —— 没有会话状态漂移可测。
 */
public class ChatSendRequest {
    private String message;
    private Focus focus;

    public ChatSendRequest() {}
    public ChatSendRequest(String message) { this.message = message; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public Focus getFocus() { return focus; }
    public void setFocus(Focus focus) { this.focus = focus; }

    /** 聚焦对象。type 目前只有 news_event（后续可扩展，Python 侧未知 type 忽略）。 */
    public record Focus(String type, Long eventId) {}
}
