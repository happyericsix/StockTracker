# -*- coding: utf-8 -*-
"""
_check_undefined.py — 静态检查「未定义变量」（专治 NameError）

什么时候用：
    改完 Python 代码后跑一下，能在运行前发现"变量名写错"这类错。
        .\\.venv\\Scripts\\python.exe _check_undefined.py

为什么需要它：
    ths_probe_v2.py 里曾把 auth["userid"] 写成裸 userid，
    结果运行时抛 NameError，还被 except 吞掉，
    表现出来像是"接口不存在"，白排查了半天。
    这种错静态就能发现，不该等到运行时。

    （项目里没装 pyflakes/ruff，所以自带一个轻量的。）

实现方式：基于 AST 手工做作用域分析。
   - 每个函数 / 类 / 推导式建一个作用域，记录"这里定义了什么名字"
   - 读一个名字时沿作用域链往上找，最后回退到 builtins
   - 闭包（内层函数用外层变量）和列表推导式（[self.f(x) for x in y]）
     都能正确识别，不会误报

要加检查目标就往 TARGETS 里加文件名。
"""
import ast
import builtins
import os
import sys

TARGETS = ["ths_probe_v2.py", "ths_client.py", "ths_probe_simple.py"]

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


class Scope:
    """一个作用域：全局 / 函数 / 类 / 推导式。"""

    def __init__(self, kind, lineno, parent=None, name="<module>"):
        self.kind = kind
        self.lineno = lineno
        self.parent = parent
        self.name = name
        self.defined = set()        # 这里定义的名字
        self.reads = []             # [(名字, 行号)] 这里读取的名字


def collect_scope(node, scope):
    """遍历一个作用域，记录定义的/读取的名字，并为子作用域递归。"""
    for child in ast.iter_child_nodes(node):
        _visit(child, scope)


def _bind_target(target, scope):
    """把赋值目标里出现的名字记进 defined。"""
    if isinstance(target, ast.Name):
        scope.defined.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _bind_target(elt, scope)
    elif isinstance(target, ast.Starred):
        _bind_target(target.value, scope)
    # Attribute / Subscript 不算定义新名字


def _visit(node, scope):
    if node is None:
        return

    # ---- 定义类 ----
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        scope.defined.add(node.name)
        fn = Scope("function", node.lineno, scope, node.name)
        args = node.args
        for a in (args.posonlyargs + args.args + args.kwonlyargs):
            fn.defined.add(a.arg)
        if args.vararg:
            fn.defined.add(args.vararg.arg)
        if args.kwarg:
            fn.defined.add(args.kwarg.arg)
        for d in list(args.defaults) + [d for d in args.kw_defaults if d]:
            _visit(d, scope)          # 默认值在【外层】作用域求值
        for d in node.decorator_list:
            _visit(d, scope)          # 装饰器同理
        collect_scope(node, fn)
        return

    if isinstance(node, ast.ClassDef):
        scope.defined.add(node.name)
        for d in node.decorator_list:
            _visit(d, scope)
        for b in node.bases:
            _visit(b, scope)
        cls = Scope("class", node.lineno, scope, node.name)
        collect_scope(node, cls)
        return

    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        comp = Scope("comprehension", node.lineno, scope)
        for gen in node.generators:
            _bind_target(gen.target, comp)
            _visit(gen.iter, comp)
            for cond in gen.ifs:
                _visit(cond, comp)
        _visit(node.elt, comp)
        return

    if isinstance(node, ast.DictComp):
        comp = Scope("comprehension", node.lineno, scope)
        for gen in node.generators:
            _bind_target(gen.target, comp)
            _visit(gen.iter, comp)
            for cond in gen.ifs:
                _visit(cond, comp)
        _visit(node.key, comp)
        _visit(node.value, comp)
        return

    if isinstance(node, ast.Lambda):
        fn = Scope("lambda", node.lineno, scope, "<lambda>")
        for a in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
            fn.defined.add(a.arg)
        collect_scope(node.body if isinstance(node.body, ast.AST) else node, fn)
        return

    # ---- 定义名字 ----
    if isinstance(node, ast.Assign):
        for t in node.targets:
            _bind_target(t, scope)
            _visit(t, scope)
        _visit(node.value, scope)
        return

    if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        _bind_target(node.target, scope)
        _visit(node.value, scope)
        return

    if isinstance(node, (ast.For, ast.AsyncFor)):
        _bind_target(node.target, scope)
        _visit(node.iter, scope)
        for n in node.body + node.orelse:
            _visit(n, scope)
        return

    if isinstance(node, ast.withitem):
        if node.optional_vars:
            _bind_target(node.optional_vars, scope)
        _visit(node.context_expr, scope)
        return
    if isinstance(node, (ast.With, ast.AsyncWith)):
        for item in node.items:
            _visit(item, scope)
        for n in node.body:
            _visit(n, scope)
        return

    if isinstance(node, ast.Import):
        for a in node.names:
            scope.defined.add((a.asname or a.name.split(".")[0]))
        return
    if isinstance(node, ast.ImportFrom):
        for a in node.names:
            if a.name != "*":
                scope.defined.add(a.asname or a.name)
        return

    if isinstance(node, ast.ExceptHandler):
        if node.name:
            scope.defined.add(node.name)
        if node.type:
            _visit(node.type, scope)
        for n in node.body:
            _visit(n, scope)
        return

    if isinstance(node, (ast.Global, ast.Nonlocal)):
        for n in node.names:
            scope.defined.add(n)
        return

    # ---- 读取名字 ----
    if isinstance(node, ast.Name):
        if isinstance(node.ctx, ast.Load):
            scope.reads.append((node.id, node.lineno))
        else:
            scope.defined.add(node.id)
        return

    for child in ast.iter_child_nodes(node):
        _visit(child, scope)


