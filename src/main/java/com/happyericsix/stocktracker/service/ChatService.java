package com.happyericsix.stocktracker.service;

import tools.jackson.databind.JsonNode;
import com.happyericsix.stocktracker.dto.MessageResponse;
import com.happyericsix.stocktracker.dto.StrategyResponse;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;
import java.util.HashMap;
import java.util.Map;

/**
 * 聊天机器人服务：异步调用 Python LLM（/api/v1/agent/chat），
 * 回复直接落库 + SSE 推送。不依赖 MessageService，避免循环依赖。
 */
@Service
public class ChatService {

    private static final Logger log = LoggerFactory.getLogger(ChatService.class);

    private final WebClient llmWebClient;
    private final MessageRepository messageRepo;
    private final UserRepository userRepository;
    private final SseEmitterService sseService;
    private final StrategyService strategyService;

    public ChatService(WebClient llmWebClient, MessageRepository messageRepo,
                       UserRepository userRepository, SseEmitterService sseService,
                       StrategyService strategyService) {
        this.llmWebClient = llmWebClient;
        this.messageRepo = messageRepo;
        this.userRepository = userRepository;
        this.sseService = sseService;
        this.strategyService = strategyService;
    }

    /** LLM 单次调用最长 60 秒，超时兜底回复 */
    @Async
    public void processAsync(Long userId, String username, String text) {
        try {
            JsonNode resp = callLlm(username, text);

            JsonNode replies = resp != null ? resp.get("replies") : null;
            if (replies == null || !replies.isArray() || replies.isEmpty()) {
                saveBotReply(userId, "抱歉，我暂时没有理解你的问题，换个问法试试？");
                return;
            }
            for (JsonNode reply : replies) {
                String content = reply.asText();
                if (content != null && !content.isBlank()) {
                    saveBotReply(userId, content);
                }
            }

            JsonNode strategyJson = resp != null ? resp.get("strategy_json") : null;
            if (strategyJson != null && strategyJson.isObject()) {
                createAndBacktestFromAgent(userId, username, strategyJson);
            }
        } catch (Exception e) {
            log.error("LLM 调用失败 userId={}: {}", userId, e.getMessage());
            saveBotReply(userId, "智能助手暂时不可用，请稍后再试。");
        }
    }

    /** 调用 agent 聊天接口；独立成包可见方法，方便单测替换远程依赖。 */
    JsonNode callLlm(String username, String text) {
        Map<String, String> body = new HashMap<>();
        body.put("user_id", username);
        body.put("message", text);

        return llmWebClient.post()
                .uri("/api/v1/agent/chat")
                .bodyValue(body)
                .retrieve()
                .bodyToMono(JsonNode.class)
                .timeout(Duration.ofSeconds(60))
                .block();
    }

    private void saveBotReply(Long userId, String content) {
        saveBotReply(userId, content, null, null);
    }

    private void saveBotReply(Long userId, String content, String relatedSymbol, String metadata) {
        User user = userRepository.findById(userId).orElse(null);
        if (user == null) {
            log.warn("推送回复失败：用户 {} 不存在", userId);
            return;
        }
        Message message = Message.builder()
                .user(user)
                .type("CHAT_BOT")
                .content(content)
                .relatedSymbol(relatedSymbol)
                .metadata(metadata)
                .read(false)
                .build();
        message = messageRepo.save(message);
        sseService.push(userId, MessageResponse.from(message));
        log.info("Bot replied to user {}: {}", userId, content);
    }

    private void createAndBacktestFromAgent(Long userId, String username, JsonNode strategyJson) {
        try {
            StrategyResponse strategy = strategyService.createFromAgent(username, strategyJson);
            String metadata = "{\"strategyId\":" + strategy.getId() + "}";
            try {
                JsonNode backtestResult = strategyService.runBacktest(username, strategy.getId());
                saveBotReply(userId, buildBacktestSummary(strategy, backtestResult),
                        strategy.getSymbol(), metadata);
            } catch (Exception backtestException) {
                log.warn("Agent strategy auto backtest failed id={}: {}", strategy.getId(), backtestException.getMessage());
                saveBotReply(userId,
                        "📄 策略「" + strategy.getName() + "」已保存，但自动回测暂时失败，请到策略详情重试。",
                        strategy.getSymbol(), metadata);
            }
        } catch (Exception createException) {
            log.error("Agent strategy save failed userId={}: {}", userId, createException.getMessage());
            saveBotReply(userId, "⚠️ 策略已生成，但保存失败，请稍后再试。");
        }
    }

    private String buildBacktestSummary(StrategyResponse strategy, JsonNode result) {
        JsonNode backtest = result == null ? null : result.get("backtest");
        if (backtest == null || backtest.isNull()) {
            return "📄 策略「" + strategy.getName() + "」已保存；当前暂无可用回测结果，请稍后重试。";
        }

        StringBuilder sb = new StringBuilder();
        sb.append("📈 策略「").append(strategy.getName()).append("」已自动回测：\n");
        sb.append("- 总收益：").append(doubleOr(backtest.get("total_return_pct"), 0)).append("%\n");
        sb.append("- 买入持有：").append(doubleOr(backtest.get("buy_and_hold_return_pct"), 0)).append("%\n");
        sb.append("- 超额收益：").append(doubleOr(backtest.get("excess_return_pct"), 0)).append("%\n");
        sb.append("- 最大回撤：").append(doubleOr(backtest.get("max_drawdown_pct"), 0)).append("%\n");
        sb.append("- 夏普比率：").append(doubleOr(backtest.get("sharpe_ratio"), 0)).append("\n");
        sb.append("- 胜率：").append(doubleOr(backtest.get("win_rate"), 0)).append("%\n");
        sb.append("- 交易笔数：").append((int) doubleOr(backtest.get("trade_count"), 0));
        sb.append("\n可在策略详情查看权益曲线和交易记录。");
        return sb.toString();
    }

    private double doubleOr(JsonNode node, double fallback) {
        if (node == null || !node.isNumber()) {
            return fallback;
        }
        return node.asDouble();
    }
}
