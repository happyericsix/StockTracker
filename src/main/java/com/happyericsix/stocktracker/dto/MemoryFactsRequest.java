package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.util.List;

/** 内部接口：批量写入语义事实（一次会话巩固抽取出来的全部事实）。 */
@Data
public class MemoryFactsRequest {

    private Long userId;

    private String sessionKey;

    /** 这批事实来自哪些账本事件（可追溯） */
    private List<Long> sourceEventIds;

    private List<MemoryFactRequest> facts;
}
