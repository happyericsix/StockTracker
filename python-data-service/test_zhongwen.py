"""test_zhongwen.py —— 证明词典外的中文名也能查到"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

from akshare_client import get_quote, resolve_symbol, NAME_TO_CODE

print("=== 词典里有的 (快速路径) ===")
for name in ['茅台', '宁德', '招行']:
    in_dict = "✓" if name in NAME_TO_CODE else "✗"
    q = get_quote(name)
    if q:
        print(f"  {in_dict} '{name}' → {q['名称']} {q['最新价']}")
    else:
        print(f"  {in_dict} '{name}' → ❌ 没拿到")

print()
print("=== 词典里没有的 A 股 (走 search_stocks 兜底) ===")
for name in ['海康威视', '科大讯飞', '中信证券', '伊利股份', '京东方', '工业富联']:
    in_dict = "✓" if name in NAME_TO_CODE else "✗"
    q = get_quote(name)
    if q:
        print(f"  {in_dict} '{name}' → {q['名称']} {q['最新价']}")
    else:
        print(f"  {in_dict} '{name}' → ❌ 没拿到")

print()
print("=== 确实不存在的股票 ===")
for name in ['阿巴阿巴', 'xxxxxxxxxx']:
    in_dict = "✓" if name in NAME_TO_CODE else "✗"
    q = get_quote(name)
    if q:
        print(f"  {in_dict} '{name}' → {q['名称']} {q['最新价']}")
    else:
        print(f"  {in_dict} '{name}' → ❌ 没拿到 (合理)")
