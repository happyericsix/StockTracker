"""
quant_model.py —— 量化分析模块
基于技术指标 + 随机森林回归，对个股进行短期预测和信号生成。

依赖: numpy, pandas, scikit-learn
数据源: akshare_client.get_history() 获取的历史K线数据
"""
import logging
from typing import Optional
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
import lightgbm as lgb
from deep_models import MiniTransformer, DQNAgent
import pickle
from pathlib import Path

logger = logging.getLogger(__name__)


# ==================== 技术指标计算 ====================

def ma(prices: list, window: int = 5) -> list:
    """简单移动平均线 (Moving Average)"""
    series = pd.Series(prices)
    return series.rolling(window=window, min_periods=1).mean().tolist()


def rsi(prices: list, window: int = 14) -> list:
    """相对强弱指标 (RSI)"""
    series = pd.Series(prices)
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(window=window, min_periods=1).mean()
    loss = (-delta.clip(upper=0)).rolling(window=window, min_periods=1).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    return rsi_val.fillna(50).tolist()


def macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """MACD 指标 (异同移动平均线)"""
    series = pd.Series(prices)
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    dif = (ema_fast - ema_slow).tolist()
    dea = pd.Series(dif).ewm(span=signal, adjust=False).mean().tolist()
    hist = [dif[i] - dea[i] for i in range(len(dif))]
    return {"dif": dif, "dea": dea, "hist": hist}


def bollinger(prices: list, window: int = 20, num_std: float = 2.0) -> dict:
    """布林带 (Bollinger Bands)"""
    series = pd.Series(prices)
    middle = series.rolling(window=window, min_periods=1).mean()
    std = series.rolling(window=window, min_periods=1).std()
    upper = (middle + num_std * std).tolist()
    mid = middle.tolist()
    lower = (middle - num_std * std).tolist()
    return {"upper": upper, "middle": mid, "lower": lower}


def generate_signal(prices: list) -> dict:
    """综合技术指标生成买卖信号

    返回:
        signal: "买入" / "卖出" / "持有" / "无法判断"
        score: -100 ~ +100 的综合打分
        details: 各指标的判断明细
    """
    if not prices or len(prices) < 20:
        return {"signal": "无法判断", "score": 0, "details": {"reason": "数据不足(需至少20个交易日)"}}

    close = np.array(prices, dtype=float)
    rsi_vals = rsi(prices)
    macd_vals = macd(prices)
    ma_short = ma(prices, 5)
    ma_long = ma(prices, 20)
    boll = bollinger(prices)

    latest = close[-1]
    score = 0.0
    details = {}

    # --- RSI 判断 ---
    rsi_now = rsi_vals[-1]
    if rsi_now < 30:
        score += 30
        details["RSI"] = f"超卖({rsi_now:.1f})，看涨"
    elif rsi_now > 70:
        score -= 30
        details["RSI"] = f"超买({rsi_now:.1f})，看跌"
    else:
        details["RSI"] = f"中性({rsi_now:.1f})"

    # --- MACD 判断 ---
    hist_now = macd_vals["hist"][-1]
    hist_prev = macd_vals["hist"][-2] if len(macd_vals["hist"]) >= 2 else 0
    if hist_now > 0 and hist_prev <= 0:
        score += 25
        details["MACD"] = "金叉，看涨"
    elif hist_now < 0 and hist_prev >= 0:
        score -= 25
        details["MACD"] = "死叉，看跌"
    elif hist_now > 0:
        score += 10
        details["MACD"] = f"多头({hist_now:.2f})"
    else:
        score -= 10
        details["MACD"] = f"空头({hist_now:.2f})"

    # --- 均线判断 ---
    if len(ma_short) >= 20 and len(ma_long) >= 20:
        if ma_short[-1] > ma_long[-1] and ma_short[-2] <= ma_long[-2]:
            score += 20
            details["MA"] = "短期均线上穿长期均线（金叉）"
        elif ma_short[-1] < ma_long[-1] and ma_short[-2] >= ma_long[-2]:
            score -= 20
            details["MA"] = "短期均线下穿长期均线（死叉）"
        elif ma_short[-1] > ma_long[-1]:
            score += 8
            details["MA"] = f"多头排列 (MA5:{ma_short[-1]:.2f} > MA20:{ma_long[-1]:.2f})"
        else:
            score -= 8
            details["MA"] = f"空头排列 (MA5:{ma_short[-1]:.2f} < MA20:{ma_long[-1]:.2f})"

    # --- 布林带判断 ---
    upper = boll["upper"][-1]
    lower = boll["lower"][-1]
    if latest <= lower:
        score += 20
        details["Bollinger"] = f"触及下轨({lower:.2f})，超卖反弹信号"
    elif latest >= upper:
        score -= 20
        details["Bollinger"] = f"触及上轨({upper:.2f})，超买回调信号"
    else:
        pos = (latest - lower) / (upper - lower) * 100
        details["Bollinger"] = f"中轨附近(位置:{pos:.0f}%)"

    # --- 综合判断 ---
    score = max(-100, min(100, score))
    if score >= 40:
        signal = "看涨 📈"
    elif score <= -40:
        signal = "看跌 📉"
    elif score >= 15:
        signal = "偏多 ↗️"
    elif score <= -15:
        signal = "偏空 ↘️"
    else:
        signal = "震荡/持有 ➡️"

    return {"signal": signal, "score": score, "details": details}


