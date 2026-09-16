package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.util.List;

/** 内部接口：一次会话巩固抽取出来的全部经验。 */
@Data
public class MemoryLessonsRequest {

    private Long userId;

    private String sessionKey;

    /** 这批经验来自哪些账本事件（可追溯） */
    private List<Long> evidenceEventIds;

    private List<MemoryLessonRequest> lessons;
}
