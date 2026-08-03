"""
test_intent.py —— 意图识别单元测试
"""
import sys
import io

# 强制 stdout 用 UTF-8（避免 Windows 中文乱码）
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

import intent_router


def test(name, msg, expected_type, expected_kw=None, expected_code=None):
    result = intent_router.parse(msg)
    ok = result.type == expected_type
    if expected_kw and result.keyword != expected_kw:
        ok = False
    if expected_code and result.code != expected_code:
        ok = False
    mark = "✅" if ok else "❌"
    print(f"{mark} [{name}] '{msg}' → type={result.type}, symbol={result.symbol}, keyword={result.keyword}, code={result.code}")
    return ok


def test_symbol(name, msg, expected_type, expected_symbol=None, expected_code=None):
    """检查 symbol 字段的版本"""
    result = intent_router.parse(msg)
    ok = result.type == expected_type
    if expected_symbol and result.symbol != expected_symbol:
        ok = False
    if expected_code and result.code != expected_code:
        ok = False
    mark = "✅" if ok else "❌"
    print(f"{mark} [{name}] '{msg}' → type={result.type}, symbol={result.symbol}, keyword={result.keyword}, code={result.code}")
    return ok


cases = [
    test,  # 默认使用 keyword 版
    test,  # placeholder
]

# case: (test_fn, name, msg, expected_type, expected_x, expected_code)
all_cases = [
    (test, "纯代码-沪深", "600519", "quote", None, None),
    (test, "纯代码-美股", "AAPL", "quote", None, None),
    (test, "中文名", "茅台", "quote", "茅台", None),
    (test, "中文长名", "贵州茅台", "quote", "贵州茅台", None),
    (test_symbol, "带前缀代码", "帮我看看sh600519", "quote", "SH600519", None),
    (test, "能买吗", "宁德能买吗", "analyze", "宁德", None),
    (test_symbol, "分析请求", "帮我分析下 002594", "analyze", "002594", None),
    (test, "自选股-看", "我的自选", "watchlist", None, None),
    (test, "自选股-怎么样", "我的持仓今天怎么样", "watchlist", None, None),
    (test, "绑定-标准", "绑定 123456", "bind", None, "123456"),
    (test, "绑定-无空格", "绑定888888", "bind", None, "888888"),
    (test, "解绑", "解绑", "unbind", None, None),
    (test, "帮助", "帮助", "help", None, None),
    (test, "闲聊", "你好", "chat", None, None),
    (test_symbol, "K线", "002594 k线", "history", "002594", None),
]

passed = 0
for fn, *args in all_cases:
    if fn(*args):
        passed += 1
print(f"\n{passed}/{len(all_cases)} passed")
sys.exit(0 if passed == len(all_cases) else 1)
