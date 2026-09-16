"""
backtest.py — Trading backtesting framework for stock prediction models.

Modes:
  1. signal-based  — uses generate_signal() buy/sell/hold signals
  2. prediction-based — uses MultiWindowPredictor output
  3. rl-based — uses DQN strategy trades

Usage:
    from backtest import BacktestEngine

    engine = BacktestEngine(initial_capital=100000, commission=0.001)
    result = engine.run_signal_backtest("600519", prices, signal_result)
    print(engine.report(result))
"""

import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """回测结果。"""
    symbol: str
    mode: str                       # "signal" | "prediction" | "rl" | "buy_and_hold"
    initial_capital: float
    final_value: float
    total_return: float             # 总收益率
    total_return_pct: float         # 总收益率%
    annualized_return: float        # 年化收益率
    sharpe_ratio: float             # 夏普比率
    max_drawdown: float             # 最大回撤
    max_drawdown_pct: float         # 最大回撤%
    calmar_ratio: float             # 卡玛比率 (年化收益/最大回撤)
    win_rate: float                 # 胜率
    total_trades: int               # 总交易次数
    profit_trades: int              # 盈利交易次数
    avg_profit_per_trade: float     # 每笔平均盈利
    avg_loss_per_trade: float       # 每笔平均亏损
    profit_factor: float            # 盈亏比
    buy_and_hold_return: float      # 买入持有收益率
    excess_return: float            # 超额收益（相比买入持有）
    equity_curve: list = field(default_factory=list)       # 净值曲线
    trade_log: list = field(default_factory=list)          # 交易记录
    daily_returns: list = field(default_factory=list)      # 每日收益率
    start_date: str = ""
    end_date: str = ""
    data_points: int = 0


