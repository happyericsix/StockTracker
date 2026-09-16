package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.util.List;

/** 内部接口：批量追加账本事件（工具调用结果就是这么入账的）。 */
@Data
public class MemoryEventsRequest {

    private Long userId;

    private String sessionKey;

    private List<MemoryEventRequest> events;
}
