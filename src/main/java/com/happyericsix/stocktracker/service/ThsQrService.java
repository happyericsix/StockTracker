package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.ThsClient;
import com.happyericsix.stocktracker.dto.ThsCredentials;
import com.happyericsix.stocktracker.dto.ThsQrCreateApiResponse;
import com.happyericsix.stocktracker.dto.ThsQrCreateResponse;
import com.happyericsix.stocktracker.dto.ThsQrPollApiResponse;
import com.happyericsix.stocktracker.dto.ThsQrPollResponse;
import com.happyericsix.stocktracker.entity.Role;
import com.happyericsix.stocktracker.entity.ThsBinding;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.ThsBindingRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.util.AesGcmCipher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.UUID;

/**
 * 同花顺扫码登录业务。
 *
 * <p>职责：把「手机扫码」这件事翻译成「本项目的登录态」。
 *
 * <pre>
 * createQr()   调 Python 生成二维码
 * pollQr()     轮询 → 扫到了就：加密存凭证 → 找/建用户 → 签发 JWT
 * </pre>
 *
 * <p>⚠️ 关键设计：<b>Java 侧不保存扫码会话状态</b>。
 * qrSessionId 本身就是不透明的会话标识，会话状态在 Python 内存里
 * （{@code QR_STORE}），Java 只是原样透传。因此<b>不需要 Redis</b>，
 * 也不用担心多实例部署时的会话共享问题。
 */
@Service
public class ThsQrService {

    private static final Logger log = LoggerFactory.getLogger(ThsQrService.class);

    private final ThsClient thsClient;
    private final ThsBindingRepository bindingRepository;
    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final TokenService tokenService;
    private final String aesKey;

    public ThsQrService(ThsClient thsClient,
                        ThsBindingRepository bindingRepository,
                        UserRepository userRepository,
                        PasswordEncoder passwordEncoder,
                        TokenService tokenService,
                        @Value("${ths.aes.key:}") String aesKey) {
        this.thsClient = thsClient;
        this.bindingRepository = bindingRepository;
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.tokenService = tokenService;
        this.aesKey = aesKey;
    }

    /** 生成二维码。抛出的异常由 Controller 转成友好响应。 */
    public ThsQrCreateResponse createQr() {
        ThsQrCreateApiResponse api = thsClient.createQr();
        if (!api.isSuccess()) {
            throw new RuntimeException(api.error() == null ? "取二维码失败" : api.error());
        }
        ThsQrCreateApiResponse.Data d = api.data();
        return new ThsQrCreateResponse(
                d.qrSessionId(), d.qrUrl(), d.pollIntervalMs(), d.expiresInSec());
    }

    /**
     * 轮询扫码状态。
     *
     * <p>三种结果：
     * <ul>
     *   <li>还没扫 → {@code pending}，前端继续轮询</li>
     *   <li>扫到了 → 建绑定 + 签发 JWT，返回 {@code ok}</li>
     *   <li>会话过期 → {@code expired}，前端提示刷新</li>
     * </ul>
     *
     * @param qrSessionId   Python 侧的扫码会话 id
     * @param currentUserId 当前登录用户 id；<b>未登录（在登录页扫码）时传 null</b>。
     *                      传了 → 这次扫码是"绑定到该用户"；
     *                      没传 → 这次扫码是"登录"（找不到已有绑定就自动建号）。
     */
    @Transactional
    public ThsQrPollResponse pollQr(String qrSessionId, Long currentUserId) {
        ThsQrPollApiResponse api;
        try {
            api = thsClient.pollQr(qrSessionId);
        } catch (RuntimeException e) {
            // Python 侧 404 表示会话已过期 —— 这不是"系统故障"，是正常的超时，
            // 所以转成 expired 让前端提示刷新，而不是抛 500
            String msg = e.getMessage() == null ? "" : e.getMessage();
            if (msg.contains("过期") || msg.contains("不存在")) {
                return ThsQrPollResponse.expired(msg);
            }
            throw e;
        }

        if (api.isPending()) {
            return ThsQrPollResponse.pending();
        }
        if (!api.isConfirmed()) {
            // ok=true 但状态既不是 pending 也不是 ok —— 理论上不该发生
            return ThsQrPollResponse.expired(
                    api.error() == null ? "扫码状态异常，请重新生成二维码" : api.error());
        }

        ThsQrPollApiResponse.Session session = api.data().session();
        if (session == null || session.account() == null || session.password() == null) {
            throw new RuntimeException("扫码成功但未返回凭证，请重新扫码");
        }

        // ① 先把绑定存下来，再发令牌。
        //    顺序不能反 —— 如果先发令牌、用户当场关掉浏览器，凭证就丢了，下次还得重扫。
        UserResolution resolution = resolveUser(session.account(), currentUserId);
        saveBinding(resolution.user(), session);

        // ② 签发本项目自己的 JWT（不透传同花顺凭证）
        String token = issueToken(resolution.user());

        log.info("ths qr login ok: account={}, username={}, firstLogin={}, boundToCurrentUser={}",
                mask(session.account()), resolution.user().getUsername(),
                resolution.firstLogin(), currentUserId != null);
        return ThsQrPollResponse.ok(token, resolution.user().getUsername(), resolution.firstLogin());
    }

