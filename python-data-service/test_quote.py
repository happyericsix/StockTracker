"""test_quote.py —— 验证 akshare_client 修复"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

from akshare_client import get_quote, resolve_symbol

print("--- resolve_symbol 测试 ---")
for s in ['茅台', '宁德', '苹果', '600519', 'sh600519', 'AAPL', '不知道的股票', '招行']:
    r = resolve_symbol(s)
    print(f"  {repr(s):20s} -> {r}")

print()
print("--- get_quote 测试 ---")
for s in ['茅台', '宁德时代', '苹果', 'AAPL', 'sh600519', '000001']:
    q = get_quote(s)
    if q:
        name = q.get("名称", "?")
        price = q.get("最新价", "?")
        change = q.get("涨跌幅", "?")
        print(f"  {s:10s} -> {name} 最新价={price} 涨跌幅={change}%")
    else:
        print(f"  {s:10s} -> ❌ 没拿到数据")
