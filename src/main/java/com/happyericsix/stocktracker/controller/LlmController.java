package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.LlmUsageService;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * LLM 用量看板（Profile 页"成本"卡片用）。
 * 整站口径：这是运维信息，不做按用户拆分（单运营者站点；拆分属于伪精度）。
 */
@RestController
@RequestMapping("/api/v1/llm")
public class LlmController {

    private final LlmUsageService llmUsageService;

    public LlmController(LlmUsageService llmUsageService) {
        this.llmUsageService = llmUsageService;
    }

    @GetMapping("/usage")
    public Result<Map<String, Object>> usage(@RequestParam(required = false, defaultValue = "30") Integer days) {
        return Result.success(llmUsageService.usage(days == null ? 30 : days));
    }
}
