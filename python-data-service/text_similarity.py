# -*- coding: utf-8 -*-
"""标题相似度：2-gram Jaccard（news_credibility 与 news_client 共用）。

<h3>为什么从 news_credibility 抽出来而不是再写一份</h3>
2026-10-09 多源化之后，"这两条是不是在讲同一件事"有两个消费方：
- news_credibility 的交叉印证维度（同一标的同日，另一家信源讲相似事件 → 加分）；
- news_client 的跨源归并（东财/新浪/同花顺/富途同时报道同一事件 → 合成一条）。

两处各写一套 2-gram，迟早会在停用词表或分词方式上分叉——
届时会出现"归并判它们是同一件事、可信度却判它们不相似"的自相矛盾，
而那种不一致没有任何单测能抓住（两个模块各自的测试都是绿的）。

<h3>为什么 2-gram 够用、不上真分词/嵌入</h3>
这里的相似度只用于"同一事件跨源转载"的粗筛：同一事件的标题天然高度重合
（多数是通稿），2-gram Jaccard 的精度足够；语义层面的"同主题不同事件"归并
是总编 LLM 一次调用的事（见 plans 里的新闻雷达升级方案），不是这一层的职责。
"""
from __future__ import annotations

import re

# 停用词与 credibility 语义同源：公告/发布类高频词出现在所有标题里，
# 不去掉的话"关于召开业绩说明会的公告"与"关于召开股东会的公告"会高度相似。
DEFAULT_STOPWORDS = frozenset("的了在是和与及为对该其将从被中公告关于发布".split())


def title_tokens(text, stopwords: frozenset = DEFAULT_STOPWORDS) -> set:
    """标题分词：中文没有天然分隔，按 2-gram + 去停用词近似。

    纯数字 gram 也去掉（"600519" 和 "600520" 的 gram 全是数字噪声）。
    """
    cleaned = re.sub(r"[\s\W]+", "", str(text or ""))
    grams = {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}
    return {g for g in grams if g not in stopwords and not g.isdigit()}


def title_similarity(a: set, b: set) -> float:
    """Jaccard 相似度；空集（标题为空/全是噪声）时返回 0 —— 无从判断 ≠ 相同。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
