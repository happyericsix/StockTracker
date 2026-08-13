"""train_models.py - Offline training script with MLflow tracking + backtesting.

Usage:
    python train_models.py                    # Train default stocks
    python train_models.py 600519 000001     # Train specific stocks
    python train_models.py --all              # Train all tracked stocks
    python train_models.py --backtest-only    # Only run backtest on existing models

After training, run `mlflow ui` to view experiment dashboard.
"""
import os
import sys
import time
import logging
import json
from pathlib import Path

os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("train_models")

import mlflow
from akshare_client import get_history
from quant_model import (
    MultiWindowPredictor,
    StockPredictor,
    analyze_stock,
    _save_to_disk,
    MODEL_DIR,
)
from deep_models import MiniTransformer, DQNAgent
from mlflow_utils import setup_tracking, log_training_run, log_prediction_accuracy
from backtest import BacktestEngine, run_comprehensive_backtest, backtest_to_llm_context

# Default stocks to train
DEFAULT_STOCKS = [
    "600519", "000001", "300750", "000858",
    "002594", "601318", "600036",
]

# Initialize MLflow
setup_tracking()


def train_stock(symbol: str) -> dict:
    """Train all models for a single stock with MLflow tracking + backtest."""
    logger.info("=" * 50)
    logger.info("Training %s ...", symbol)

    # Fetch historical data
    records = get_history(symbol)
    if not records or len(records) < 60:
        logger.warning("%s: insufficient data (%s records), skipping",
                       symbol, len(records) if records else 0)
        return None

    prices = []
    for r in records:
        try:
            prices.append(float(r["close"]))
        except (KeyError, ValueError, TypeError):
            continue

    if len(prices) < 60:
        logger.warning("%s: insufficient valid prices, skipping", symbol)
        return None

    logger.info("%s: %s price points loaded", symbol, len(prices))
    t0 = time.time()

    with log_training_run(symbol, "all", {"data_points": len(prices)}):

        # --- 1. LightGBM ---
        logger.info("  [1/3] Training LightGBM...")
        multi = MultiWindowPredictor()
        multi.train(prices)
        lgb_pred = multi.predict(prices)

        mlflow.log_metrics({
            "lgb_confidence": lgb_pred.get("confidence", 0),
            "lgb_predicted_change": lgb_pred.get("predicted_change_pct", 0) or 0,
        })
        mlflow.set_tag("lgb_consensus", lgb_pred.get("consensus", "?"))

        # --- 2. MiniTransformer ---
        logger.info("  [2/3] Training MiniTransformer...")
        tf = MiniTransformer()
        tf_prices = prices[-200:] if len(prices) > 200 else prices
        p = StockPredictor()
        X_tf, y_tf, _ = p._prepare_features(tf_prices)
        tf_result = None
        if X_tf is not None and len(X_tf) >= 35:
            tf.train(tf_prices, X_tf, y_tf, epochs=80, lr=0.005)
            tf_pred = tf.predict(X_tf)
            if tf_pred is not None:
                tf_result = round(float(tf_pred * 100), 2)
                mlflow.log_metrics({"tf_predicted_change_pct": tf_result})
        else:
            logger.warning("  Transformer: insufficient features")

        # --- 3. DQN ---
        logger.info("  [3/3] Training DQNAgent...")
        dqn = DQNAgent()
        dqn_prices = prices[-120:] if len(prices) > 120 else prices
        p2 = StockPredictor()
        X_dqn, y_dqn, _ = p2._prepare_features(dqn_prices)
        dqn_result = None
        if X_dqn is not None and len(dqn_prices) >= 60:
            dqn.train(dqn_prices, X_dqn, y_dqn, episodes=150, lr=0.02)
            dqn_result = dqn.get_strategy()
            if dqn_result:
                mlflow.log_metrics({
                    "dqn_return_pct": dqn_result.get("total_return_pct", 0),
                    "dqn_trades": dqn_result.get("trade_count", 0),
                })

        # --- Save to disk ---
        logger.info("  Saving models to %s ...", MODEL_DIR)
        _save_to_disk(symbol, multi, tf, dqn)

        # --- Run analysis for signal ---
        logger.info("  Running comprehensive analysis...")
        analysis = analyze_stock(prices, symbol)

        # --- 4. Backtest ---
        logger.info("  [4/4] Running backtest...")
        engine = BacktestEngine(initial_capital=100_000)
        signal = analysis.get("signal", {})
        predictions = analysis.get("prediction", {})

        bt_results = run_comprehensive_backtest(
            symbol, prices,
            signal=signal,
            predictions=predictions,
            rl_result=dqn_result,
        )

        # Log backtest metrics to MLflow
        for mode, r in bt_results.items():
            if isinstance(r, type) or mode in ("best_mode", "best_return", "buy_and_hold"):
                continue
            if hasattr(r, 'total_return_pct'):
                mlflow.log_metrics({
                    f"bt_{mode}_return_pct": r.total_return_pct,
                    f"bt_{mode}_sharpe": r.sharpe_ratio,
                    f"bt_{mode}_max_dd": r.max_drawdown_pct,
                    f"bt_{mode}_win_rate": r.win_rate,
                })

        # Log best mode
        mlflow.set_tag("best_strategy", bt_results.get("best_mode", "none"))

        # Generate backtest report text
        for mode, r in bt_results.items():
            if hasattr(r, 'total_return_pct'):
                report = engine.report(r)
                logger.info(report)

        # Save backtest results as artifact
        bt_json = {}
        for mode, r in bt_results.items():
            if hasattr(r, 'total_return_pct'):
                bt_json[mode] = {
                    "total_return_pct": r.total_return_pct,
                    "sharpe": r.sharpe_ratio,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "win_rate": r.win_rate,
                    "total_trades": r.total_trades,
                    "excess_return": r.excess_return,
                }

        bt_path = Path(MODEL_DIR) / f"{symbol}_backtest.json"
        bt_path.write_text(json.dumps(bt_json, indent=2, ensure_ascii=False))
        mlflow.log_artifact(str(bt_path))

        elapsed = time.time() - t0
        mlflow.log_metric("training_time_seconds", round(elapsed, 1))
        logger.info("  %s: ALL DONE in %.1f seconds", symbol, elapsed)

        return {
            "symbol": symbol,
            "lgb_pred": lgb_pred,
            "tf_result": tf_result,
            "dqn_result": dqn_result,
            "backtest": bt_results,
        }