# ==================== 机器学习预测 ====================

class StockPredictor:
    """LightGBM ??????????????"""

    def __init__(self, window_days: int = None, label: str = "", seed: int = 42):
        if window_days and window_days <= 60: params = dict(n_estimators=100, max_depth=4, num_leaves=24, min_child_samples=12, learning_rate=0.03, reg_alpha=0.02, reg_lambda=0.5)
        elif window_days and window_days <= 120: params = dict(n_estimators=120, max_depth=4, num_leaves=20, min_child_samples=15, learning_rate=0.03, reg_alpha=0.05, reg_lambda=1.5)
        else: params = dict(n_estimators=150, max_depth=3, num_leaves=15, min_child_samples=25, learning_rate=0.02, reg_alpha=0.15, reg_lambda=2.5)
        params.update(dict(verbose=-1, random_state=seed, n_jobs=-1))
        self.model = lgb.LGBMRegressor(**params)
        self.seed = seed
        self.window_days = window_days
        self.label = label
        self.trained = False

    def _prepare_features(self, prices: list) -> tuple:
        """Build feature matrix. Uses window-appropriate indicator lookback periods.
        short window -> fast indicators, long window -> slow indicators."""
        df = pd.DataFrame({"close": pd.Series(prices, dtype=float)})
        close_diff = df["close"].diff()

        # Determine indicator lookback based on window
        if self.window_days is None:
            ma_fast, ma_slow = 20, 60
            rsi_period = 28
            macd_fast, macd_slow, macd_signal = 24, 52, 18
            boll_period = 40
        elif self.window_days >= 120:
            ma_fast, ma_slow = 10, 30
            rsi_period = 21
            macd_fast, macd_slow, macd_signal = 16, 32, 12
            boll_period = 30
        elif self.window_days >= 60:
            ma_fast, ma_slow = 5, 20
            rsi_period = 14
            macd_fast, macd_slow, macd_signal = 12, 26, 9
            boll_period = 20
        else:
            ma_fast, ma_slow = 3, 10
            rsi_period = 7
            macd_fast, macd_slow, macd_signal = 6, 13, 5
            boll_period = 10

        # ===== Basic features: rolling returns =====
        for lag in [1, 2, 3, 5, 10]:
            df[f"ret_{lag}"] = df["close"].pct_change(lag)

        # ===== Technical indicators (window-adaptive) =====
        df["ma_fast"] = ma(df["close"].tolist(), ma_fast)
        df["ma_slow"] = ma(df["close"].tolist(), ma_slow)
        df["rsi"] = rsi(df["close"].tolist(), rsi_period)
        boll = bollinger(df["close"].tolist(), boll_period)
        df["boll_pos"] = (df["close"] - pd.Series(boll["lower"])) / (pd.Series(boll["upper"]) - pd.Series(boll["lower"])).replace(0, 1)
        macd_vals = macd(df["close"].tolist(), macd_fast, macd_slow, macd_signal)
        df["macd_hist"] = macd_vals["hist"]

        # ===== Process features =====
        df["consecutive_up"] = (close_diff > 0).rolling(5, min_periods=1).sum()
        df["ret_5d"] = df["close"].pct_change(5)
        df["ret_10d"] = df["close"].pct_change(10)
        df["acceleration"] = df["ret_5d"] - df["ret_10d"]
        df["vol_5d"] = df["close"].pct_change().rolling(5, min_periods=3).std()
        df["vol_20d"] = df["close"].pct_change().rolling(20, min_periods=10).std()
        df["vol_regime"] = df["vol_5d"] / (df["vol_20d"] + 1e-9)
        df["max_up_5d"] = close_diff.rolling(5, min_periods=1).max()
        df["max_down_5d"] = close_diff.rolling(5, min_periods=1).min()
        df["ma_divergence"] = (df["ma_fast"] - df["ma_slow"]) / (df["ma_slow"] + 1e-9)
        df["price_range"] = (df["close"].rolling(5).max() - df["close"].rolling(5).min()) / (df["close"] + 1e-9)
        df["close_position"] = (df["close"] - df["close"].rolling(20).min()) / (df["close"].rolling(20).max() - df["close"].rolling(20).min() + 1e-9)

        # Target: next day return
        df["target"] = df["close"].pct_change(1).shift(-1)
        df = df.dropna()

        if len(df) < 20:
            return None, None, None

        feature_cols = [c for c in df.columns if c != "close" and c != "target"]
        X = df[feature_cols].values
        y = df["target"].values
        return X, y, feature_cols

    def train(self, prices: list) -> bool:
        """训练模型"""
        X, y, feature_cols = self._prepare_features(prices)
        if X is None or len(X) < 20:
            logger.warning("训练数据不足")
            return False

        # Temporal split: train on older 80%, validate on recent 20%
        split_idx = int(len(X) * 0.8)
        if split_idx < 10:
            return False
        X_train, X_test = X[:split_idx], X[split_idx:]
        y_train, y_test = y[:split_idx], y[split_idx:]
        self.model.fit(X_train, y_train)
        self.trained = True
        self._feature_names = feature_cols
        self._train_cutoff = split_idx

        train_score = self.model.score(X_train, y_train)
        test_score = self.model.score(X_test, y_test)
        logger.info(f"模型训练完成 - 训练集R²: {train_score:.3f}, 测试集R²: {test_score:.3f}")
        return True

    def predict(self, prices: list) -> Optional[dict]:
        """预测下一交易日涨跌幅"""
        if not self.trained:
            return None

        # ?????????????????????????
        window_prices = prices[-self.window_days:] if self.window_days and len(prices) > self.window_days else prices
        result = self._prepare_features(window_prices)
        if result is None or result[0] is None:
            return None
        X, _, feature_cols = result
        if X is None or len(X) == 0:
            return None

        # 使用最新一天的特征做预测
        latest_features = X[-1].reshape(1, -1)
        pred_return = self.model.predict(latest_features)[0]

        # ?????
        importance = {}
        for name, imp in zip(
            feature_cols,
            self.model.feature_importances_,
        ):
            importance[name] = float(round(imp, 4))

        # ????
        direction = "bullish" if pred_return > 0.005 else "bearish" if pred_return < -0.005 else "neutral"

        return {
            "predicted_change_pct": round(float(pred_return * 100), 2),
            "direction": direction,
            "feature_importance": importance,
        }


