package com.happyericsix.stocktracker.dto;

import java.util.List;

/**
 * 个股资讯时间轴的响应：**事件列表 + 后台解读的进度**。
 *
 * <h3>为什么不是裸数组</h3>
 * 用户的原话：*"本来新闻获取的就慢，用户点进来过了几秒才有新闻内容，结果出来以后
 * 还要再等一会才有你的 ai 分析"*。
 *
 * <p>根因是这条接口原来会在返回**之前**同步做完"抓上游 + 6 次批量 LLM 解读"，
 * 于是首屏那几秒里页面只能显示一行"资讯加载中…"，而且没有任何办法知道
 * "还差多少"。现在改成：接口立刻返回库里已有的内容，抓取与解读交给后台，
 * 并把**进度**一起返回 —— 前端因此能先渲染列表、再让解读逐条长出来。
 *
 * <p>进度不是装饰：没有它，前端只能用一个盲目的定时器反复轮询，既不知道该等多久，
 * 也无法在"模型其实已经失败"时停止等待（那会变成一个永远转下去的圈）。
 *
 * @param events        时间倒序的事件列表（库内即时可得的那些）
 * @param analyzing     后台是否**仍有**抓取/解读在进行。前端据此决定要不要继续轮询
 * @param analyzedCount 已经带解读的条数（前端展示"已解读 x/y"）
 * @param total         本次返回的条数
 * @param pendingCount  其中**这一轮**预计会被解读的条数（受每轮上限约束，可能小于 total - analyzedCount）
 */
public record StockNewsTimelineResponse(
        List<NewsEventResponse> events,
        boolean analyzing,
        int analyzedCount,
        int total,
        int pendingCount) {
}
