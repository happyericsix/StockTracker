"""test_debug.py —— 深度 debug resolve_symbol"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

from akshare_client import resolve_symbol, get_quote, NAME_TO_CODE

print("=== 直接调 resolve_symbol ===")
for name in ['海康威视', '科大讯飞', '中信证券', '伊利', '京东方']:
    in_dict = name in NAME_TO_CODE
    r = resolve_symbol(name)
    print(f"  {name:10s} in_dict={in_dict} resolved={r!r}")

print()
print("=== get_quote 拿到的数据 ===")
for name in ['海康威视', '科大讯飞']:
    q = get_quote(name)
    if q:
        print(f"  {name}: {q.get('名称')} {q.get('最新价')}")
    else:
        print(f"  {name}: ❌ None")