# ==================== 主分析函数 ====================



# ==================== ?????? ====================

class MultiWindowPredictor:
    """Three-window predictor: short(60d), mid(120d), long(all)"""

    WINDOWS = [
        (60, "short"),
        (120, "mid"),
        (None, "long"),
    ]

    def __init__(self):
        self.predictors = {}
        for days, label in self.WINDOWS:
            seed = days if days else 500
            self.predictors[label] = StockPredictor(window_days=days, label=label, seed=seed)

    def train(self, prices: list):
        """Train all window predictors."""
        for label, pred in self.predictors.items():
            window_prices = prices[-pred.window_days:] if pred.window_days and len(prices) > pred.window_days else prices
            pred.train(list(window_prices))

    def predict(self, prices: list) -> dict:
        """Return predictions from all 3 windows + consensus signal"""
        results = {}
        directions = []

        for label, pred in self.predictors.items():
            r = pred.predict(list(prices))
            if r is None:
                results[label] = {"error": "insufficient data"}
                continue

            results[label] = {
                "predicted_change_pct": r["predicted_change_pct"],
                "direction": r["direction"],
                "top_features": sorted(
                    r["feature_importance"].items(),
                    key=lambda x: x[1], reverse=True
                )[:3],
            }
            directions.append(r["direction"])

        # Consensus logic
        if len(directions) >= 2 and len(set(directions)) == 1:
            consensus = directions[0]
            confidence = "high"
            advice = "All windows agree, signal reliable"
        elif len(directions) >= 2:
            from collections import Counter
            c = Counter(directions)
            consensus = c.most_common(1)[0][0]
            if c.most_common(1)[0][1] >= 2:
                confidence = "medium"
                advice = "Majority windows agree, take as reference"
            else:
                confidence = "low"
                consensus = "neutral"
                advice = "Windows disagree, suggest wait"
        else:
            confidence = "low"
            consensus = "neutral"
            advice = "Insufficient data"

        return {
            "windows": results,
            "consensus": consensus,
            "confidence": confidence,
            "advice": advice,
        }

