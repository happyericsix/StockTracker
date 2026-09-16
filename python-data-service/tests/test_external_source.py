# -*- coding: utf-8 -*-
"""外部能力边界（T2a）：健康门禁、分级 TTL、不可用摘除。

这些测试打的是**边界**而不是数据（数据正确性由各自的 client 测试管），
所以全程用假的 producer，不碰网络。要钉住的是四件事：

1. TTL 按**数据集**分级（新闻 10 分钟 ≠ 财报 1 天）；
2. 连续失败 → 判不可用，且冷却期内**不再调用上游**（这是"挂了不拖慢对话"的关键）；
3. 冷却结束后半开：成功即恢复，失败立刻再关；
4. 空结果不算故障（"今天没消息"是合法答案），但也不入缓存。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import external_source as es


def registry(**kwargs):
    return es.ExternalSourceRegistry(**kwargs)


def test_ttl_is_declared_per_dataset_not_globally():
    """新闻 10 分钟、财报 1 天：新鲜度需求差两个数量级，一刀切必然错一头。"""
    assert es.DATASETS["eastmoney.news"].ttl_s == 600
    assert es.DATASETS["eastmoney.news_global"].ttl_s == 600
    assert es.DATASETS["eastmoney.financial"].ttl_s == 86400
    assert es.DATASETS["eastmoney.news"].ttl_label == "10 分钟"


def test_cache_hit_avoids_calling_upstream():
    reg = registry()
    calls = []

    def producer():
        calls.append(1)
        return {"items": [1]}

    first = reg.fetch("eastmoney.news", "600519", producer, now=1000.0)
    second = reg.fetch("eastmoney.news", "600519", producer, now=1001.0)

    assert first.ok and not first.cached
    assert second.ok and second.cached
    assert len(calls) == 1, "TTL 内的第二次调用不该再打上游"
    assert second.fetched_at == first.fetched_at


def test_cache_expires_by_dataset_ttl():
    reg = registry()
    calls = []

    def producer():
        calls.append(1)
        return {"items": [1]}

    reg.fetch("eastmoney.news", "k", producer, now=1000.0)
    # 601 秒后：新闻过期（TTL 600），重新取
    reg.fetch("eastmoney.news", "k", producer, now=1600.1)
    assert len(calls) == 2

    reg.fetch("eastmoney.financial", "k", producer, now=1000.0)
    # 财报 1 天 TTL：一小时后仍然是缓存
    stale = reg.fetch("eastmoney.financial", "k", producer, now=4600.0)
    assert stale.cached is True


def test_repeated_failures_mark_the_source_down_and_stop_calling_it():
    """上游挂了最坏的结果不是报错，而是**每次调用都白等一次超时**。"""
    reg = registry(failure_threshold=3, cooldown_s=300.0)
    calls = []

    def boom():
        calls.append(1)
        raise ConnectionError("upstream gone")

    for i in range(3):
        result = reg.fetch("eastmoney.news", f"k{i}", boom, now=1000.0)
        assert not result.ok
        assert "取数失败" in result.error

    assert len(calls) == 3
    assert not reg.available("eastmoney.news", now=1000.0)
    assert reg.status("eastmoney.news", now=1000.0) == es.STATUS_DOWN

    # 冷却期内：连 producer 都不该被调到（这正是门禁的价值）
    blocked = reg.fetch("eastmoney.news", "k-new", boom, now=1100.0)
    assert not blocked.ok
    assert "当前不可用" in blocked.error
    assert len(calls) == 3, "冷却期内不能再打上游"
    assert "不要重试" in blocked.error, "错误说明必须告诉模型不要重试"


def test_source_health_is_shared_across_datasets_of_the_same_provider():
    """一个上游挂了，它下面的**所有**数据集都不该再被调用（新闻挂了财报也一起停）。"""
    reg = registry(failure_threshold=2, cooldown_s=300.0)

    def boom():
        raise RuntimeError("x")

    for i in range(2):
        reg.fetch("eastmoney.news", f"k{i}", boom, now=1000.0)

    assert not reg.available("eastmoney.news", now=1000.0)
    assert not reg.available("eastmoney.financial", now=1000.0)


def test_cooldown_then_half_open_recovers_on_success():
    reg = registry(failure_threshold=2, cooldown_s=100.0)

    def boom():
        raise RuntimeError("x")

    for i in range(2):
        reg.fetch("eastmoney.news", f"k{i}", boom, now=1000.0)
    assert not reg.available("eastmoney.news", now=1050.0)

    # 冷却结束 → 半开：放一次真实试探
    assert reg.available("eastmoney.news", now=1100.1)
    recovered = reg.fetch("eastmoney.news", "good", lambda: {"items": [1]}, now=1100.2)

    assert recovered.ok
    assert reg.status("eastmoney.news", now=1100.2) == es.STATUS_OK
    assert reg.available("eastmoney.news", now=1100.2)


def test_half_open_failure_closes_the_door_again():
    """半开只放**一次**：试探再失败就立刻重新关上门，不能变成"每次都试"。"""
    reg = registry(failure_threshold=2, cooldown_s=100.0)

    def boom():
        raise RuntimeError("still down")

    for i in range(2):
        reg.fetch("eastmoney.news", f"k{i}", boom, now=1000.0)

    reg.fetch("eastmoney.news", "probe", boom, now=1100.5)
    assert not reg.available("eastmoney.news", now=1101.0)
    assert reg.available("eastmoney.news", now=1200.6)


def test_empty_result_is_not_a_failure_but_is_not_cached_either():
    """'这只股票今天没新闻' 是合法答案，不是上游故障。

    空（`None` / 空列表）不能进缓存，否则一次短暂的空返回会在 TTL 内把真相挡住。
    """
    reg = registry()
    calls = []

    def empty():
        calls.append(1)
        return []

    first = reg.fetch("eastmoney.news", "600519", empty, now=1000.0)
    second = reg.fetch("eastmoney.news", "600519", empty, now=1001.0)

    assert first.ok and second.ok
    assert not first.cached and not second.cached
    assert len(calls) == 2, "空结果不入缓存"
    assert reg.status("eastmoney.news", now=1001.0) == es.STATUS_OK


def test_empty_shell_payload_is_cached_and_that_is_intentional():
    """`{"items": []}` 是真实新闻接口的空返回形状：它**会**被缓存。

    这是刻意的取舍：它确实表达了"当前没有消息"，缓存 10 分钟省下配额，
    而新消息的滞后上限就是这一个 TTL（新闻 TTL 本身就是 10 分钟）。
    """
    reg = registry()
    calls = []

    def empty_shell():
        calls.append(1)
        return {"items": []}

    reg.fetch("eastmoney.news", "600519", empty_shell, now=1000.0)
    second = reg.fetch("eastmoney.news", "600519", empty_shell, now=1001.0)

    assert second.cached is True
    assert len(calls) == 1


def test_unknown_dataset_does_not_block_or_crash():
    """声明写错（数据集名拼错）不能变成"悄悄少一个能力"，也不能把取数搞崩。"""
    reg = registry()

    result = reg.fetch("who.knows", "k", lambda: {"x": 1}, now=1000.0)

    assert result.ok and result.value == {"x": 1}
    assert reg.available("who.knows")


def test_degraded_is_visible_before_it_is_down():
    """失败一两次还没到阈值时，/health 要能看出"这个源在抖"。"""
    reg = registry(failure_threshold=3)

    def boom():
        raise RuntimeError("flaky")

    reg.fetch("eastmoney.news", "k", boom, now=1000.0)

    assert reg.status("eastmoney.news", now=1000.0) == es.STATUS_DEGRADED
    assert reg.available("eastmoney.news", now=1000.0)
    snapshot = reg.snapshot(now=1000.0)
    assert snapshot["providers"]["eastmoney"]["failures"] == 1
    assert snapshot["datasets"]["eastmoney.news"]["ttl_s"] == 600
    assert snapshot["datasets"]["eastmoney.news"]["available"] is True


def test_source_labels_are_human_readable_for_the_data_block():
    """数据块里要写清"谁给的"，写 `eastmoney.news` 这种内部名等于没写。"""
    assert es.source_label("eastmoney.news") == "东方财富·个股新闻"
    assert es.source_label("eastmoney.financial") == "东方财富·财务摘要"


def test_default_registry_is_the_shared_singleton():
    """健康状态必须跨请求共享，否则"连续失败"永远攒不到阈值。"""
    from agent import external_source

    assert external_source.available("eastmoney.news") is True
    assert es.REGISTRY is external_source.REGISTRY


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
