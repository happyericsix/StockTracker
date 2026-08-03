"""train_models.py - Offline training script for all quant models.

Usage:
    python train_models.py                    # Train default stocks
    python train_models.py 600519 000001     # Train specific stocks
    python train_models.py --all              # Train all tracked stocks

This script:
  1. Fetches historical data via akshare
  2. Trains LightGBM (3 windows) + MiniTransformer + DQNAgent
  3. Saves weights to python-data-service/models/
  
After training, the API will load models from disk (milliseconds)
instead of retraining on every call (10-30 seconds).
"""
import os
import sys
import time
import logging

# Ensure we are in the script directory for relative imports
os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("train_models")

from akshare_client import get_history
from quant_model import (
    MultiWindowPredictor,
    StockPredictor,
    _save_to_disk,
    MODEL_DIR,
)
from deep_models import MiniTransformer, DQNAgent

# Default stocks to train (common A-shares)
DEFAULT_STOCKS = [
    "600519",  # Kweichow Moutai
    "000001",  # Ping An Bank
    "300750",  # CATL
    "000858",  # Wuliangye
    "002594",  # BYD
    "601318",  # Ping An Insurance
    "600036",  # CMB
    "601398",  # ICBC
    "688981",  # SMIC
    "300059",  # East Money
]


def train_stock(symbol: str) -> bool:
    """Train all models for a single stock and save to disk."""
    logger.info("=" * 50)
    logger.info("Training %s ...", symbol)

    # Fetch historical data
    records = get_history(symbol)
    if not records or len(records) < 60:
        logger.warning("%s: insufficient data (%s records), skipping", symbol, len(records) if records else 0)
        return False

    prices = []
    for r in records:
        try:
            prices.append(float(r["close"]))
        except (KeyError, ValueError, TypeError):
            continue

    if len(prices) < 60:
        logger.warning("%s: insufficient valid prices (%s), skipping", symbol, len(prices))
        return False

    logger.info("%s: %s price points loaded", symbol, len(prices))
    t0 = time.time()

    # --- 1. LightGBM MultiWindowPredictor ---
    logger.info("  [1/3] Training LightGBM (3 windows)...")
    multi = MultiWindowPredictor()
    multi.train(prices)
    lgb_pred = multi.predict(prices)
    logger.info("  LightGBM done: consensus=%s, confidence=%s",
                lgb_pred.get("consensus", "?"),
                lgb_pred.get("confidence", "?"))

    # --- 2. MiniTransformer ---
    logger.info("  [2/3] Training MiniTransformer...")
    tf = MiniTransformer()
    tf_prices = prices[-200:] if len(prices) > 200 else prices
    p = StockPredictor()
    X_tf, y_tf, _ = p._prepare_features(tf_prices)
    tf_result = None
    if X_tf is not None and len(X_tf) >= 35:
        tf.train(tf_prices, X_tf, y_tf, epochs=100, lr=0.005)
        tf_pred = tf.predict(X_tf)
        if tf_pred is not None:
            tf_result = round(float(tf_pred * 100), 2)
            logger.info("  Transformer done: predicted_change=%+.2f%%", tf_result)
        else:
            logger.warning("  Transformer prediction returned None")
    else:
        logger.warning("  Transformer: insufficient features (%s samples)", len(X_tf) if X_tf is not None else 0)

    # --- 3. DQNAgent ---
    logger.info("  [3/3] Training DQNAgent...")
    dqn = DQNAgent()
    dqn_prices = prices[-120:] if len(prices) > 120 else prices
    p2 = StockPredictor()
    X_dqn, y_dqn, _ = p2._prepare_features(dqn_prices)
    dqn_result = None
    if X_dqn is not None and len(dqn_prices) >= 60:
        dqn.train(dqn_prices, X_dqn, y_dqn, episodes=200, lr=0.02)
        dqn_result = dqn.get_strategy()
        if dqn_result:
            logger.info("  DQN done: total_return=%+.2f%%, trades=%s",
                        dqn_result.get("total_return_pct", 0),
                        dqn_result.get("trade_count", 0))
    else:
        logger.warning("  DQN: insufficient data")

    # --- Save to disk ---
    logger.info("  Saving models to %s ...", MODEL_DIR)
    _save_to_disk(symbol, multi, tf, dqn)

    elapsed = time.time() - t0
    logger.info("  %s: ALL DONE in %.1f seconds", symbol, elapsed)
    return True


def main():
    args = sys.argv[1:]

    if not args:
        symbols = DEFAULT_STOCKS
        print("No symbols specified, training default stocks:")
        print("  " + ", ".join(symbols))
        print()
    else:
        symbols = args

    print("Model directory: %s" % MODEL_DIR)
    print("Stocks to train: %s" % len(symbols))
    print()

    success = 0
    failed = 0

    for i, sym in enumerate(symbols, 1):
        print("[%s/%s] %s" % (i, len(symbols), sym))
        try:
            if train_stock(sym):
                success += 1
            else:
                failed += 1
        except Exception as e:
            logger.error("%s: FAILED - %s", sym, e, exc_info=True)
            failed += 1
        print()

    print("=" * 50)
    print("TRAINING COMPLETE")
    print("  Success: %s" % success)
    print("  Failed:  %s" % failed)
    print("  Models saved to: %s" % MODEL_DIR)
    print()
    print("Now restart app.py and models will load from disk in milliseconds!")


if __name__ == "__main__":
    main()