# ==================== ???? ====================

import time
import threading

_model_cache: dict = {}       # {symbol: {"models": {...}, "last_train": timestamp, "prices": [...], "prices_len": 0}}
_cache_lock = threading.Lock()
CACHE_TTL = 3600  # 1??????
MODEL_DIR = Path(__file__).parent / 'models'
MODEL_DIR.mkdir(exist_ok=True)
DISK_MODEL_MAX_AGE = 6 * 3600  # Retrain if model older than 6 hours (covers daily market close at 15:00)


def _try_load_disk(symbol: str, prices: list):
    try:
        multi = MultiWindowPredictor()
        all_found = True
        for label in ["short", "mid", "long"]:
            path = MODEL_DIR / (symbol + "_lgb_" + label + ".pkl")
            if path.exists():
                with open(path, "rb") as f:
                    multi.predictors[label] = pickle.load(f)
            else:
                all_found = False
                break
        if not all_found:
            return None

        # Check if model is stale (older than DISK_MODEL_MAX_AGE)
        # This ensures auto-retrain after each trading day's market close (15:00 CST)
        oldest_mtime = min(
            (MODEL_DIR / (symbol + "_lgb_" + label + ".pkl")).stat().st_mtime
            for label in ["short", "mid", "long"]
        )
        age_seconds = time.time() - oldest_mtime
        if age_seconds > DISK_MODEL_MAX_AGE:
            logger.info(
                "Disk model for %s is %.1f hours old, will retrain",
                symbol, age_seconds / 3600,
            )
            return None

        lgb_pred = multi.predict(prices)

        tf_result = None
        tf_path = MODEL_DIR / (symbol + "_transformer.pkl")
        if tf_path.exists():
            tf = MiniTransformer.load(str(tf_path))
            tf_prices = prices[-200:] if len(prices) > 200 else prices
            p = StockPredictor()
            X_tf, y_tf, _ = p._prepare_features(tf_prices)
            if X_tf is not None and len(X_tf) >= tf.seq_len:
                tf_pred_val = tf.predict(X_tf)
                if tf_pred_val is not None:
                    tf_result = {
                        "predicted_change_pct": round(float(tf_pred_val * 100), 2),
                        "note": "Transformer model loaded from disk",
                    }

        dqn_result = None
        dqn_path = MODEL_DIR / (symbol + "_dqn.pkl")
        if dqn_path.exists():
            dqn = DQNAgent.load(str(dqn_path))
            if dqn.trained:
                dqn_result = dqn.get_strategy()

        logger.info("Loaded models from disk for " + symbol)
        return {
            "prediction": lgb_pred,
            "transformer": tf_result,
            "rl_strategy": dqn_result,
        }
    except Exception as e:
        logger.warning("Failed to load models from disk: " + str(e))
        return None


def _save_to_disk(symbol: str, multi: MultiWindowPredictor, tf, dqn):
    try:
        for label, predictor in multi.predictors.items():
            if predictor.trained:
                path = MODEL_DIR / (symbol + "_lgb_" + label + ".pkl")
                with open(path, "wb") as f:
                    pickle.dump(predictor, f)
        if tf is not None and tf.trained:
            tf.save(str(MODEL_DIR / (symbol + "_transformer.pkl")))
        if dqn is not None and dqn.trained:
            dqn.save(str(MODEL_DIR / (symbol + "_dqn.pkl")))
        logger.info("Saved models to disk for " + symbol)
    except Exception as e:
        logger.warning("Failed to save models: " + str(e))


