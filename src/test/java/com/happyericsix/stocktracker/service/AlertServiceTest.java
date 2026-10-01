package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.AlertRequest;
import com.happyericsix.stocktracker.dto.AlertResponse;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.AlertRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Spy;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 预警旧值（pnl_profit/pnl_loss）归一与开关切换契约。
 *
 * <p>这两条路径此前各有一个"从未跑通"的断点：前端下拉提交 pnl_profit/pnl_loss
 * 被 AlertRequest 的 TYPE_REGEX 在 @Valid 层拒成 400；前端开关只发 {enabled}
 * 又被 symbol/@NotBlank、threshold/@NotNull 拒成 400。本用例组钉住修复后的契约。
 */
@ExtendWith(MockitoExtension.class)
class AlertServiceTest {

    @Mock
    private AlertRepository alertRepo;
    @Mock
    private UserRepository userRepository;
    @Mock
    private StockService stockService;
    @InjectMocks
    private AlertService alertService;

    private User owner() {
        return User.builder().id(7L).username("alice")
                .password("secret").email("alice@example.com").build();
    }

    private AlertRequest request(String symbol, String type, Double threshold, Boolean enabled) {
        AlertRequest req = new AlertRequest();
        req.setSymbol(symbol);
        req.setConditionType(type);
        req.setThreshold(threshold);
        req.setEnabled(enabled);
        return req;
    }

    @Test
    void addAlertNormalizesLegacyPnlProfit() {
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner()));
        when(alertRepo.save(any(Alert.class))).thenAnswer(inv -> inv.getArgument(0));

        alertService.addAlert("alice", request("600519", "pnl_profit", 8.0, null));

        ArgumentCaptor<Alert> captor = ArgumentCaptor.forClass(Alert.class);
        verify(alertRepo).save(captor.capture());
        assertEquals("pnl_percent", captor.getValue().getConditionType());
        assertEquals(8.0, captor.getValue().getThreshold());
    }

    @Test
    void addAlertNormalizesLegacyPnlLossToNegative() {
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner()));
        when(alertRepo.save(any(Alert.class))).thenAnswer(inv -> inv.getArgument(0));

        // 用户手滑输入负数也必须归一成"负数止损"，符号由类型决定而不是用户输入
        alertService.addAlert("alice", request("600519", "pnl_loss", -5.0, null));

        ArgumentCaptor<Alert> captor = ArgumentCaptor.forClass(Alert.class);
        verify(alertRepo).save(captor.capture());
        assertEquals("pnl_percent", captor.getValue().getConditionType());
        assertEquals(-5.0, captor.getValue().getThreshold());
    }

    @Test
    void updateAlertNormalizesLegacyPairTogether() {
        Alert existing = Alert.builder().id(42L).stockSymbol("600519")
                .conditionType("price_above").threshold(1500.0).enabled(true)
                .user(owner()).build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner()));
        when(alertRepo.findByIdAndUserId(42L, 7L)).thenReturn(Optional.of(existing));
        when(alertRepo.save(any(Alert.class))).thenAnswer(inv -> inv.getArgument(0));

        AlertResponse res = alertService.updateAlert("alice", 42L,
                request("600519", "pnl_loss", 5.0, null));

        assertEquals("pnl_percent", res.getConditionType());
        // 类型与阈值必须一起翻译：pnl_loss 落库为负数
        assertEquals(-5.0, res.getThreshold());
    }

    @Test
    void updateAlertLegacyTypeWithoutThresholdKeepsPairUntouched() {
        // 只传旧类型、不带阈值：宁可整对不动，也不能把"止损"写成正阈值的"止盈"
        Alert existing = Alert.builder().id(42L).stockSymbol("600519")
                .conditionType("pnl_percent").threshold(-5.0).enabled(true)
                .user(owner()).build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner()));
        when(alertRepo.findByIdAndUserId(42L, 7L)).thenReturn(Optional.of(existing));
        when(alertRepo.save(any(Alert.class))).thenAnswer(inv -> inv.getArgument(0));

        AlertResponse res = alertService.updateAlert("alice", 42L,
                request("600519", "pnl_loss", null, null));

        assertEquals("pnl_percent", res.getConditionType());
        assertEquals(-5.0, res.getThreshold());
    }

    @Test
    void updateAlertToggleSendsCanonicalFieldsAndOnlyFlipsEnabled() {
        // 前端开关现在带全规范字段（值取自上次响应，等于不变），后端只应改 enabled
        Alert existing = Alert.builder().id(42L).stockSymbol("600519")
                .conditionType("pnl_percent").threshold(-5.0).enabled(true)
                .cooldownMinutes(30).user(owner()).build();
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner()));
        when(alertRepo.findByIdAndUserId(42L, 7L)).thenReturn(Optional.of(existing));
        when(alertRepo.save(any(Alert.class))).thenAnswer(inv -> inv.getArgument(0));

        AlertResponse res = alertService.updateAlert("alice", 42L,
                request("600519", "pnl_percent", -5.0, false));

        assertEquals(false, res.getEnabled());
        assertEquals("pnl_percent", res.getConditionType());
        assertEquals(-5.0, res.getThreshold());
        assertEquals(30, res.getCooldownMinutes());
    }
}
