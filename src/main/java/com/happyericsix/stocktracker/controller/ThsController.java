package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.ThsQrCreateResponse;
import com.happyericsix.stocktracker.dto.ThsQrPollResponse;
import com.happyericsix.stocktracker.dto.ThsStatusResponse;
import com.happyericsix.stocktracker.dto.ThsSyncResult;
import com.happyericsix.stocktracker.entity.ThsBinding;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.ThsQrService;
import com.happyericsix.stocktracker.service.ThsSyncService;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.LocalDateTime;

/**
 * 同花顺扫码登录 + 自选股同步接口。
 *
 * <p>对应设计文档 §6.4。五个端点：
 * <pre>
 * POST   /api/v1/ths/qr/create   生成二维码        （免登录，在 SecurityConfig 白名单）
 * GET    /api/v1/ths/qr/poll     轮询扫码状态      （免登录）
 * POST   /api/v1/ths/sync        手动触发同步      （需 JWT）
 * GET    /api/v1/ths/status      绑定状态摘要      （需 JWT）
 * DELETE /api/v1/ths/bind        解绑              （需 JWT）
 * </pre>
 *
 * <p>薄控制器：所有业务在 {@link ThsQrService} / {@link ThsSyncService} 里。
 * 响应统一用项目的 {@link Result} 包装。
 */
@RestController
@RequestMapping("/api/v1/ths")
@RequiredArgsConstructor
public class ThsController {

    private static final Logger log = LoggerFactory.getLogger(ThsController.class);

    private final ThsQrService thsQrService;
    private final ThsSyncService thsSyncService;
    private final UserRepository userRepository;

    // ==================== 扫码（免登录） ====================

    /** 生成同花顺登录二维码。 */
    @PostMapping("/qr/create")
    public Result<ThsQrCreateResponse> createQr() {
        try {
            return Result.success(thsQrService.createQr());
        } catch (Exception e) {
            log.warn("ths qr create failed: {}", e.getMessage());
            return Result.error(500, e.getMessage() == null ? "取二维码失败" : e.getMessage());
        }
    }

    /**
     * 轮询扫码状态。
     *
     * <p>注意这个接口也会返回 {@code code=200} 的 {@code pending} ——
     * 那是"手机还没扫"的正常状态，前端应按 {@code data.status} 分支处理，
     * 不要看到非 ok 就报错。
     *
     * <p><b>可选认证</b>：这两个扫码端点虽然在 SecurityConfig 里放行（登录页要用），
     * 但如果请求带了有效 JWT，Spring 仍会把用户填进 {@code Authentication}。
     * 于是同一套接口支持两种用法：
     * <ul>
     *   <li>带 JWT（个人中心里扫码）→ 绑定到当前登录用户</li>
     *   <li>不带 JWT（登录页扫码）→ 登录/自动建号</li>
     * </ul>
     */
    @GetMapping("/qr/poll")
    public Result<ThsQrPollResponse> pollQr(@RequestParam String qrSessionId,
                                            Authentication authentication) {
        try {
            // authentication 为 null 表示未登录（登录页扫码）；不为 null 则是"先登录再绑定"
            Long currentUserId = currentUserId(authentication);
            return Result.success(thsQrService.pollQr(qrSessionId, currentUserId));
        } catch (Exception e) {
            log.warn("ths qr poll failed: {}", e.getMessage());
            return Result.error(500, e.getMessage() == null ? "扫码状态查询失败" : e.getMessage());
        }
    }

    // ==================== 同步 / 状态 / 解绑（需 JWT） ====================

    /** 手动触发一次自选股同步。 */
    @PostMapping("/sync")
    public Result<ThsSyncResult> sync(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        ThsSyncService.SyncResult r = thsSyncService.syncFavorites(userId);
        if (r == null) {
            return Result.error(400, "尚未绑定同花顺账号，或凭证已失效（请重新扫码）");
        }
        return Result.success("同步完成",
                new ThsSyncResult(r.added(), r.unchanged(), r.total(), r.syncedAt()));
    }

    /** 绑定状态 + 最后同步摘要（只读本地库，不调 Python）。 */
    @GetMapping("/status")
    public Result<ThsStatusResponse> status(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        ThsBinding b = thsSyncService.status(userId);
        if (b == null) {
            return Result.success(ThsStatusResponse.unbound());
        }
        LocalDateTime expire = b.getExpireTime();
        // expireTime 为 null = 手机端没勾「30 天免登录」。
        // 这不代表凭证不能用（策略是"先试再说"），所以 expired 只是本地参考值。
        boolean expired = expire != null && expire.isBefore(LocalDateTime.now());
        return Result.success(new ThsStatusResponse(
                true, expire, expired, b.getLastSyncAt(), b.getLastSyncCount(), b.getLastError()));
    }

    /** 解绑：删除凭证。已同步进自选股的股票**保留**（那是用户的数据）。 */
    @DeleteMapping("/bind")
    public Result<String> unbind(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        thsSyncService.unbind(userId);
        return Result.success("已解绑（已同步的自选股保留）", null);
    }

    // ==================== 内部 ====================

    /** 从 Authentication 取本项目用户 id。 */
    private Long currentUserId(Authentication authentication) {
        if (authentication == null || authentication.getName() == null) {
            return null;
        }
        return userRepository.findByUsername(authentication.getName())
                .map(User::getId)
                .orElse(null);
    }
}
