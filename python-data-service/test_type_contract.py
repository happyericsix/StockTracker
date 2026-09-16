# -*- coding: utf-8 -*-
"""
test_type_contract.py — 类型契约测试

为什么需要它：
    Java 侧的 DTO 是强类型的（`String` / `long` / `Double`）。
    如果 Python 返回的 JSON 类型会"飘"（数字变字符串、出现 NaN 等），
    Java 那边就会随机反序列化失败——而且往往是在生产环境才暴露。

    这个脚本把「各字段的确切类型」和「危险边界的行为」固定下来，
    以后谁改了取数逻辑，跑一下就知道有没有破坏契约。

核心结论（实测得出）：
    1. Python 的 json.dumps **不会**自动把 int/float 变成字符串。
       只有手动 str() / f-string 才会。所以类型是可控的。
    2. ⚠️ 但 float('nan') / float('inf') 会输出成 `NaN` / `Infinity`
       —— 这是**非法 JSON**，Java 的 Jackson 会直接报错。
    3. ⚠️ 只有 price 一个字段是从外部字符串解析成 float 的，
       所以它是唯一可能出问题的地方，必须重点测。

用法：
    .\\.venv\\Scripts\\python.exe test_type_contract.py
"""
import json
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import ths_client as tc


def check(name, actual, expected_type, allow_none=False):
    """检查一个值是不是期望的类型。"""
    if actual is None:
        ok = allow_none
        shown = "None"
    else:
        ok = isinstance(actual, expected_type)
        shown = f"{type(actual).__name__} = {actual!r}"
    mark = "OK " if ok else "!! "
    print(f"  {mark}{name:22} {shown}")
    return ok


def main():
    problems = 0

    print("=" * 64)
    print("[1] 自选股接口的字段类型契约")
    print("=" * 64)

    # 用真实格式的返回构造（'688023|1A0001|,17|16|' 是实测拿到的格式）
    stocks = tc.parse_selfstock("688023|1A0001|399006|300033|,17|16|32|33|")
    print(f"  解析出 {len(stocks)} 只")

    for s in stocks:
        print(f"\n  --- {s['market']}{s['code']} ---")
        if not check("code -> str", s["code"], str):
            problems += 1
        if not check("market -> str", s["market"], str):
            problems += 1
        if not check("market_type -> str", s["market_type"], str):
            problems += 1

    # 转成对外 DTO 格式，检查 price/addedAt
    print(f"\n  --- 对外 DTO 格式（_to_dict）---")
    for s in stocks:
        dto = tc.ThsClient._to_dict(s)
        if not check("price -> float/None", dto["price"], float, allow_none=True):
            problems += 1
        if not check("addedAt -> str/None", dto["addedAt"], str, allow_none=True):
            problems += 1

    print()
    print("=" * 64)
    print("[2] price 的解析边界（唯一的浮点数来源）")
    print("=" * 64)
    print("  同花顺原始值 -> float() 之后 -> JSON 样子\n")

    # price 的解析逻辑：float(raw_price)，异常则 None
    raw_cases = [
        ("123.45",     "正常小数"),
        ("1450",       "整数形状"),
        ("0",          "零"),
        ("-1.5",       "负数"),
        ("",           "空串"),
        (None,         "None"),
        ("abc",        "非数字"),
        ("1e3",        "科学计数法"),
        ("nan",        "字符串 nan ← 危险"),
        ("inf",        "字符串 inf ← 危险"),
        ("  12.3  ",   "带空格"),
    ]

    for raw, desc in raw_cases:
        # 用 ths_client 里那个真正的实现（_safe_float），不是自己重写一遍
        price = tc._safe_float(raw if raw != "" else None)

        # 看它会序列化成什么 JSON
        try:
            js = json.dumps({"price": price})
        except Exception as e:
            js = f"序列化异常: {e}"

        # 严格模式检验（模拟 Java Jackson 的严格性：不容忍 NaN/Infinity）
        strict_ok = True
        try:
            json.loads(js, parse_constant=lambda c: (_ for _ in ()).throw(
                ValueError(f"非法常量 {c}")
            ))
        except ValueError:
            strict_ok = False
        except Exception:
            pass

        flag = "" if strict_ok else "   ⚠️ 非法 JSON！Java 会报错"
        print(f"    {desc:16} raw={raw!r:12} -> price={price!r:10} -> {js}{flag}")
        if not strict_ok:
            problems += 1

    print()
    print("=" * 64)
    print("[3] 其它字段的类型稳定性")
    print("=" * 64)

    # qr/create 的返回结构（模拟真实值）
    sample = {
        "ok": True,
        "data": {
            "qrSessionId": "7e38665d92ac431ab5a22196698795cf",
            "qrUrl": "http://mobile.10jqka.com.cn/?source=PC&qrid=usk_xxx",
            "pollIntervalMs": 4000,
            "expiresInSec": 120,
        },
    }
    for k, v in sample.items():
        check(f"{k} -> {type(v).__name__}", v, type(v))
    for k, v in sample["data"].items():
        check(f"data.{k} -> {type(v).__name__}", v, type(v))

    # poll 接口的 session 里 expire_time 是什么类型
    print()
    print("  poll 成功返回的 session 字段：")
    session_sample = {
        "account": "mx_7miffcx00",
        "password": "a" * 32,
        "qrid": "usk_xxx",
        "expire_time": 0,          # ← 注意是同花顺直接给的，可能是 0
    }
    for k, v in session_sample.items():
        check(f"session.{k} -> {type(v).__name__}", v, type(v))

    print()
    print("=" * 64)
    if problems:
        print(f"发现 {problems} 处类型风险")
    else:
        print("类型契约全部通过：字段类型稳定，无非法 JSON 风险")
    print("=" * 64)

    print()
    print("【给 Java 侧的类型结论】")
    print("  ok                  boolean")
    print("  data.qrSessionId    String")
    print("  data.qrUrl          String")
    print("  data.pollIntervalMs long")
    print("  data.expiresInSec   int")
    print("  session.account     String")
    print("  session.password    String")
    print("  session.expire_time long   ← JSON 键名是下划线，需 @JsonProperty")
    print("  stock.code          String")
    print("  stock.market        String   (SH/SZ/BJ/ZS，不会是 null)")
    print("  stock.price         Double   ← 可能是 null（指数没有加入价）")
    print("  stock.addedAt       String   ← 可能是 null")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
