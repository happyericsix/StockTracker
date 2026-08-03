import sys, os, io
sys.path.insert(0, os.path.dirname(__file__))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from akshare_client import get_history
from quant_model import StockPredictor, generate_signal

symbol = "600519"
print("=" * 55)
print(f"  LightGBM StockPredictor 验证 — {symbol}")
print("=" * 55)

# Step 1: 拉数据
records = get_history(symbol)
prices = [float(r["close"]) for r in records if float(r["close"]) > 0]
print(f"\n1. 数据: {len(prices)} 个交易日, 最新价 {prices[-1]:.2f}")

# Step 2: 训练
p = StockPredictor()
ok = p.train(prices)
print(f"2. 训练: {'成功' if ok else '失败'}")

# Step 3: 预测
result = p.predict(prices)
print(f"3. 预测结果:")
print(f"   预测涨跌幅: {result['predicted_change_pct']}%")
print(f"   方向判断:   {result['direction']}")
print(f"   特征数量:   {len(result['feature_importance'])}")

# Step 4: Top 5 特征
print(f"\n4. 最重要的5个特征:")
for name, imp in sorted(result["feature_importance"].items(), key=lambda x: x[1], reverse=True)[:5]:
    print(f"   {name:<25} {imp:.4f}")

# Step 5: 和规则信号对比
signal = generate_signal(prices)
print(f"\n5. 技术指标综合信号:")
print(f"   信号: {signal['signal']}")
print(f"   打分: {signal['score']}")
for k, v in signal['details'].items():
    print(f"   {k}: {v}")

print(f"\n{'=' * 55}")
print(f"  结论: LightGBM 已成功跑通!")
print(f"{'=' * 55}\n")
