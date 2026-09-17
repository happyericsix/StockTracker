# -*- coding: utf-8 -*-
"""测试全局准备。

<h3>为什么必须在这里设 INTERNAL_API_TOKEN</h3>
P4a 把 Python 侧的服务间鉴权改成了 **fail-closed**：没配 token 时 `/api/v1/*` 一律 503。
这个默认值是对的（生产忘配就等于裸奔），但它会让"在干净环境里跑测试"变成一片 503 ——
测试会替我们踩这个坑，而不是让部署去踩。

`app.py` 的 `_load_env_file()` 只在环境变量**不存在**时才读 `.env`，
所以这里用 `setdefault` 的语义就是"测试环境优先"：本地有 `.env` 也不会覆盖它，
于是测试结果不依赖开发者机器上的 `.env` 内容（这本身就是一件该钉住的事）。
"""
import os
import sys
from pathlib import Path

import pytest

# 让 `tests/` 下的用例可以直接 `import agent` / `import app`
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 必须在任何模块 import app 之前设置（app.py 在 import 期就读取它）
os.environ.setdefault("INTERNAL_API_TOKEN", "test-internal-token")


@pytest.fixture(autouse=True)
def _no_background_review(monkeypatch):
    """测试里**绝不允许**后台裁决者（critic）真去调模型。

    为什么必须全局挡住：`run_agent` 在确定性审计报出候选数字时，会**异步入队**
    一次真实的 LLM 调用。于是"跑一遍单元测试"会悄悄产生网络请求与账单 ——
    而失败还不会让测试红（异步、fail-open），属于最阴的一类副作用。

    发现的经过：接上裁决者之后跑全量，stderr 里冒出一段真实的中文判决文本。
    真调评测走 tests/test_review_eval.py（`REVIEW_EVAL_LIVE=1` 显式开启）。
    """
    import agent.react_agent as ra

    # 挡在 `_run_review`（真正发请求的那一层），而不是 `_queue_review`：
    # 后者自己的逻辑（"没有候选就不入队"）是要被测试的，挡掉它等于把被测对象换成了替身。
    monkeypatch.setattr(ra, "_run_review", lambda *args, **kwargs: None)
    # 策略审查同理：产出策略后它也会异步发一次真实请求
    monkeypatch.setattr(ra, "_run_strategy_review", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def _no_memory_http(monkeypatch):
    """测试里也**绝不真连 Java 的记忆接口**（它根本没起）。

    不这么做的代价是量出来的：后台的 `_queue_tool_log` / `_queue_usage` 会往
    localhost:8080 发真实请求，而那个端口是死的 —— 每次失败约 4 秒，而写入用的是
    **单线程**执行池，于是 pytest 报完"491 passed in 47s"之后，进程还要再花约 125 秒
    排空这些注定失败的请求（实测：整轮墙钟 173s vs pytest 自身 48s）。

    挡在 `requests.get/post` 这一层而不是 `memory_store._get/_post`：
    契约测试（tests/test_memory_internal_contract.py）正是靠替换 requests 来断言
    请求形状的，挡更高的层会把它们要测的东西一起挡掉 —— 而用例自己的 monkeypatch
    在本夹具之后执行，所以那些契约测试照常工作。

    **只拦记忆内部接口的 URL**，不做"一律禁止联网"：真调评测
    （`REVIEW_EVAL_LIVE=1` / `TOOL_EVAL_LIVE=1`）需要真的调模型与外部数据源，
    一刀切会把它们也拦死（这一点是第一版写错后发现的）。
    """
    import requests

    _INTERNAL_PREFIX = "/api/v1/internal/"

    def _guard(real):
        def wrapper(url, *args, **kwargs):
            if _INTERNAL_PREFIX in str(url):
                raise requests.exceptions.ConnectionError("记忆服务未启动（测试环境）")
            return real(url, *args, **kwargs)

        return wrapper

    monkeypatch.setattr(requests, "get", _guard(requests.get))
    monkeypatch.setattr(requests, "post", _guard(requests.post))
