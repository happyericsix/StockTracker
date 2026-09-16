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
