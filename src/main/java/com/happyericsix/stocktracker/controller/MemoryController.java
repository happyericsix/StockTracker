package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.MemoryFactService;
import com.happyericsix.stocktracker.service.MemoryLessonService;
import com.happyericsix.stocktracker.service.MemoryService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 记忆管理接口（面向用户，走 JWT）。
 *
 * <h3>为什么用户必须能看到并管理自己的记忆</h3>
 * 这是记忆系统能不能被信任的前提，也是合规要求：
 * <ul>
 *   <li><b>可查看</b>：用户有权知道系统"记住了我什么"，否则他会觉得被暗中画像；</li>
 *   <li><b>可撤回</b>：说错了、改主意了，要能撤掉（逻辑撤回，历史仍可回溯）；</li>
 *   <li><b>可审核</b>：经验（lessons）默认 pending，只有用户/运营确认后才变成"可信做法"，
 *       这是防止被污染的经验持续生效的那道闸门。</li>
 * </ul>
 *
 * <h3>归属校验</h3>
 * 所有查询与操作都以<b>当前登录用户</b>的 id 为范围，不接受前端传 userId ——
 * 与内部接口（Python 调用的那套）刻意分开：那套靠共享密钥，这套靠 JWT + 归属。
 */
@RestController
@RequestMapping("/api/v1/memory")
@RequiredArgsConstructor
public class MemoryController {

    private final UserRepository userRepository;
    private final MemoryFactService factService;
    private final MemoryLessonService lessonService;
    private final MemoryService memoryService;

    /** 记忆概览：几个数字（多少条事实、多少条经验待确认、多少段摘要） */
    @GetMapping("/overview")
    public Result<Map<String, Object>> overview(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(memoryService.overview(userId));
    }

    /** 当前有效的事实（用户看到的"系统记住了我什么"） */
    @GetMapping("/facts")
    public Result<List<Map<String, Object>>> facts(Authentication authentication,
                                                   @RequestParam(required = false) String factType,
                                                   @RequestParam(required = false, defaultValue = "50") int limit) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(factService.searchFacts(userId, null, null, factType, limit));
    }

    /** 长期画像（稳定身份信息，与注入给模型的那份一致，便于用户核对） */
    @GetMapping("/persona")
    public Result<List<Map<String, Object>>> persona(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(factService.persona(userId));
    }

    /** 某个设置的历史变更链（"这个值以前是什么"） */
    @GetMapping("/facts/history")
    public Result<List<Map<String, Object>>> factHistory(Authentication authentication,
                                                         @RequestParam String subject,
                                                         @RequestParam String predicate) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(factService.factHistory(userId, subject, predicate, 20));
    }

    /** 撤回一条事实：逻辑撤回，历史仍可回溯（见 MemoryFactService.retract） */
    @PostMapping("/facts/{id}/retract")
    public Result<String> retractFact(Authentication authentication, @PathVariable Long id) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return factService.retract(userId, id)
                ? Result.success("已撤回")
                : Result.error(404, "这条记忆不存在");
    }

    /** 经验列表（含"出现过几次"与是否建议升格为 skill） */
    @GetMapping("/lessons")
    public Result<List<Map<String, Object>>> lessons(Authentication authentication,
                                                     @RequestParam(required = false, defaultValue = "50") int limit) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(lessonService.listForUser(userId, limit));
    }

    /** 确认一条经验可信（pending → active）。这是"经验变成可信做法"的唯一入口。 */
    @PostMapping("/lessons/{id}/activate")
    public Result<String> activateLesson(Authentication authentication, @PathVariable Long id) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return lessonService.activate(userId, id)
                ? Result.success("已确认")
                : Result.error(404, "这条经验不存在");
    }

    /** 停用一条经验（active → retired），停用后不再注入 */
    @PostMapping("/lessons/{id}/retire")
    public Result<String> retireLesson(Authentication authentication, @PathVariable Long id) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return lessonService.retire(userId, id)
                ? Result.success("已停用")
                : Result.error(404, "这条经验不存在");
    }

    /** 会话摘要（前情提要）：用户可以看系统是怎么总结自己的 */
    @GetMapping("/episodes")
    public Result<List<Map<String, Object>>> episodes(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        Map<String, Object> corpus = memoryService.indexCorpus(userId);
        Object episodes = corpus.get("episodes");
        List<Map<String, Object>> out = new ArrayList<>();
        if (episodes instanceof List<?> list) {
            for (Object item : list) {
                if (item instanceof Map<?, ?> map) {
                    @SuppressWarnings("unchecked")
                    Map<String, Object> typed = (Map<String, Object>) map;
                    Map<String, Object> copy = new LinkedHashMap<>(typed);
                    copy.remove("id");   // 内部主键不外发
                    out.add(copy);
                }
            }
        }
        return Result.success(out);
    }

    private Long currentUserId(Authentication authentication) {
        if (authentication == null || authentication.getName() == null) {
            return null;
        }
        return userRepository.findByUsername(authentication.getName())
                .map(User::getId)
                .orElse(null);
    }
}
