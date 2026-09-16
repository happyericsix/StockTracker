import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from quant_model import StockPredictor, analyze_stock
from backtest import BacktestEngine
from deep_models import DQNAgent


def make_prices(n=80):
    return [100 * (1 + 0.01) ** i for i in range(n)]


def test_prepare_features_keeps_latest_row_aligned():
    prices = make_prices()
    predictor = StockPredictor(window_days=60)

    X, y, cols = predictor._prepare_features(prices)

    assert X is not None and y is not None
    assert len(X) == len(prices)
    assert np.isnan(y[-1]), "last target must be unknown next-day return"
    assert not np.isnan(y[-2]), "second-to-last target must be realized"

    ret1_idx = cols.index("ret_1")
    expected_last_ret = prices[-1] / prices[-2] - 1
    assert abs(float(X[-1, ret1_idx]) - expected_last_ret) < 1e-9


def test_train_uses_only_realized_targets():
    prices = make_prices()
    predictor = StockPredictor(window_days=60)
    X, y, cols = predictor._prepare_features(prices)
    valid = ~np.isnan(y)

    assert valid.sum() == len(prices) - 1
    assert not valid[-1]


def test_dqn_updates_weights_and_returns_real_equity():
    prices = make_prices()
    agent = DQNAgent(state_dim=27, hidden=8)
    before = agent.W1.copy()

    assert agent.train(prices, None, None, episodes=3) is True
    assert not np.allclose(before, agent.W1)

    strategy = agent.get_strategy()
    assert strategy is not None
    assert isinstance(strategy["equity_curve"], list)
    assert len(strategy["equity_curve"]) >= 2
    assert isinstance(strategy["total_return_pct"], float)


def test_rl_backtest_rejects_fabricated_equity():
    engine = BacktestEngine()
    result = engine.run_rl_backtest("TEST", make_prices(), {"total_return_pct": 25.0})

    assert result.total_return == 0.0
    assert result.trade_log == []


def test_prediction_sim_accepts_real_direction_names():
    prices = make_prices()
    engine = BacktestEngine()

    bullish = {"consensus": "bullish", "confidence": "high"}
    equity, trades, daily_rets = engine._simulate_prediction(prices, bullish)

    assert len(equity) == len(prices)
    assert len(trades) >= 1
    assert len(daily_rets) == len(prices) - 1

    bearish = {"consensus": "bearish", "confidence": "medium"}
    equity, trades, daily_rets = engine._simulate_prediction(prices, bearish)
    assert len(equity) == len(prices)


def test_signal_sim_uses_only_past_prices(monkeypatch):
    import quant_model

    prices = make_prices()
    calls = []

    def fake_generate_signal(history):
        calls.append(len(history))
        return {"score": 20, "signal": "bullish"}

    monkeypatch.setattr(quant_model, "generate_signal", fake_generate_signal)
    engine = BacktestEngine()
    engine._simulate_signal(prices, {"score": 20, "signal": "bullish"})

    assert calls == list(range(20, len(prices)))
    assert max(calls) <= len(prices) - 1


def test_analyze_stock_does_not_expose_models_by_default():
    result = analyze_stock(make_prices(), "TEST")

    assert "signal" in result
    assert "indicators" in result
    assert "prediction" not in result
    assert "transformer" not in result
    assert "rl_strategy" not in result