def lookup(name, scope):
    """沿作用域链找这个名字（模拟闭包），找不到再看 builtins。"""
    # Python 自动注入的模块级名字，不算未定义
    if name in ("__file__", "__name__", "__doc__", "__package__", "__spec__",
                "__loader__", "__builtins__", "__debug__"):
        return True
    cur = scope
    while cur is not None:
        if name in cur.defined:
            return True
        cur = cur.parent
    return hasattr(builtins, name)


def walk_scopes(scope, out):
    out.append(scope)
    for child in getattr(scope, "children", []):
        walk_scopes(child, out)


def gather(scope, out):
    """收集作用域树（用 defined 里的线索不够，所以要显式建树）。"""
    out.append(scope)
    for child in scope.__dict__.get("_children", []):
        gather(child, out)


def check_file(path, fname):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), fname)

    # 建作用域树
    root = Scope("module", 0, None, "<module>")
    root._children = []

    # 重新实现一遍带建树的收集
    def build(node, scope):
        for child in ast.iter_child_nodes(node):
            b(child, scope)

    def b(node, scope):
        if node is None:
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope.defined.add(node.name)
            fn = Scope("function", node.lineno, scope, node.name)
            fn._children = []
            scope._children.append(fn)
            args = node.args
            for a in (args.posonlyargs + args.args + args.kwonlyargs):
                fn.defined.add(a.arg)
            if args.vararg:
                fn.defined.add(args.vararg.arg)
            if args.kwarg:
                fn.defined.add(args.kwarg.arg)
            for d in list(args.defaults) + [d for d in args.kw_defaults if d]:
                b(d, scope)
            for d in node.decorator_list:
                b(d, scope)
            for n in node.body:
                b(n, fn)
            return

        if isinstance(node, ast.ClassDef):
            scope.defined.add(node.name)
            cls = Scope("class", node.lineno, scope, node.name)
            cls._children = []
            scope._children.append(cls)
            for d in node.decorator_list:
                b(d, scope)
            for base in node.bases:
                b(base, scope)
            for n in node.body:
                b(n, cls)
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            comp = Scope("comprehension", node.lineno, scope, "<comp>")
            comp._children = []
            scope._children.append(comp)
            for gen in node.generators:
                _bind_target(gen.target, comp)
                b(gen.iter, comp)
                for cond in gen.ifs:
                    b(cond, comp)
            if isinstance(node, ast.DictComp):
                b(node.key, comp)
                b(node.value, comp)
            else:
                b(node.elt, comp)
            return

        if isinstance(node, ast.Lambda):
            fn = Scope("lambda", node.lineno, scope, "<lambda>")
            fn._children = []
            scope._children.append(fn)
            for a in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs):
                fn.defined.add(a.arg)
            b(node.body, fn)
            return

        if isinstance(node, ast.Assign):
            for t in node.targets:
                _bind_target(t, scope)
                b(t, scope)
            b(node.value, scope)
            return
        if isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            _bind_target(node.target, scope)
            b(node.value, scope)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            _bind_target(node.target, scope)
            b(node.iter, scope)
            for n in node.body + node.orelse:
                b(n, scope)
            return
        if isinstance(node, ast.With):
            for item in node.items:
                if item.optional_vars:
                    _bind_target(item.optional_vars, scope)
                b(item.context_expr, scope)
            for n in node.body:
                b(n, scope)
            return
        if isinstance(node, ast.Import):
            for a in node.names:
                scope.defined.add(a.asname or a.name.split(".")[0])
            return
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name != "*":
                    scope.defined.add(a.asname or a.name)
            return
        if isinstance(node, ast.ExceptHandler):
            if node.name:
                scope.defined.add(node.name)
            if node.type:
                b(node.type, scope)
            for n in node.body:
                b(n, scope)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            for n in node.names:
                scope.defined.add(n)
            return
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                scope.reads.append((node.id, node.lineno))
            else:
                scope.defined.add(node.id)
            return

        for child in ast.iter_child_nodes(node):
            b(child, scope)

    for n in tree.body:
        b(n, root)

    # 收集全部作用域并检查读取
    allscopes = []
    gather(root, allscopes)

    problems = []
    for sc in allscopes:
        for name, lineno in sc.reads:
            if not lookup(name, sc):
                problems.append((lineno, sc.name, name))

    return problems


def main():
    base = os.path.dirname(os.path.abspath(__file__))
    total = 0
    for fname in TARGETS:
        path = os.path.join(base, fname)
        if not os.path.exists(path):
            continue
        problems = check_file(path, fname)
        # 去重（同一名字同一行可能被记多次）
        problems = sorted(set(problems))
        print(f"\n{'-' * 58}\n{fname}")
        if problems:
            for lineno, scope, name in problems:
                print(f"  ⚠ 第 {lineno} 行  [{scope}]  引用了未定义的名字: {name}")
                total += 1
        else:
            print("  ✓ 未发现未定义变量")

    print(f"\n{'=' * 58}")
    print(f"共发现 {total} 处未定义引用")
    print("=" * 58)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
