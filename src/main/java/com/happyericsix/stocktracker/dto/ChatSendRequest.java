package com.happyericsix.stocktracker.dto;

/**
 * 聊天发送请求。{@code focus} 是可选的"聚焦上下文"（agent 互通 P2，2026-10-09）：
 * 用户从资讯雷达点"问 AI"进入聚焦对话时，前端在**聚焦期间的每条消息**上都带它。
 *
 * <p>形状一：{@code {"type":"news_event","eventId":321}} —— Python 侧会把
 * 这条新闻的事实（标题/来源/方向/摘要/可信度）注入 agent 的 system 上下文，
 * 用户即可围绕这一条报道追问，agent 可再调用新闻工具深挖。
 *
 * <p>形状二（2026-10-16 反问功能）：{@code {"type":"memory_lesson","lessonId":41}} ——
 * 用户在记忆页对一条经验点"反问"，Python 侧把这条教训全文注入上下文，
 * agent 摆证据自证或认错；agent 只答不改，处置权在用户的停用/编辑按钮。
 *
 * <p><b>conversationId（2026-11-07 聚焦会话隔离）</b>：聚焦抽屉每次打开生成一个，
 * 抽屉内多轮共用、关掉即弃（短期记忆）。带它的消息与主聊天助手**双向隔离**：
 * 聚焦对话不携带主聊天历史（每次开启全新），主聊天也看不到聚焦对话的往来
 * ——此前两者共享同一条 CHAT 历史，"问 AI"会被聊天助手的上下文带偏，
 * 聚焦对话的往来也会污染主助手的前情提要。
 *
 * <p>后端刻意无状态：不带 focus/conversationId 就是普通聊天（同一端点、同一会话），
 * "退出聚焦"只是前端不再带这个字段 —— 没有会话状态漂移可测。
 */
public class ChatSendRequest {
    private String message;
    private Focus focus;
    /** 聚焦会话 id（可选）：抽屉每次开启生成，抽屉内多轮共用，关掉即弃。 */
    private String conversationId;

    public ChatSendRequest() {}
    public ChatSendRequest(String message) { this.message = message; }

    public String getMessage() { return message; }
    public void setMessage(String message) { this.message = message; }

    public Focus getFocus() { return focus; }
    public void setFocus(Focus focus) { this.focus = focus; }

    public String getConversationId() { return conversationId; }
    public void setConversationId(String conversationId) { this.conversationId = conversationId; }

    /** 聚焦对象。未知 type 由 Python 侧忽略（fail-open 降级为普通聊天）。 */
    public record Focus(String type, Long eventId, Long lessonId) {
        /** 兼容构造：news_event 只带 eventId（lessonId 无意义）。 */
        public Focus(String type, Long eventId) {
            this(type, eventId, null);
        }
    }
}
