package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.NewsEventResponse;
import com.happyericsix.stocktracker.dto.NewsSearchRequest;
import com.happyericsix.stocktracker.dto.NewsTopicResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.StockNewsTimelineResponse;
import com.happyericsix.stocktracker.entity.NewsTopic;
import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.NewsService;
import com.happyericsix.stocktracker.service.NewsTopicService;
import lombok.RequiredArgsConstructor;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.Page;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.time.LocalDateTime;
import java.time.format.DateTimeParseException;
import java.util.List;

/**
 * 资讯 / 新闻端点（plan N3）。
 *
 * <p>全量 {@code Result<T>} 包装 + {@code Authentication}（照 {@code StrategyController}，
 * 约定 10）；JWT 保护走 {@code SecurityConfig} 的 {@code anyRequest().authenticated()}，
 * 不需要单独放行或声明。没有缓存：资讯是时效内容，缓存只会造成"看不到新公告"的 bug
 * （plan §0.4 约定 9）。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/news")
public class NewsController {

    private static final Logger log = LoggerFactory.getLogger(NewsController.class);

    private final NewsService newsService;
    private final NewsTopicService newsTopicService;
    private final ObjectMapper objectMapper;
    private final JobRunService jobRunService;

    /**
     * 搜索：body {@code {symbol?, keyword?, types?, days=7, scope?, page=0, size=20}}。
     *
     * <p>{@code scope=mine} 只返回与当前用户自选相关的事件（新闻本身仍是全局事实表）。
     */
    @PostMapping("/search")
    public Result<Page<NewsEventResponse>> search(
            @RequestBody NewsSearchRequest request,
            Authentication authentication) {
        return Result.success(newsService.search(authentication.getName(), request));
    }

    /**
     * 个股事件时间轴（时间倒序）：个股页的"事件与解读"区（N5b-2）用它。
     *
     * <p><b>这条接口现在是"立刻返回"的</b>（2026-09-20 改造）：只读库并返回，
     * 兜底抓取与分批 LLM 解读交给后台；进度通过 {@code analyzing / analyzedCount /
     * pendingCount} 一起返回，前端据此渲染列表并轮询补全。
     *
     * <p>原来的行为是"同步做完抓取 + 最多 6 次模型调用才返回"，用户看到的是
     * 点进去先空等几秒、一个字的正文都没有。
     */
    @GetMapping("/events")
    public Result<StockNewsTimelineResponse> stockEvents(
            @RequestParam String symbol,
            @RequestParam(defaultValue = "30") int days,
            Authentication authentication) {
        return Result.success(newsService.timeline(authentication.getName(), symbol, days));
    }

    /**
     * 结构化解读：body {@code {eventIds:[…], force?:false}}。
     *
     * <p>幂等 —— 已经有解读且 {@code force=false} 的条目不会重复调用模型
     * （前端"深度解读"按钮点第二次不该再花一次钱）。{@code force=true} 才会重跑。
     */
    @PostMapping("/analyze")
    public Result<List<NewsEventResponse>> analyze(
            @RequestBody AnalyzeRequest request,
            Authentication authentication) {
        boolean force = Boolean.TRUE.equals(request.force());
        return Result.success(newsService.analyze(authentication.getName(), request.eventIds(), force));
    }

    /**
     * 个股"当前怎么看"：读完这批事件之后的**一段连贯判断**（一次 LLM 调用，按标的缓存）。
     *
     * <p>与 {@code /analyze} 的分工：那个给每条一个方向标签，这个做综合 ——
     * 用户的原始反馈是"你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法"。
     *
     * <p>拿不到时返回 {@code data = null}（不是错误）：**"这次给不出结论"是合法状态**，
     * 前端据此隐藏整块，而不是显示一段空话占住页面最显眼的位置。
     */
    /**
     * 市场级综合（资讯雷达顶部"今日信息面"）：近 48h 快讯按主题关联。
     * 服务端 10 分钟缓存，全站共用一份；不可用时返回 data=null（前端隐藏整块）。
     */
    @GetMapping("/market-read")
    public Result<JsonNode> marketRead() {
        return Result.success(newsService.marketRead());
    }

    @GetMapping("/stock-read")
    public Result<String> stockRead(
            @RequestParam String symbol,
            @RequestParam(defaultValue = "90") int days,
            Authentication authentication) {
        return Result.success(newsService.stockRead(authentication.getName(), symbol, days));
    }

    /**
     * 手动触发增量刷新（运维/调试用）：{@code day=YYYYMMDD} 表示"从那天补到今天"，
     * 缺省只补今天。定时采集（{@code NewsIngestJob}）与它共用同一段落库逻辑 ——
     * 定时负责常态供给，手动按钮降级为兜底手段。
     */
    @PostMapping("/refresh")
    public Result<String> refresh(
            @RequestParam(required = false) String day,
            Authentication authentication) {
        log.info("User {} 手动触发资讯增量刷新（day={}）", authentication.getName(), day);
        // 成败都记一行（与定时趟同一心跳键，监控看到的是同一条管道），但失败要如实
        // 抛给用户 —— 心跳只留痕，不吞错。
        try {
            String summary = newsService.refresh(day);
            jobRunService.record(JobRunService.JOB_NEWS_REFRESH, JobRunService.STATUS_OK, summary);
            return Result.success(summary);
        } catch (Exception e) {
            jobRunService.record(JobRunService.JOB_NEWS_REFRESH, JobRunService.STATUS_FAILED,
                    e.toString());
            throw e;
        }
    }

    /**
     * 新资讯计数（资讯页"新消息"横幅）：{@code since} 之后发布的条数。
     *
     * <p>前端记上次浏览时间戳并轮询这里，有新增就提示"N 条新资讯，点击载入"——
     * 仿新闻 App 的红点。只报个数不推内容：轻查询，分钟级轮询也不构成压力。
     */
    @GetMapping("/count-since")
    public Result<Long> countSince(@RequestParam String since) {
        LocalDateTime moment;
        try {
            moment = LocalDateTime.parse(since);
        } catch (DateTimeParseException e) {
            throw new IllegalArgumentException("since 格式不对，要用 ISO 时间（如 2026-10-12T09:00:00）");
        }
        if (moment.isAfter(LocalDateTime.now().plusMinutes(5))) {
            throw new IllegalArgumentException("since 不能是未来时间");
        }
        return Result.success(newsService.countSince(moment));
    }

    /**
     * 取资讯原文正文（首次阅读时懒抓取，之后命中库）。
     *
     * <p>失败用 {@code Result.error} 带回人话原因（不在白名单/JS 渲染页/请求失败），
     * 前端据此降级为"摘要 + 原文链接"—— 抓不到正文不代表这条资讯坏了。
     */
    @PostMapping("/events/{id}/body")
    public Result<String> eventBody(@PathVariable Long id) {
        try {
            return Result.success(newsService.getOrFetchBody(id));
        } catch (IllegalArgumentException e) {
            return Result.error(404, e.getMessage());
        } catch (IllegalStateException e) {
            return Result.error(502, e.getMessage());
        }
    }

    /**
     * 事件线列表（雷达页"事件线"区块）：同一事件的多条报道聚成的故事。
     *
     * <p><b>默认只返回已撰稿的线</b>（卡片 = 标题 + digest 一行，点开看全文）：
     * 未撰稿的线大半是总编判定"信息量不足"的程序性披露，不占页面位置
     * （成员报道仍在信息流里）。{@code includeUnsynthesized=true} 供调试看全量。
     */
    @GetMapping("/topics")
    public Result<List<NewsTopicResponse>> topics(
            @RequestParam(defaultValue = "7") int days,
            @RequestParam(required = false) String symbol,
            @RequestParam(defaultValue = "false") boolean includeClosed,
            @RequestParam(defaultValue = "false") boolean includeUnsynthesized) {
        return Result.success(newsTopicService
                .listTopics(days, symbol, includeClosed, includeUnsynthesized).stream()
                .map(topic -> NewsTopicResponse.from(topic, objectMapper))
                .toList());
    }

    /** 专题详情：事件线本体 + 成员资讯（时间倒序）。 */
    @GetMapping("/topics/{id}")
    public Result<TopicDetailResponse> topicDetail(@PathVariable Long id) {
        NewsTopic topic = newsTopicService.topic(id);
        if (topic == null) {
            return Result.error(404, "事件线不存在");
        }
        return Result.success(new TopicDetailResponse(
                NewsTopicResponse.from(topic, objectMapper),
                newsTopicService.loadMembers(id).stream()
                        .map(event -> NewsEventResponse.from(event, objectMapper))
                        .toList()));
    }

    /**
     * {@code POST /analyze} 的请求体。
     *
     * <p>刻意做成控制器内的 record 而不是新开一个 DTO 文件：它只有两个字段、
     * 只服务这一个端点，单独开文件只会让"这个 DTO 属于谁"更难找
     * （plan 的文件清单里也只列了 {@code NewsEventResponse} 与 {@code NewsSearchRequest}）。
     */
    public record AnalyzeRequest(List<Long> eventIds, Boolean force) {}

    /** 专题详情响应：线本体 + 成员（复用 {@link NewsEventResponse}，前端同一套卡片渲染）。 */
    public record TopicDetailResponse(NewsTopicResponse topic, List<NewsEventResponse> events) {}
}
