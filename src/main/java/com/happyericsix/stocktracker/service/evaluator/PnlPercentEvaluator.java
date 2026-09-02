package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 盈亏百分比（pnl_percent）：
 *  - 止盈 (threshold > 0)：pnl >= threshold，方向 ABOVE
 *  - 止损 (threshold < 0)：pnl <= threshold，方向 BELOW
 *
 * buyPrice 从 alert.user 自己的 FavoriteStock 里拿（不是从 RefreshedPrice），
 * 保证"用户 A 买 AAPL@$100、用户 B 买 AAPL@$120"时各自用自己的买价
 * 没有买入价或该用户没添加该股票为自选股时直接跳过
 *
 * 边沿触发：armed=true 时条件满足才触发一次，触发后 armed=false
 * 重置：pnl 回落到重置区间 或 经过 reArmHours 小时
 */
@Component
public class PnlPercentEvaluator implements AlertEvaluator {

    private final FavoriteStockRepository favoriteRepo;

    public PnlPercentEvaluator(FavoriteStockRepository favoriteRepo) {
        this.favoriteRepo = favoriteRepo;
    }

    @Override
    public String supportedType() { return "pnl_percent"; }

    @Override
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        Double buyPrice = lookupBuyPrice(alert);
        if (buyPrice == null || buyPrice <= 0) {
            return AlertEvaluator.notTriggered("missing buyPrice");
        }
        double pnl = (data.currentPrice() - buyPrice) / buyPrice * 100.0;
        // 方向由 threshold 正负决定：止盈看 ABOVE，止损看 BELOW
        ArmedResetPolicy.Direction direction = alert.getThreshold() != null && alert.getThreshold() < 0
                ? ArmedResetPolicy.Direction.BELOW
                : ArmedResetPolicy.Direction.ABOVE;
        boolean fire = ArmedResetPolicy.check(alert, pnl, direction, LocalDateTime.now());
        return fire ? AlertEvaluator.triggered(pnl) : AlertEvaluator.notTriggered();
    }

    private Double lookupBuyPrice(Alert alert) {
        if (alert.getUser() == null || alert.getUser().getId() == null) return null;
        List<FavoriteStock> favs = favoriteRepo.findByUserIdAndStockSymbol(
                alert.getUser().getId(), alert.getStockSymbol());
        if (favs.isEmpty()) return null;
        return favs.get(0).getBuyPrice();
    }
}
