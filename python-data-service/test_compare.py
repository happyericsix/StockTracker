"""
对比测试：LightGBM（新版）vs RandomForest（旧版）预测准确率
用法：python test_compare.py [股票代码]  （默认 600519）
"""
import sys, os, io
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import train_test_split
from akshare_client import get_history
from quant_model import StockPredictor, ma, rsi, bollinger, macd

# ========== 旧版 RandomForest 预测器（对比基准） ==========
class OldRF:
    def __init__(self):
        self.model = RandomForestRegressor(n_estimators=100, max_depth=10, min_samples_split=5, random_state=42, n_jobs=-1)
        self.trained = False

    def _prepare(self, prices):
        df = pd.DataFrame({"close": pd.Series(prices, dtype=float)})
        for lag in [1, 2, 3, 5, 10]:
            df[f"ret_{lag}"] = df["close"].pct_change(lag)
        df["ma5"] = ma(df["close"].tolist(), 5)
        df["ma20"] = ma(df["close"].tolist(), 20)
        df["rsi"] = rsi(df["close"].tolist(), 14)
        boll = bollinger(df["close"].tolist())
        df["boll_pos"] = (df["close"] - pd.Series(boll["lower"])) / (pd.Series(boll["upper"]) - pd.Series(boll["lower"])).replace(0, 1)
        df["target"] = df["close"].pct_change(1).shift(-1)
        df = df.dropna()
        if len(df) < 20: return None, None
        feat = [c for c in df.columns if c not in ("close", "target")]
        return df[feat].values, df["target"].values

    def train(self, prices):
        X, y = self._prepare(prices)
        if X is None: return False
        X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
        self.model.fit(X_tr, y_tr)
        self.trained = True
        self.train_r2 = self.model.score(X_tr, y_tr)
        self.test_r2 = self.model.score(X_te, y_te)
        return True

    def predict(self, prices):
        if not self.trained: self.train(prices)
        X, _ = self._prepare(prices)
        if X is None or len(X) == 0: return None
        return self.model.predict(X[-1].reshape(1, -1))[0]

# ========== 测试主流程 ==========
symbol = sys.argv[1] if len(sys.argv) > 1 else "600519"
print(f"\n{'='*60}")
print(f"  LightGBM vs RandomForest -- {symbol}")
print(f"{'='*60}\n")

# 1. 获取真实数据
print("[1/4] Getting historical data...")
records = get_history(symbol)
if not records:
    print("  Failed to get data")
    sys.exit(1)
prices = [float(r["close"]) for r in records if float(r["close"]) > 0]
print(f"  Got {len(prices)} trading days")
print(f"  Range: {records[0]['date']} ~ {records[-1]['date']}")
print(f"  Latest price: {prices[-1]:.2f}")

# 2. 训练旧版 RandomForest
print("\n[2/4] Training RandomForest (old)...")
rf = OldRF()
rf.train(prices)
print(f"  RandomForest (10 features): train R2={rf.train_r2:.4f}, test R2={rf.test_r2:.4f}")

# 3. 训练新版 LightGBM
print("\n[3/4] Training LightGBM (new)...")
lgb_predictor = StockPredictor()
lgb_predictor.train(prices)
X, y, cols = lgb_predictor._prepare_features(prices)
X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
lgb_train_r2 = lgb_predictor.model.score(X_tr, y_tr)
lgb_test_r2 = lgb_predictor.model.score(X_te, y_te)
print(f"  LightGBM  ({len(cols)} features): train R2={lgb_train_r2:.4f}, test R2={lgb_test_r2:.4f}")

# 4. 预测对比
print("\n[4/4] Prediction comparison...")
rf_pred = rf.predict(prices)
lgb_result = lgb_predictor.predict(prices)

print(f"\n{'='*60}")
print(f"  RESULTS")
print(f"{'='*60}")
print(f"  {'Metric':<25} {'RandomForest':>15} {'LightGBM':>15}")
print(f"  {'-'*55}")
print(f"  {'Features':<25} {10:>15} {len(cols):>15}")
print(f"  {'Train R2':<25} {rf.train_r2:>15.4f} {lgb_train_r2:>15.4f}")
print(f"  {'Test R2':<25} {rf.test_r2:>15.4f} {lgb_test_r2:>15.4f}")
print(f"  {'Predicted change %':<25} {rf_pred*100:>14.2f}% {lgb_result['predicted_change_pct']:>14.2f}%")
print(f"  {'Direction':<25} {'N/A':>15} {lgb_result['direction']:>15}")
print(f"{'='*60}")

# 结论
if lgb_test_r2 > rf.test_r2:
    imp_pct = (lgb_test_r2 - rf.test_r2) / abs(rf.test_r2) * 100 if rf.test_r2 != 0 else 100
    print(f"\n  LightGBM test R2 improved by {imp_pct:.1f}%")
else:
    imp_pct = abs((lgb_test_r2 - rf.test_r2) / abs(rf.test_r2) * 100) if rf.test_r2 != 0 else 0
    print(f"\n  LightGBM test R2 decreased by {imp_pct:.1f}%")

# 特征重要性 Top 5
print(f"\n  LightGBM Top 5 features:")
top5 = sorted(lgb_result["feature_importance"].items(), key=lambda x: x[1], reverse=True)[:5]
for name, imp in top5:
    print(f"    {name:<30} {imp:.4f}")
print()
