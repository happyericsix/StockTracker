# -*- coding: utf-8 -*-
"""
_audit_vendor.py — 检查 vendored thspypc 里"调用参数与函数定义不匹配"的 bug

背景：main 分支的 client.py 里 _do_tcp_login 被传了 max_retries=1，
但函数定义不接受这个参数，导致扫码登录路径直接崩。
这个脚本用 AST 静态扫一遍，看还有没有同类问题。

一次性自检脚本，检查完可以删。
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "vendor", "thspypc_src", "src", "thspypc")

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def collect_signatures(tree):
    """收集 {类名.方法名: (位置参数名, 是否接受**kwargs)}"""
    sigs = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = item.args
            named = [a.arg for a in args.posonlyargs + args.args]
            named += [a.arg for a in args.kwonlyargs]
            if args.vararg:
                named.append("*" + args.vararg.arg)
            if args.kwarg:
                named.append("**" + args.kwarg.arg)
            sigs[f"{node.name}.{item.name}"] = named
    return sigs


def main():
    print("=" * 62)
    print("审计 vendored thspypc：调用参数 vs 函数定义")
    print("=" * 62)

    problems = 0
    checked = 0

    for fname in sorted(os.listdir(SRC)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(SRC, fname)
        tree = ast.parse(open(path, encoding="utf-8").read())
        sigs = collect_signatures(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            # 只看 self.xxx(...) 这种自调用
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "self"):
                continue

            method = func.attr
            # 找到这个方法属于哪个类（可能有重名，先全部收集）
            candidates = {k: v for k, v in sigs.items() if k.endswith("." + method)}
            if not candidates:
                continue
            checked += 1

            passed_kw = {kw.arg for kw in node.keywords if kw.arg}
            if not passed_kw:
                continue

            # 只要有一个候选签名能接受，就不算问题
            ok = False
            for key, params in candidates.items():
                if "**" + "kwargs" in params or any(p.startswith("**") for p in params):
                    ok = True
                    break
                if passed_kw.issubset(set(params)):
                    ok = True
                    break
            if ok:
                continue

            problems += 1
            print(f"\n✗ {fname}:{node.lineno}")
            print(f"    调用 self.{method}({', '.join(k + '=' for k in sorted(passed_kw))})")
            for key, params in candidates.items():
                print(f"    定义 {key}({', '.join(params)})")
            print(f"    → 多出来的参数: {sorted(passed_kw - set(list(candidates.values())[0]))}")

    print()
    print("=" * 62)
    print(f"检查了 {checked} 处 self.xxx() 调用，发现 {problems} 处参数不匹配")
    print("=" * 62)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