class BacktestEngine:
    """
    回测引擎。

    Args:
        initial_capital: 初始资金
        commission: 手续费率（默认千分之一）
        slippage: 滑点（默认0.001即千分之一）
        risk_free_rate: 无风险利率（用于夏普比率，默认3%）
    """

    def __init__(self, initial_capital: float = 100_000,
                 commission: float = 0.001, slippage: float = 0.001,
                 risk_free_rate: float = 0.03):
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.risk_free_rate = risk_free_rate

    # ==================== Signal Backtest ====================

    def run_signal_backtest(self, symbol: str, prices: list,
                            signal_result: dict) -> BacktestResult:
        """
        基于技术信号的回测。

        Args:
            symbol: 股票代码
            prices: 收盘价列表（旧→新）
            signal_result: generate_signal() 的输出，含 signal 字段
        """
        if len(prices) < 20:
            return self._empty_result(symbol, "signal")

        buy_and_hold = self._buy_and_hold_return(prices)
        equity, trades, daily_rets = self._simulate_signal(prices, signal_result)

        return self._build_result(
            symbol, "signal", equity, trades, daily_rets,
            buy_and_hold, prices
        )

    def _simulate_signal(self, prices: list, signal: dict):
        """信号交易模拟：每个交易日只用该日之前的历史数据生成信号，避免前视偏差。"""
        prices = np.array(prices, dtype=float)
        n = len(prices)
        cash = self.initial_capital
        shares = 0.0
        equity = [self.initial_capital]
        trades = []
        daily_returns = []

        for i in range(1, n):
            price = prices[i]

            score = 0
            if i >= 20:
                try:
                    from quant_model import generate_signal
                    score = generate_signal(prices[:i].tolist()).get("score", 0)
                except Exception as e:
                    logger.warning("signal generation failed at bar %s: %s", i, e)

            # 交易逻辑：score > 10 买入，score < -10 卖出
            if score > 10 and shares == 0:
                # 买入
                actual_price = price * (1 + self.slippage)
                max_shares = cash * (1 - self.commission) / actual_price
                shares = max_shares
                cost = shares * actual_price * (1 + self.commission)
                cash -= cost
                trades.append({
                    "day": i, "action": "BUY", "price": price,
                    "shares": shares, "value": shares * price + cash,
                })

            elif score < -10 and shares > 0:
                # 卖出
                actual_price = price * (1 - self.slippage)
                cash += shares * actual_price * (1 - self.commission)
                trades.append({
                    "day": i, "action": "SELL", "price": price,
                    "shares": shares, "value": cash,
                })
                shares = 0

            # 记录净值和日收益
            total_value = cash + shares * price
            equity.append(total_value)
            if i > 0 and equity[-2] > 0:
                daily_returns.append((equity[-1] - equity[-2]) / equity[-2])

        # 最终清仓
        if shares > 0:
            total_value = cash + shares * prices[-1]
            equity[-1] = total_value

        return equity, trades, daily_returns

    # ==================== Prediction Backtest ====================

    def run_prediction_backtest(self, symbol: str, prices: list,
                                predictions: dict) -> BacktestResult:
        """
        基于模型预测的回测。

        Args:
            prices: 收盘价列表
            predictions: MultiWindowPredictor.predict() 的输出
                {"predicted_change_pct": ..., "consensus": "up/down", ...}
        """
        if len(prices) < 20:
            return self._empty_result(symbol, "prediction")

        buy_and_hold = self._buy_and_hold_return(prices)
        equity, trades, daily_rets = self._simulate_prediction(prices, predictions)

        return self._build_result(
            symbol, "prediction", equity, trades, daily_rets,
            buy_and_hold, prices
        )

    def _simulate_prediction(self, prices: list, predictions: dict):
        """预测驱动交易：预测涨→买入，预测跌→卖出/空仓。"""
        prices = np.array(prices, dtype=float)
        n = len(prices)
        cash = self.initial_capital
        shares = 0.0
        equity = [self.initial_capital]
        trades = []
        daily_returns = []

        consensus = predictions.get("consensus", "neutral")
        confidence = predictions.get("confidence", "low")
        confident = isinstance(confidence, str) and confidence in ("high", "medium")

        for i in range(1, n):
            price = prices[i]

            if consensus in ("bullish", "up") and confident and shares == 0:
                actual_price = price * (1 + self.slippage)
                max_shares = cash * (1 - self.commission) / actual_price
                shares = max_shares
                cash -= shares * actual_price * (1 + self.commission)
                trades.append({
                    "day": i, "action": "BUY", "price": price,
                    "shares": shares, "value": shares * price + cash,
                })

            elif consensus in ("bearish", "down") and confident and shares > 0:
                actual_price = price * (1 - self.slippage)
                cash += shares * actual_price * (1 - self.commission)
                trades.append({
                    "day": i, "action": "SELL", "price": price,
                    "shares": shares, "value": cash,
                })
                shares = 0

            total_value = cash + shares * price
            equity.append(total_value)
            if equity[-2] > 0:
                daily_returns.append((equity[-1] - equity[-2]) / equity[-2])

        if shares > 0:
            equity[-1] = cash + shares * prices[-1]

        return equity, trades, daily_returns

    # ==================== RL Backtest ====================

    def run_rl_backtest(self, symbol: str, prices: list,
                        rl_result: dict) -> BacktestResult:
        """
        基于 DQN 策略的回测。

        Args:
            rl_result: DQNAgent.get_strategy() 的输出
                {total_return_pct, trade_count, trades: [...], interpretation: "..."}
        """
        if len(prices) < 20 or not rl_result:
            return self._empty_result(symbol, "rl")

        buy_and_hold = self._buy_and_hold_return(prices)
        prices_arr = np.array(prices, dtype=float)
        n = len(prices_arr)

        # DQN 必须返回真实逐日净值；没有净值曲线时视为不可回测，禁止用噪声编造。
        raw_equity = rl_result.get("equity_curve")
        if not isinstance(raw_equity, list) or len(raw_equity) < 2:
            return self._empty_result(symbol, "rl")

        equity = [float(v) for v in raw_equity]
        trades = rl_result.get("trades", [])
        if not isinstance(trades, list):
            trades = []
        daily_rets = []
        for i in range(1, len(equity)):
            if equity[i - 1] > 0:
                daily_rets.append((equity[i] - equity[i - 1]) / equity[i - 1])

        result = self._build_result(
            symbol, "rl", equity, trades, daily_rets,
            buy_and_hold, prices_arr
        )
        result.total_trades = len(trades)
        result.trade_log = trades
        return result

    # ==================== Metrics ====================

    def _build_result(self, symbol: str, mode: str,
                      equity: list, trades: list, daily_rets: list,
                      buy_and_hold: float, prices) -> BacktestResult:
        """计算所有指标并构建结果。"""
        final_value = equity[-1] if equity else self.initial_capital
        total_return = (final_value - self.initial_capital) / self.initial_capital

        # 年化收益率（假设252个交易日）
        n_days = len(daily_rets)
        if n_days > 0:
            annualized = (1 + total_return) ** (252 / max(n_days, 1)) - 1
        else:
            annualized = 0.0

        # 夏普比率
        if n_days > 1 and np.std(daily_rets) > 0:
            daily_rf = self.risk_free_rate / 252
            excess = np.mean(daily_rets) - daily_rf
            sharpe = np.sqrt(252) * excess / np.std(daily_rets)
        else:
            sharpe = 0.0

        # 最大回撤
        peak = equity[0]
        max_dd = 0.0
        for val in equity:
            if val > peak:
                peak = val
            dd = (peak - val) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd

        # 卡玛比率
        calmar = annualized / max_dd if max_dd > 0 else 0.0

        # 交易统计：按 (BUY, SELL) 配对成"已平仓回合"再统计。
        # 旧实现把所有 SELL 都算盈利（单轮往返胜率恒 100%），并把 (SELL,BUY)
        # 相邻对也当一轮亏损参与盈亏比，指标完全失真。
        profits = []
        losses = []
        i = 0
        while i + 1 < len(trades):
            t0 = trades[i]
            t1 = trades[i + 1]
            if t0.get("action") == "BUY" and t1.get("action") == "SELL":
                try:
                    delta = float(t1.get("value") or 0.0) - float(t0.get("value") or 0.0)
                except (TypeError, ValueError):
                    delta = 0.0
                if delta > 0:
                    profits.append(delta)
                else:
                    losses.append(-delta)
                i += 2
            else:
                i += 1

        closed_rounds = len(profits) + len(losses)
        profit_trades = len(profits)
        win_rate = (len(profits) / closed_rounds) if closed_rounds else 0.0
        total_trades = max(len(trades), 1)

        avg_profit = float(np.mean(profits)) if profits else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        profit_factor = (sum(profits) / sum(losses)) if losses else 0.0

        excess_return = total_return - buy_and_hold

        return BacktestResult(
            symbol=symbol,
            mode=mode,
            initial_capital=self.initial_capital,
            final_value=round(final_value, 2),
            total_return=round(total_return, 4),
            total_return_pct=round(total_return * 100, 2),
            annualized_return=round(annualized, 4),
            sharpe_ratio=round(sharpe, 4),
            max_drawdown=round(max_dd, 4),
            max_drawdown_pct=round(max_dd * 100, 2),
            calmar_ratio=round(calmar, 4),
            win_rate=round(win_rate, 4),
            total_trades=total_trades,
            profit_trades=profit_trades,
            avg_profit_per_trade=round(avg_profit, 2),
            avg_loss_per_trade=round(avg_loss, 2),
            profit_factor=round(profit_factor, 4),
            buy_and_hold_return=round(buy_and_hold, 4),
            excess_return=round(excess_return, 4),
            equity_curve=[round(v, 2) for v in equity],
            trade_log=trades,
            daily_returns=daily_rets,
            start_date="",
            end_date="",
            data_points=len(prices),
        )

    def _empty_result(self, symbol: str, mode: str) -> BacktestResult:
        return BacktestResult(
            symbol=symbol, mode=mode,
            initial_capital=self.initial_capital,
            final_value=self.initial_capital,
            total_return=0, total_return_pct=0,
            annualized_return=0, sharpe_ratio=0,
            max_drawdown=0, max_drawdown_pct=0,
            calmar_ratio=0, win_rate=0,
            total_trades=0, profit_trades=0,
            avg_profit_per_trade=0, avg_loss_per_trade=0,
            profit_factor=0, buy_and_hold_return=0,
            excess_return=0,
        )

    def _buy_and_hold_return(self, prices) -> float:
        """买入持有策略收益率。"""
        prices = np.array(prices, dtype=float)
        if len(prices) < 2:
            return 0.0
        return (prices[-1] - prices[0]) / prices[0]

    # ==================== Report ====================

    def report(self, result: BacktestResult) -> str:
        """生成回测报告。"""
        return f"""
{'='*50}
  回测报告: {result.symbol} ({result.mode})
{'='*50}
  数据量:     {result.data_points} 条
  初始资金:   ${result.initial_capital:,.2f}
  最终资金:   ${result.final_value:,.2f}
{'─'*40}
  总收益率:   {result.total_return_pct:+.2f}%
  年化收益:   {result.annualized_return*100:+.2f}%
  买入持有:   {result.buy_and_hold_return*100:+.2f}%
  超额收益:   {result.excess_return*100:+.2f}%
{'─'*40}
  夏普比率:   {result.sharpe_ratio:.3f}
  最大回撤:   {result.max_drawdown_pct:.2f}%
  卡玛比率:   {result.calmar_ratio:.3f}
{'─'*40}
  交易次数:   {result.total_trades}
  胜率:       {result.win_rate*100:.1f}%
  盈亏比:     {result.profit_factor:.2f}
  平均盈利:   ${result.avg_profit_per_trade:,.2f}
  平均亏损:   ${result.avg_loss_per_trade:,.2f}
{'─'*40}
  评级:       {self._grade(result)}
{'='*50}
"""

    def _grade(self, result: BacktestResult) -> str:
        """基于指标给出评级。"""
        score = 0
        if result.total_return_pct > 0:
            score += 2
        if result.excess_return > 0:
            score += 2
        if result.sharpe_ratio > 1.0:
            score += 2
        elif result.sharpe_ratio > 0.5:
            score += 1
        if result.max_drawdown_pct < 20:
            score += 2
        elif result.max_drawdown_pct < 35:
            score += 1
        if result.profit_factor > 1.5:
            score += 2
        elif result.profit_factor > 1.0:
            score += 1

        if score >= 8:
            return "[5/5] 优秀"
        elif score >= 6:
            return "[4/5] 良好"
        elif score >= 4:
            return "[3/5] 一般"
        elif score >= 2:
            return "[2/5] 较差"
        else:
            return "[1/5] 差"


