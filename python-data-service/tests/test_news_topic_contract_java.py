# -*- coding: utf-8 -*-
"""新闻事件线契约（2/2）：**跨语言一致性**——Java 侧常量与 Python 侧必须逐字相同。

做法与 test_execution_contract_java.py 同一套：解析 Java 文件的标记区域
（>>> NEWS_TOPIC_CONTRACT … <<< NEWS_TOPIC_CONTRACT），比对字符串常量集合。
为什么不用整文件扫描：见执行契约测试的模块 docstring（对无关改动过敏，
最后会被一行绕过）。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import news_understanding  # noqa: E402
from news_topics import EDITOR_OUTPUT_FIELDS, SKIP_REASONS, WRITER_OUTPUT_FIELDS  # noqa: E402

JAVA_FILE = (Path(__file__).resolve().parents[2] / "src" / "main" / "java"
             / "com" / "happyericsix" / "stocktracker" / "service" / "NewsTopicContract.java")


def _region():
    assert JAVA_FILE.exists(), f"Java 侧契约文件不见了：{JAVA_FILE}"
    text = JAVA_FILE.read_text(encoding="utf-8")
    assert ">>> NEWS_TOPIC_CONTRACT" in text and "<<< NEWS_TOPIC_CONTRACT" in text, \
        "Java 契约文件的标记注释被删了，跨语言一致性检查会静默失效"
    return text.split(">>> NEWS_TOPIC_CONTRACT", 1)[1].split("<<< NEWS_TOPIC_CONTRACT", 1)[0]


def _java_strings():
    return [value for _name, value in
            re.findall(r'static final String ([A-Z0-9_]+)\s*=\s*"([^"]+)"', _region())]


def test_java_declares_exactly_the_python_values():
    """方向枚举 + 未撰稿原因：两侧必须一字不差（多一个少一个都是契约破坏）。"""
    python_values = list(news_understanding.DIRECTIONS) + list(SKIP_REASONS)
    java = _java_strings()
    assert java, "没能从 Java 契约区域解析出任何字符串常量"
    assert len(java) == len(set(java)), f"Java 侧有重复值：{java}"
    assert set(java) == set(python_values), (
        "两边枚举值不一致 —— "
        f"仅 Java 有 {sorted(set(java) - set(python_values))}，"
        f"仅 Python 有 {sorted(set(python_values) - set(java))}")


def test_field_name_sets_match():
    """输出字段名集合：Python 的元组 vs Java 的 Set.of 常量。"""
    text = JAVA_FILE.read_text(encoding="utf-8")
    for name, python_fields in (("WRITER_OUTPUT_FIELDS", WRITER_OUTPUT_FIELDS),
                                ("EDITOR_OUTPUT_FIELDS", EDITOR_OUTPUT_FIELDS)):
        match = re.search(rf"Set<String> {name}\s*=\s*Set\.of\((.*?)\);", text, re.S)
        assert match, f"Java 侧 {name} 不见了"
        java_fields = re.findall(r'"([^"]+)"', match.group(1))
        assert set(java_fields) == set(python_fields), (name, java_fields, python_fields)
