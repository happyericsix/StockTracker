package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.AlertRequest;
import com.happyericsix.stocktracker.dto.AlertResponse;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.AlertRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.List;
import java.util.stream.Collectors;

@Service
public class AlertService {

    private static final Logger log = LoggerFactory.getLogger(AlertService.class);
    private final AlertRepository alertRepo;
    private final UserRepository userRepository;
    private final StockService stockService;

    public AlertService(AlertRepository alertRepo, UserRepository userRepository, StockService stockService) {
        this.alertRepo = alertRepo;
        this.userRepository = userRepository;
        this.stockService = stockService;
    }

    public List<AlertResponse> getAlerts(String username) {
        User user = getUser(username);
        return alertRepo.findByUserId(user.getId()).stream()
                .map(this::toResponse).collect(Collectors.toList());
    }

    @Transactional
    public AlertResponse addAlert(String username, AlertRequest request) {
        User user = getUser(username);
        String conditionType = request.getConditionType();
        Double threshold = request.getThreshold();

        if ("pnl_profit".equals(conditionType)) {
            conditionType = "pnl_percent";
            threshold = (threshold != null) ? Math.abs(threshold) : 10.0;
        } else if ("pnl_loss".equals(conditionType)) {
            conditionType = "pnl_percent";
            threshold = (threshold != null) ? -Math.abs(threshold) : -10.0;
        }

        Alert.AlertBuilder alertBuilder = Alert.builder()
                .stockSymbol(request.getSymbol().toUpperCase())
                .conditionType(conditionType)
                .threshold(threshold)
                .enabled(request.getEnabled() != null ? request.getEnabled() : true)
                .user(user);
        // v1 新字段：仅当请求显式传值才覆盖 @Builder.Default（5min / 0.5 / 2h）。
        // 不能无条件赋值：显式 null 会覆盖默认值并违反 NOT NULL 列约束。
        if (request.getCooldownMinutes() != null) {
            alertBuilder.cooldownMinutes(request.getCooldownMinutes());
        }
        if (request.getResetRatio() != null) {
            alertBuilder.resetRatio(request.getResetRatio());
        }
        if (request.getReArmHours() != null) {
            alertBuilder.reArmHours(request.getReArmHours());
        }
        Alert alert = alertBuilder.build();

        alert = alertRepo.save(alert);
        log.info("User {} added alert for {} (type={}, threshold={})",
                username, request.getSymbol(), conditionType, threshold);
        return toResponse(alert);
    }

    @Transactional
    public AlertResponse updateAlert(String username, Long id, AlertRequest request) {
        User user = getUser(username);
        Alert alert = alertRepo.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("预警不存在"));

        if (request.getSymbol() != null && !request.getSymbol().isBlank()) {
            alert.setStockSymbol(request.getSymbol().toUpperCase());
        }
        if (request.getConditionType() != null) {
            alert.setConditionType(request.getConditionType());
        }
        if (request.getThreshold() != null) {
            alert.setThreshold(request.getThreshold());
        }
        if (request.getEnabled() != null) {
            alert.setEnabled(request.getEnabled());
        }
        // v1 新字段：仅当请求显式传值才改（不传保留原值）
        if (request.getCooldownMinutes() != null) {
            alert.setCooldownMinutes(request.getCooldownMinutes());
        }
        if (request.getResetRatio() != null) {
            alert.setResetRatio(request.getResetRatio());
        }
        if (request.getReArmHours() != null) {
            alert.setReArmHours(request.getReArmHours());
        }

        alert = alertRepo.save(alert);
        log.info("User {} updated alert id={}", username, id);
        return toResponse(alert);
    }

    @Transactional
    public void deleteAlert(String username, Long id) {
        User user = getUser(username);
        Alert alert = alertRepo.findByIdAndUserId(id, user.getId())
                .orElseThrow(() -> new IllegalArgumentException("预警不存在"));
        alertRepo.delete(alert);
        log.info("User {} deleted alert id={} ({})", username, id, alert.getStockSymbol());
    }
    @Transactional
    public void deleteByUserAndSymbol(String username, String symbol) {
        User user = getUser(username);
        List<Alert> alerts = alertRepo.findByUserIdAndStockSymbol(user.getId(), symbol.toUpperCase());
        if (!alerts.isEmpty()) {
            alertRepo.deleteAll(alerts);
            log.info("User {} deleted {} alerts for {}", username, alerts.size(), symbol.toUpperCase());
        }
    }

    private AlertResponse toResponse(Alert a) {
        AlertResponse r = new AlertResponse(a.getId(), a.getStockSymbol(),
                a.getConditionType(), a.getThreshold(), a.getEnabled());
        // v1 字段透传
        r.setArmed(a.getArmed());
        r.setLastEvaluatedAt(a.getLastEvaluatedAt());
        r.setCooldownMinutes(a.getCooldownMinutes());
        r.setResetRatio(a.getResetRatio());
        r.setReArmHours(a.getReArmHours());
        // v3 跟踪止盈字段透传
        r.setHighWatermark(a.getHighWatermark());
        // 股票名称透传（解析失败回退为代码）
        r.setName(stockService.resolveStockName(a.getStockSymbol()));
        return r;
    }

    private User getUser(String username) {
        return userRepository.findByUsername(username)
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));
    }
}