def run_backtest_only(symbols: list[str]):
    """Only run backtest on existing trained models."""
    engine = BacktestEngine(initial_capital=100_000)

    for sym in symbols:
        logger.info("Backtesting %s...", sym)
        records = get_history(sym)
        if not records or len(records) < 20:
            continue

        prices = [float(r["close"]) for r in records
                  if r.get("close") and float(r["close"]) > 0]

        analysis = analyze_stock(prices, sym)
        signal = analysis.get("signal", {})
        predictions = analysis.get("prediction", {})

        bt_result = engine.run_signal_backtest(sym, prices, signal)
        logger.info(engine.report(bt_result))

        if predictions:
            bt_pred = engine.run_prediction_backtest(sym, prices, predictions)
            logger.info(engine.report(bt_pred))


def main():
    args = sys.argv[1:]

    if "--backtest-only" in args:
        symbols = [a for a in args if a != "--backtest-only"] or DEFAULT_STOCKS
        run_backtest_only(symbols)
        return

    symbols = [a for a in args if not a.startswith("--")] or DEFAULT_STOCKS
    logger.info("Stocks to train: %s", symbols)

    all_results = {}
    success = 0
    failed = 0

    for i, sym in enumerate(symbols, 1):
        logger.info("[%s/%s] %s", i, len(symbols), sym)
        try:
            result = train_stock(sym)
            if result:
                all_results[sym] = result
                success += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("%s: FAILED - %s", sym, e, exc_info=True)
            failed += 1

    logger.info("=" * 50)
    logger.info("TRAINING COMPLETE - Success: %s, Failed: %s", success, failed)

    # Summary of best strategies
    logger.info("\n=== 最佳策略汇总 ===")
    for sym, r in all_results.items():
        bt = r.get("backtest", {})
        best = bt.get("best_mode", "N/A")
        best_ret = bt.get("best_return", 0)
        logger.info("  %s: 最佳=%s (%.2f%%)", sym, best, best_ret * 100)

    logger.info("\n查看实验面板: mlflow ui --backend-store-uri file:///%s",
                Path(__file__).parent / "mlruns")


if __name__ == "__main__":
    main()