def _get_cached_or_train(symbol: str, prices: list) -> dict:
    """?????????????????"""
    prices_key = len(prices)  # ?????????????

    with _cache_lock:
        cached = _model_cache.get(symbol)

    if cached and cached["prices_len"] == prices_key:
        elapsed = time.time() - cached["last_train"]
        if elapsed < CACHE_TTL:
            logger.info(f"Cache hit: {symbol} (trained {elapsed:.0f}s ago)")
            return cached["models"]

    # Try loading from disk first
    disk_result = _try_load_disk(symbol, prices)
    if disk_result is not None:
        with _cache_lock:
            _model_cache[symbol] = {
                "models": disk_result,
                "last_train": time.time(),
                "prices_len": prices_key,
            }
        return disk_result

    # ?????????????
    logger.info(f"Training models for {symbol}...")
    t0 = time.time()

    # --- LightGBM ---
    multi = MultiWindowPredictor()
    multi.train(prices)
    lgb_pred = multi.predict(prices)

    # --- Transformer ---
    try:
        from deep_models import MiniTransformer
        tf = MiniTransformer()
        tf_prices = prices[-200:] if len(prices) > 200 else prices
        p = StockPredictor()
        X_tf, y_tf, _ = p._prepare_features(tf_prices)
        if X_tf is not None and len(X_tf) >= 35:
            tf.train(tf_prices, X_tf, y_tf, epochs=80, lr=0.005)
            tf_pred_val = tf.predict(X_tf)
            tf_result = {
                "predicted_change_pct": round(float(tf_pred_val * 100), 2) if tf_pred_val else None,
                "note": "Transformer??????????LightGBM??",
            }
        else:
            tf_result = None
    except Exception as e:
        logger.warning(f"Transformer??: {e}")
        tf_result = None

    # --- DQN ---
    try:
        from deep_models import DQNAgent
        dqn = DQNAgent()
        dqn_prices = prices[-120:] if len(prices) > 120 else prices
        p2 = StockPredictor()
        X_dqn, y_dqn, _ = p2._prepare_features(dqn_prices)
        if X_dqn is not None and len(dqn_prices) >= 60:
            dqn.train(dqn_prices, X_dqn, y_dqn, episodes=120, lr=0.02)
            dqn_result = dqn.get_strategy()
        else:
            dqn_result = None
    except Exception as e:
        logger.warning(f"DQN??: {e}")
        dqn_result = None

    models = {
        "prediction": lgb_pred,
        "transformer": tf_result,
        "rl_strategy": dqn_result,
    }

    with _cache_lock:
        _model_cache[symbol] = {
            "models": models,
            "last_train": time.time(),
            "prices_len": prices_key,
        }

    # Save to disk for future fast loading
    _save_to_disk(symbol, multi, tf, dqn)

    elapsed = time.time() - t0
    logger.info(f"Training complete for {symbol}: {elapsed:.1f}s")
    return models



def analyze_stock(prices: list, symbol: str = "") -> dict:
    """对一只股票进行完整量化分析

    Args:
        prices: 收盘价列表（按日期从旧到新）
        symbol: 股票代码（可选，仅用于日志）

    Returns:
        包含技术信号、预测、统计信息的字典
    """
    if not prices or len(prices) < 5:
        return {"symbol": symbol, "error": "数据不足"}

    close = [float(p) for p in prices]

    # --- 基础统计 ---
    latest = close[-1]
    prev = close[-2] if len(close) >= 2 else close[-1]
    change_pct = ((latest - prev) / prev) * 100
    high = max(close)
    low = min(close)
    avg_price = np.mean(close)
    volatility = np.std(close) / avg_price * 100 if avg_price > 0 else 0

    stats = {
        "latest_price": round(latest, 2),
        "change_pct": round(change_pct, 2),
        "high_20d": round(high, 2),
        "low_20d": round(low, 2),
        "avg_20d": round(avg_price, 2),
        "volatility_pct": round(volatility, 2),
    }

    # --- 技术信号 ---
    tech_signal = generate_signal(close)
    current_rsi = rsi(close)[-1]
    current_macd = macd(close)
    current_boll = bollinger(close)

    indicators = {
        "ma5": round(ma(close, 5)[-1], 2),
        "ma20": round(ma(close, 20)[-1], 2) if len(close) >= 20 else None,
        "rsi": round(current_rsi, 1),
        "macd": {
            "dif": round(current_macd["dif"][-1], 3),
            "dea": round(current_macd["dea"][-1], 3),
            "hist": round(current_macd["hist"][-1], 3),
        },
        "bollinger": {
            "upper": round(current_boll["upper"][-1], 2),
            "middle": round(current_boll["middle"][-1], 2),
            "lower": round(current_boll["lower"][-1], 2),
        },
    }

    # --- ML models (cached, 1h TTL) ---
    cached = _get_cached_or_train(symbol, close)
    prediction = cached["prediction"]
    tf_result = cached.get("transformer")
    dqn_result = cached.get("rl_strategy")


    result = {
        "symbol": symbol,
        "stats": stats,
        "signal": tech_signal,
        "indicators": indicators,
        "prediction": prediction,
        "transformer": tf_result,
        "rl_strategy": dqn_result,
    }

    return result
