package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.time.LocalDateTime;

/** 内部接口：追加一条账本事件（只追加，没有对应的 update/delete 接口）。 */
@Data
public class MemoryEventRequest {

    private Long userId;

    private String sessionKey;

    /** chat_user / chat_bot / strategy / system */
    private String kind;

    private String role;

    private String content;

    private String symbol;

    /** user / model / tool / external */
    private String provenance;

    /** high / medium / low */
    private String trust;

    /** 内容所指时间（可空，M1 由相对时间解析器填充） */
    private LocalDateTime eventTime;

    /** 原始时间措辞，如"去年" */
    private String rawTimePhrase;

    private String meta;
}
