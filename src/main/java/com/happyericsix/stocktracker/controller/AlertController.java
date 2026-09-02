package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.AlertRequest;
import com.happyericsix.stocktracker.dto.AlertResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.AlertService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/alerts")
public class AlertController {

    private final AlertService alertService;

    @GetMapping
    public ResponseEntity<List<AlertResponse>> getAlerts(Authentication authentication) {
        return ResponseEntity.ok(alertService.getAlerts(authentication.getName()));
    }

    @PostMapping
    public ResponseEntity<AlertResponse> addAlert(
            @Valid @RequestBody AlertRequest request,
            Authentication authentication) {
        return ResponseEntity.ok(alertService.addAlert(authentication.getName(), request));
    }

    @PutMapping("/{id}")
    public ResponseEntity<AlertResponse> updateAlert(
            @PathVariable Long id,
            @Valid @RequestBody AlertRequest request,
            Authentication authentication) {
        return ResponseEntity.ok(alertService.updateAlert(authentication.getName(), id, request));
    }

    @DeleteMapping("/{id}")
    public ResponseEntity<Result<String>> deleteAlert(
            @PathVariable Long id,
            Authentication authentication) {
        alertService.deleteAlert(authentication.getName(), id);
        return ResponseEntity.ok(Result.success("删除成功"));
    }
}