def run_comprehensive_backtest(symbol: str, prices: list,
                               signal: dict = None,
                               predictions: dict = None,
                               rl_result: dict = None,
                               initial_capital: float = 100_000) -> dict:
    """
    一键运行所有可用模式的回测。

    Returns:
        {"signal": BacktestResult, "prediction": ..., "rl": ..., "buy_and_hold": float}
    """
    engine = BacktestEngine(initial_capital=initial_capital)
    results = {}

    if signal:
        results["signal"] = engine.run_signal_backtest(symbol, prices, signal)

    if predictions:
        results["prediction"] = engine.run_prediction_backtest(symbol, prices, predictions)

    if rl_result:
        results["rl"] = engine.run_rl_backtest(symbol, prices, rl_result)

    results["buy_and_hold"] = engine._buy_and_hold_return(prices)

    # 找出最佳策略
    best_mode = None
    best_return = -float("inf")
    for mode, r in results.items():
        if isinstance(r, BacktestResult) and r.total_return > best_return:
            best_return = r.total_return
            best_mode = mode

    results["best_mode"] = best_mode
    results["best_return"] = best_return

    return results


def backtest_to_llm_context(symbol: str, results: dict) -> str:
    """
    将回测结果转换为 LLM 可用的上下文文本，供 DeepSeek 生成解读。

    Args:
        results: run_comprehensive_backtest() 的输出
    """
    parts = [f"## {symbol} 回测结果\n"]

    for mode, r in results.items():
        if mode in ("best_mode", "best_return", "buy_and_hold"):
            continue
        if isinstance(r, BacktestResult):
            parts.append(
                f"**{mode} 策略**: "
                f"收益率 {r.total_return_pct:+.1f}%, "
                f"夏普 {r.sharpe_ratio:.2f}, "
                f"最大回撤 {r.max_drawdown_pct:.1f}%, "
                f"胜率 {r.win_rate*100:.0f}%"
            )

    bh = results.get("buy_and_hold", 0)
    parts.append(f"\n买入持有基准: {bh*100:+.1f}%")
    parts.append(f"\n最佳策略: {results.get('best_mode', 'N/A')}")

    return "\n".join(parts)
