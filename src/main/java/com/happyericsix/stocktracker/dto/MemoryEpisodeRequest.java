package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

/**
 * 内部接口：Python 侧生成好摘要后回传的情情节记忆。
 *
 * 版本号由 Java 侧分配（同一 sessionKey 现有最大版本 + 1），Python 不需要也不该自己算 ——
 * 避免两端对"当前版本是哪个"产生分歧，这个坑和历史上 user_id/user_name 那次是同一类。
 */
@Data
public class MemoryEpisodeRequest {

    private Long userId;

    private String sessionKey;

    /** 前情提要正文 */
    private String summary;

    private List<String> keyPoints;

    /** 不确定的信息放这里（疑问句），不要写进 summary 当事实 */
    private List<String> openQuestions;

    /** 涉及的标的/策略等实体，如 {"symbols": ["600519"]} */
    private Map<String, Object> entities;

    /** 来源账本事件 id，摘要必须可追溯到原始事件 */
    private List<Long> sourceEventIds;

    private String model;

    private LocalDateTime startedAt;

    private LocalDateTime endedAt;
}
