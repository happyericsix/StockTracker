package com.happyericsix.stocktracker.exception;

import com.happyericsix.stocktracker.dto.Result;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.security.authentication.BadCredentialsException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ControllerAdvice;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;

@ControllerAdvice
public class GlobalExceptionHandler {

    private static final Logger log = LoggerFactory.getLogger(GlobalExceptionHandler.class);

    /**
     * 登录失败（用户名不存在/密码错误）必须给 401 + 明确文案，而不是掉进 500 兜底。
     * 2026-10-05 云端实测：密码输错返回"服务器内部错误"，用户会以为网站坏了；
     * 而这恰恰是用户最常触发的错误路径。不记 ERROR 日志（记了会淹没真异常）。
     */
    @ExceptionHandler(BadCredentialsException.class)
    public ResponseEntity<Result<Void>> handleBadCredentials(BadCredentialsException ex) {
        return ResponseEntity.status(401).body(Result.error(401, "用户名或密码错误"));
    }

    @ExceptionHandler(FavoriteAlreadyExistsException.class)
    public ResponseEntity<Result<Void>> handleFavoriteAlreadyExists(FavoriteAlreadyExistsException ex) {
        return ResponseEntity.badRequest().body(Result.error(400, ex.getMessage()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<Result<Void>> handleIllegalArgument(IllegalArgumentException ex) {
        return ResponseEntity.badRequest().body(Result.error(400, ex.getMessage()));
    }

    @ExceptionHandler(BusinessException.class)
    public ResponseEntity<Result<Void>> handleBusiness(BusinessException ex) {
        return ResponseEntity.status(ex.getCode()).body(Result.error(ex.getCode(), ex.getMessage()));
    }

    /**
     * 参数校验失败：Spring 的默认消息是英文（"must not be blank"）且直接透给前端 ——
     * 2026-10-10 用户反馈"怎么能把后端报错直接拿上来"。常见消息翻译成中文，
     * 未收录的罕见消息保留原文（比错译好），外面包一层"参数不合法"。
     */
    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<Result<Void>> handleValidation(MethodArgumentNotValidException ex) {
        String message = ex.getBindingResult().getFieldErrors().stream()
                .map(e -> e.getField() + "：" + translateValidationMessage(e.getDefaultMessage()))
                .reduce((a, b) -> a + "；" + b)
                .orElse("参数校验未通过");
        return ResponseEntity.badRequest().body(Result.error(400, "参数不合法：" + message));
    }

    private static String translateValidationMessage(String raw) {
        if (raw == null) {
            return "格式不正确";
        }
        if (raw.contains("must not be blank") || raw.contains("must not be null")) {
            return "不能为空";
        }
        if (raw.contains("must be greater than")) {
            return "数值太小";
        }
        if (raw.contains("must be less than")) {
            return "数值太大";
        }
        if (raw.contains("size must be between")) {
            return "长度不在允许范围内";
        }
        if (raw.contains("must be a valid")) {
            return "格式不正确";
        }
        return raw;
    }

    /** 唯一键冲突 / NOT NULL 违反等：此前会落到默认 500 白页 */
    @ExceptionHandler(DataIntegrityViolationException.class)
    public ResponseEntity<Result<Void>> handleDataIntegrity(DataIntegrityViolationException ex) {
        log.warn("Data integrity violation: {}", ex.getMessage());
        return ResponseEntity.status(409).body(Result.error(409, "数据冲突（重复提交或缺少必要字段），请刷新后重试"));
    }

    /** 请求体不是合法 JSON / 类型无法反序列化 */
    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Result<Void>> handleUnreadable(HttpMessageNotReadableException ex) {
        return ResponseEntity.badRequest().body(Result.error(400, "请求体格式错误"));
    }

    /**
     * 路径/查询参数类型不匹配（如 /strategies/undefined/paper/equity 的 id）：
     * 这是**调用方的错**，此前落到兜底 500，监控里看起来像服务端故障——
     * 2026-10-27 模拟盘页资产走势图字段误用（item.id vs item.strategyId）实测
     * 一次页面加载就产生两条"Unhandled exception"。归到 400 让它一眼可辨。
     */
    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<Result<Void>> handleTypeMismatch(MethodArgumentTypeMismatchException ex) {
        log.warn("Request param type mismatch: {} = '{}'", ex.getName(), ex.getValue());
        return ResponseEntity.badRequest().body(Result.error(400,
                String.format("参数 %s 格式错误（期望 %s）", ex.getName(),
                        ex.getRequiredType() != null ? ex.getRequiredType().getSimpleName() : "未知")));
    }

    /** 兜底：任何未预期异常不再泄漏默认 500 白页，统一 Result 信封 */
    @ExceptionHandler(Exception.class)
    public ResponseEntity<Result<Void>> handleGeneric(Exception ex) {
        log.error("Unhandled exception", ex);
        return ResponseEntity.status(500).body(Result.error(500, "服务器内部错误，请稍后重试"));
    }
}