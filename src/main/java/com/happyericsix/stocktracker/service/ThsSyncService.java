package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.ThsClient;
import com.happyericsix.stocktracker.dto.ThsCredentials;
import com.happyericsix.stocktracker.dto.ThsSelfStocksResponse;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.ThsBinding;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.ThsBindingRepository;
import com.happyericsix.stocktracker.util.AesGcmCipher;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 把同花顺自选股同步进本项目的 {@code favorite_stocks} 表。
 *
 * <h3>三条铁律</h3>
 * <ol>
 *   <li><b>只增不改</b>：已存在的行原样不动，绝不碰用户手填的 buyPrice / quantity</li>
 *   <li><b>不写 buyPrice</b>：同花顺的「加入价」是加自选时的参考价，
 *       不是真实买入成本。写进去会污染 {@code PnlPercentEvaluator} 的盈亏计算</li>
 *   <li><b>底层代码等价去重</b>：库里可能已有裸码 {@code 600519}，
 *       而同花顺给的是 {@code SH600519}，归一化后再比对，避免双行</li>
 * </ol>
 */
@Service
public class ThsSyncService {

    private static final Logger log = LoggerFactory.getLogger(ThsSyncService.class);

    private final ThsClient thsClient;
    private final ThsBindingRepository bindingRepository;
    private final FavoriteStockRepository favoriteStockRepository;
    private final String aesKey;

    public ThsSyncService(ThsClient thsClient,
                          ThsBindingRepository bindingRepository,
                          FavoriteStockRepository favoriteStockRepository,
                          @Value("${ths.aes.key:}") String aesKey) {
        this.thsClient = thsClient;
        this.bindingRepository = bindingRepository;
        this.favoriteStockRepository = favoriteStockRepository;
        this.aesKey = aesKey;
    }

    /** 同步结果摘要。 */
    public record SyncResult(int added, int unchanged, int total, LocalDateTime syncedAt) {}

    /**
     * 同步某用户的自选股。
     *
     * <p>失败不抛异常（同步是后台行为，不该阻塞登录），
     * 原因记进 {@code ths_bindings.lastError} 供前端展示。
     *
     * @return 同步摘要；未绑定时返回 null
     */
    @Transactional
    public SyncResult syncFavorites(Long userId) {
        ThsBinding binding = bindingRepository.findByUserId(userId).orElse(null);
        if (binding == null) {
            log.debug("ths sync skipped: user {} has no binding", userId);
            return null;
        }

        List<ThsSelfStocksResponse.Stock> stocks;
        try {
            ThsCredentials cred = decrypt(binding.getSessionEncrypted());
            stocks = thsClient.selfStocks(cred.account(), cred.password());
        } catch (Exception e) {
            // 凭证失效 / 同花顺接口异常 —— 记下来，让前端能提示"请重新扫码"
            String msg = e.getMessage() == null ? "同步失败" : e.getMessage();
            log.warn("ths sync failed for user {}: {}", userId, msg);
            binding.setLastError(msg);
            bindingRepository.save(binding);
            return null;
        }

        // 现有股票的「底层代码」集合，用于等价去重
        List<FavoriteStock> existing = favoriteStockRepository.findByUserId(userId);
        Set<String> existingBare = new HashSet<>();
        for (FavoriteStock f : existing) {
            existingBare.add(bareCode(f.getStockSymbol()));
        }

        int added = 0;
        int unchanged = 0;
        for (ThsSelfStocksResponse.Stock s : stocks) {
            String symbol = s.toProjectSymbol();        // SH688023
            String bare = bareCode(symbol);             // 688023

            if (existingBare.contains(bare)) {
                unchanged++;
                continue;   // 已存在 → 原样不动（第 1 条铁律）
            }

            favoriteStockRepository.save(FavoriteStock.builder()
                    .stockSymbol(symbol)
                    .user(binding.getUser())
                    // ⚠️ 刻意不写 buyPrice / quantity / buyDate（第 2 条铁律）
                    //    同花顺的加入价不是成本价，留给用户在 Dashboard 自己填
                    .build());
            existingBare.add(bare);
            added++;
        }

        binding.setLastSyncAt(LocalDateTime.now());
        binding.setLastSyncCount(added);
        binding.setLastError(null);
        bindingRepository.save(binding);

        log.info("ths sync done: user={}, added={}, unchanged={}, total={}",
                userId, added, unchanged, stocks.size());
        return new SyncResult(added, unchanged, stocks.size(), binding.getLastSyncAt());
    }

    /** 绑定状态（读本地库，不调 Python）。 */
    @Transactional(readOnly = true)
    public ThsBinding status(Long userId) {
        return bindingRepository.findByUserId(userId).orElse(null);
    }

    /** 解绑：删除凭证，但**保留**已同步进 favorite_stocks 的股票。 */
    @Transactional
    public void unbind(Long userId) {
        bindingRepository.deleteByUserId(userId);
        log.info("ths binding removed for user {}", userId);
    }

    /**
     * 取「底层代码」：去掉市场前缀并大写，用于等价去重。
     *
     * <p>让 {@code 600519} / {@code SH600519} / {@code sh600519} 都归一成 {@code 600519}。
     * 不这样做的话，用户手工录过 600519，同步时又会插入一行 SH600519，出现双行。
     */
    static String bareCode(String symbol) {
        if (symbol == null) {
            return "";
        }
        String s = symbol.trim().toUpperCase();
        // 去掉常见的市场前缀
        for (String prefix : new String[]{"SH", "SZ", "BJ", "HK", "ZS"}) {
            if (s.startsWith(prefix) && s.length() > prefix.length()) {
                return s.substring(prefix.length());
            }
        }
        return s;
    }

    private ThsCredentials decrypt(String encrypted) {
        return ThsCredentials.fromJson(AesGcmCipher.decrypt(encrypted, aesKey));
    }
}