    /**
     * 决定这次扫码绑到哪个用户。
     *
     * <p>三种情况，对应「扫码既能登录、也能绑定」的设计：
     * <ol>
     *   <li><b>已登录时扫码</b>（{@code currentUserId != null}）→ 绑定到当前用户。
     *       这就是"先登录再扫码"，用来把同花顺账号挂到已有账号上。</li>
     *   <li><b>未登录，但这个同花顺账号之前绑过</b> → 直接登进那个账号。
     *       ★ 这条是关键：它保证"绑过一次之后，以后扫码登录都进同一个账号"，
     *       而不是每次扫码都新建一个。</li>
     *   <li><b>未登录，且从没绑过</b> → 自动建号（分配一个新 id），扫完直接进。</li>
     * </ol>
     *
     * @param thsAccount    同花顺账号（如 mx_7miffcx00）
     * @param currentUserId 当前登录用户 id；未登录传 null
     */
    private UserResolution resolveUser(String thsAccount, Long currentUserId) {
        // 情况 1：已登录 → 绑到当前用户（"先登录再扫码"）
        if (currentUserId != null) {
            User u = userRepository.findById(currentUserId)
                    .orElseThrow(() -> new RuntimeException("当前登录用户不存在，请重新登录"));
            return new UserResolution(u, false);
        }

        // 情况 2：未登录，但该同花顺账号已有绑定 → 登进那个账号
        User existing = findUserByThsAccount(thsAccount);
        if (existing != null) {
            return new UserResolution(existing, false);
        }

        // 情况 3：全新账号 → 自动建号
        String suffix = thsAccount.length() > 4
                ? thsAccount.substring(thsAccount.length() - 4) : thsAccount;
        String username = "ths_" + suffix;
        // 防止极小概率的用户名撞车
        if (userRepository.existsByUsername(username)) {
            username = username + "_" + UUID.randomUUID().toString().substring(0, 4);
        }

        User user = User.builder()
                .username(username)
                // 随机密码 + 哈希存储：这个账号只能通过扫码登录，
                // 密码登录入口即使被猜到也用不了
                .password(passwordEncoder.encode(UUID.randomUUID().toString()))
                .email(username + "@stocktracker.local")
                .role(Role.USER)
                .build();
        userRepository.save(user);

        log.info("ths first login: auto-created user {}", username);
        return new UserResolution(user, true);
    }

    /**
     * 查「这个同花顺账号之前绑过哪个用户」。
     *
     * <p>这是「扫码登录」能认出老账号的关键 —— 没有它，每次扫码都会新建一个用户。
     *
     * <p>实现说明：同花顺账号是加密存在 {@code session_encrypted} 里的，
     * <b>无法用 SQL 直接查</b>（密文不可比较），所以只能逐条解密比对。
     * 个人自用场景下绑定记录极少（通常 1~2 条），这个开销可以忽略；
     * 如果将来真有多租户需求，应该额外加一列存账号的<b>哈希</b>用于索引查询。
     *
     * @return 匹配到的用户；没绑过返回 null
     */
    private User findUserByThsAccount(String thsAccount) {
        for (ThsBinding b : bindingRepository.findAll()) {
            if (b.getSessionEncrypted() == null) {
                continue;
            }
            try {
                ThsCredentials cred = ThsCredentials.fromJson(
                        AesGcmCipher.decrypt(b.getSessionEncrypted(), aesKey));
                if (thsAccount.equals(cred.account())) {
                    return b.getUser();
                }
            } catch (Exception e) {
                // 解密失败通常是 ths.aes.key 被改过 —— 跳过这条，不要让整个登录流程挂掉
                log.warn("ths binding decrypt failed (id={}): {}", b.getId(), e.getMessage());
            }
        }
        return null;
    }

    /** 加密保存（或更新）凭证。 */
    private void saveBinding(User user, ThsQrPollApiResponse.Session session) {
        String plain = new ThsCredentials(session.account(), session.password()).toJson();
        String encrypted = AesGcmCipher.encrypt(plain, aesKey);

        ThsBinding binding = bindingRepository.findByUserId(user.getId())
                .orElseGet(() -> ThsBinding.builder().user(user).build());

        binding.setSessionEncrypted(encrypted);
        // expireTime=0 表示手机端没勾「30 天免登录」，存 null（策略是"先试再说"）
        binding.setExpireTime(session.expireTime() > 0
                ? LocalDateTime.ofInstant(
                        Instant.ofEpochSecond(session.expireTime()), ZoneId.systemDefault())
                : null);
        // 换了凭证，之前可能的错误状态清掉
        binding.setLastError(null);

        bindingRepository.save(binding);
        log.info("ths binding saved: userId={}, expireTime={}",
                user.getId(), binding.getExpireTime());
    }

    /** 用本项目自己的机制签发 JWT。 */
    private String issueToken(User user) {
        Authentication auth = new UsernamePasswordAuthenticationToken(
                user.getUsername(),
                null,
                List.of(new SimpleGrantedAuthority("ROLE_" + user.getRole().name())));
        return tokenService.generateToken(auth);
    }

    /** 日志脱敏。 */
    private static String mask(String account) {
        return account == null ? "?"
                : (account.length() > 6 ? account.substring(0, 6) + "***" : "***");
    }

    private record UserResolution(User user, boolean firstLogin) {}
}
