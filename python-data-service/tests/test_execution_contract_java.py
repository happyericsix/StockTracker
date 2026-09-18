# -*- coding: utf-8 -*-
"""执行契约（2/3）：**跨语言一致性**——Java 侧常量与 Python 侧必须逐字相同。

<h3>为什么必须有这个文件</h3>
口径会被两边**同时**用到：Python 侧算并给出成交价与快照，Java 侧结算、落库、展示。
只要有一边写错一个字符串，表现就是"同一个原因统计不到一起"或"口径判定永远不相等"——
**不报错、只是结果悄悄不对**（这正是本项目在 `user_id` / `user_name` 上栽过的跟头）。

<h3>为什么用标记区域解析，而不是扫全文件</h3>
文件里还有 `MAX_ENUM_CHARS`、若干 `List.of(...)` 与注释里的字面量。
整文件扫描会让测试对无关改动过敏，最后被人用一行 `@SuppressWarnings` 绕过。
标记区域让"哪些常量受契约约束"变得显式。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402

JAVA_FILE = (Path(__file__).resolve().parents[2] / "src" / "main" / "java"
             / "com" / "happyericsix" / "stocktracker" / "service" / "ExecutionContract.java")


def _java_text():
    assert JAVA_FILE.exists(), f"Java 侧契约文件不见了：{JAVA_FILE}"
    return JAVA_FILE.read_text(encoding="utf-8")


def _region():
    text = _java_text()
    assert ">>> EXECUTION_CONTRACT" in text and "<<< EXECUTION_CONTRACT" in text, \
        "Java 契约文件的标记注释被删了，跨语言一致性检查会静默失效"
    return text.split(">>> EXECUTION_CONTRACT", 1)[1].split("<<< EXECUTION_CONTRACT", 1)[0]


def _java_strings():
    return [value for _name, value in
            re.findall(r'static final String ([A-Z0-9_]+)\s*=\s*"([^"]+)"', _region())]


def _java_ints():
    return {name: int(value) for name, value in
            re.findall(r"static final int ([A-Z0-9_]+)\s*=\s*(\d+)", _region())}


def _python_values():
    return (list(ec.DECISIONS) + list(ec.SETTLEMENT_KINDS) + list(ec.FILL_BASES)
            + list(ec.ADJUST_MODES) + list(ec.SKIP_REASONS) + [ec.UNKNOWN_PREFIX])


# ==================== 1. 值集合必须完全一致 ====================


def test_java_declares_exactly_the_python_values():
    java = _java_strings()
    assert java, "没能从 Java 契约区域解析出任何字符串常量"
    assert len(java) == len(set(java)), f"Java 侧有重复值：{java}"
    assert set(java) == set(_python_values()), (
        "两边枚举值不一致 —— "
        f"仅 Java 有 {sorted(set(java) - set(_python_values()))}，"
        f"仅 Python 有 {sorted(set(_python_values()) - set(java))}")


def test_java_int_versions_match_python():
    java = _java_ints()
    assert java.get("SNAPSHOT_SCHEMA_VERSION") == ec.SNAPSHOT_SCHEMA_VERSION, java
    assert java.get("MONEY_POLICY_VERSION") == ec.MONEY.version, java


def test_java_lists_are_built_from_those_constants():
    """Java 侧的 List/Map 必须由常量组装（而不是又抄一份字面量），否则改一处会漏一处。"""
    text = _java_text()
    for name in ("DECISIONS", "SETTLEMENT_KINDS", "FILL_BASES", "ADJUST_MODES", "SKIP_REASONS"):
        assert re.search(rf"List<String> {name}\s*=", text), name
        assert not re.search(rf"List<String> {name}\s*=\s*List\.of\(\s*\"", text), \
            f"{name} 里出现了裸字面量，应当引用声明的常量"


# ==================== 2. 映射与归一规则必须一致 ====================


def test_fill_basis_map_pairs_match():
    """成交价口径的映射是本方案的核心口径，两边必须一致。"""
    text = _java_text()
    assert re.search(r"SETTLEMENT_DAILY,\s*FILL_CLOSE", text), "Java 侧 daily 映射不见了"
    assert re.search(r"SETTLEMENT_REALTIME,\s*FILL_REALTIME_LAST", text), \
        "Java 侧 realtime 映射不见了"
    assert ec.FILL_BASIS_BY_SETTLEMENT[ec.SETTLEMENT_DAILY] == ec.FILL_CLOSE
    assert ec.FILL_BASIS_BY_SETTLEMENT[ec.SETTLEMENT_REALTIME] == ec.FILL_REALTIME_LAST


def test_normalize_rule_is_declared_on_both_sides():
    """未知值归一成 `unknown_*` 的规则两边都要有 —— 只在一侧做，另一侧就会写出裸的未知值。"""
    text = _java_text()
    assert "UNKNOWN_PREFIX" in text and "normalizeSkipReason" in text
    assert ec.UNKNOWN_PREFIX == "unknown_"


def test_compare_rule_is_declared_on_both_sides():
    """'只有指纹相同才可比'这条规则也要两边都有：Java 负责展示层判定，Python 负责计算层。"""
    text = _java_text()
    assert "compareBlockReason" in text and "ExecutionFingerprint" in text
    assert callable(ec.compare_allowed)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
