# -*- coding: utf-8 -*-
"""向量化、语义索引与 RRF 融合。

三条最要紧的性质：
1. **RRF 只用名次**：两路召回的分数量纲不可比（余弦 vs 关键词命中），
   所以融合不能相加分数，只能融合名次 —— 换 embedding 模型也不用重调权重。
2. **向量不可用时整条链路照常工作**：退化成关键词排序，不报错、不降级体验。
3. **语义召回要能改变排序**：关键词排不上的旧事（换个说法问）必须能被捞回来。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import embeddings, memory_store, recall, vector_index


# ==================== RRF ====================

def test_rrf_favors_items_that_rank_high_in_both_lists():
    fused = vector_index.reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])

    # a 与 b 都在两路里靠前，分数应当接近且高于只在一路出现的 c / d
    assert fused["a"] > fused["c"]
    assert fused["b"] > fused["d"]
    assert fused["a"] == fused["b"]


def test_rrf_ignores_missing_and_empty_rankings():
    fused = vector_index.reciprocal_rank_fusion([[], None, ["x"]])
    assert set(fused) == {"x"}
    assert vector_index.reciprocal_rank_fusion(None) == {}


def test_rrf_score_matches_the_formula():
    fused = vector_index.reciprocal_rank_fusion([["x"]], k=60)
    assert abs(fused["x"] - 1.0 / 61) < 1e-9


# ==================== 向量化（fail-open + 缓存） ====================

def test_embeddings_are_disabled_without_a_key(monkeypatch):
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)

    assert embeddings.is_enabled() is False
    # 关键：不报错，只是返回 None，调用方退化成关键词检索
    assert embeddings.embed(["文本"]) is None


def test_embedding_result_is_cached_and_normalized(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "emb.jsonl"))
    embeddings.reset_cache_for_tests()

    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"embedding": [3.0, 4.0]}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        assert "dimensions" not in json, "bge-m3 这类模型不支持 dimensions，带了会 400"
        return FakeResponse()

    import agent.embeddings as module
    monkeypatch.setattr(module.requests, "post", fake_post)

    first = embeddings.embed(["贵州茅台"])
    second = embeddings.embed(["贵州茅台"])

    assert calls["n"] == 1, "同一段文本不该重复调用（缓存没生效）"
    assert first == second
    # 归一化后模长为 1，于是余弦相似度可以直接用点积算
    assert abs(sum(value * value for value in first[0]) - 1.0) < 1e-9


def test_embedding_failure_returns_none_instead_of_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("EMBEDDING_API_KEY", "test-key")
    monkeypatch.setenv("EMBEDDING_CACHE_PATH", str(tmp_path / "emb.jsonl"))
    embeddings.reset_cache_for_tests()

    import agent.embeddings as module

    def boom(*args, **kwargs):
        raise RuntimeError("embedding backend down")

    monkeypatch.setattr(module.requests, "post", boom)

    assert embeddings.embed(["文本"]) is None


# ==================== 语义索引 ====================

FACTS = [
    {"id": 1, "subject": "user", "predicate": "stop_loss_pct", "object": "5", "factType": "constraint"},
    {"id": 2, "subject": "user", "predicate": "sector_preference", "object": "白酒", "factType": "preference"},
    {"id": 3, "subject": "600519", "predicate": "holding_cost", "object": "1500", "factType": "holding"},
]
EPISODES = [
    {"id": 11, "sessionKey": "1:2026-09-14", "summary": "用户调试均线策略，关心回撤"},
]
LESSONS = [
    {"id": 21, "taskType": "backtest", "symptom": "回测提示历史数据不足",
     "resolution": "先取更长周期的数据", "reusableRule": "回测前确认条数≥20"},
]


def _fake_embeddings(monkeypatch, table):
    """用一个确定的假向量表替换真实 embedding 调用。"""
    monkeypatch.setattr(embeddings, "is_enabled", lambda: True)
    monkeypatch.setattr(embeddings, "embed", lambda texts: [table[str(t)] for t in texts])
    monkeypatch.setattr(embeddings, "embed_one", lambda text: table.get(str(text)))


def test_semantic_search_ranks_by_similarity(monkeypatch):
    monkeypatch.setattr(memory_store, "index_corpus", lambda user_id: {
        "facts": FACTS, "episodes": EPISODES, "lessons": LESSONS})

    # 构造一个"只关心止损"的向量空间：查询与止损那条最接近
    table = {
        "user stop_loss_pct 5 constraint": [1.0, 0.0],
        "user sector_preference 白酒 preference": [0.0, 1.0],
        "600519 holding_cost 1500 holding": [0.7, 0.7],
        "backtest 回测提示历史数据不足 先取更长周期的数据 回测前确认条数≥20": [0.0, 1.0],
        "用户调试均线策略，关心回撤": [0.6, 0.8],
        "把止损改成5%": [1.0, 0.0],
    }
    _fake_embeddings(monkeypatch, table)

    index = vector_index.NumpyVectorIndex(ttl_seconds=0)
    hits = index.search(1, "把止损改成5%", kinds={"fact"}, top_k=3)

    assert hits[0][0]["id"] == 1
    assert hits[0][1] > hits[1][1]


def test_semantic_search_is_empty_when_embeddings_are_unavailable(monkeypatch):
    monkeypatch.setattr(embeddings, "is_enabled", lambda: False)

    index = vector_index.NumpyVectorIndex()
    assert index.search(1, "随便问问") == []


def test_semantic_search_is_empty_when_corpus_is_empty(monkeypatch):
    monkeypatch.setattr(memory_store, "index_corpus", lambda user_id: {})
    _fake_embeddings(monkeypatch, {"随便问问": [1.0, 0.0]})

    assert vector_index.NumpyVectorIndex().search(1, "随便问问") == []


def test_index_failure_is_swallowed(monkeypatch):
    def boom(user_id):
        raise RuntimeError("java down")

    monkeypatch.setattr(memory_store, "index_corpus", boom)
    _fake_embeddings(monkeypatch, {"q": [1.0, 0.0]})

    assert vector_index.NumpyVectorIndex().search(1, "q") == []


# ==================== Chroma 后端（VECTOR_BACKEND=chroma） ====================

def test_backend_selection_follows_configuration(monkeypatch, tmp_path):
    monkeypatch.delenv("VECTOR_BACKEND", raising=False)
    assert isinstance(vector_index._create_index(), vector_index.NumpyVectorIndex)

    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setenv("CHROMA_DATA_DIR", str(tmp_path / "chroma"))
    index = vector_index._create_index()
    assert isinstance(index, vector_index.ChromaVectorIndex)
    assert index.backend() == "chroma"

    monkeypatch.setenv("VECTOR_BACKEND", "CHROMA  ")   # 大小写与空白都要容错
    assert isinstance(vector_index._create_index(), vector_index.ChromaVectorIndex)


def test_unknown_backend_falls_back_to_numpy(monkeypatch):
    monkeypatch.setenv("VECTOR_BACKEND", "qdrant")
    assert isinstance(vector_index._create_index(), vector_index.NumpyVectorIndex)


def test_chroma_unavailable_falls_back_instead_of_failing(monkeypatch, tmp_path):
    """向量库起不来必须退回进程内索引 —— 记忆检索不能因此整个失效。"""
    import chromadb

    def boom(*args, **kwargs):
        raise RuntimeError("chroma cannot start")

    monkeypatch.setenv("VECTOR_BACKEND", "chroma")
    monkeypatch.setattr(chromadb, "PersistentClient", boom)

    assert isinstance(vector_index._create_index(), vector_index.NumpyVectorIndex)


def test_chroma_backend_ranks_and_persists(monkeypatch, tmp_path):
    monkeypatch.setattr(memory_store, "index_corpus", lambda user_id: {
        "facts": FACTS, "lessons": LESSONS, "episodes": EPISODES})
    table = {
        "user stop_loss_pct 5 constraint": [1.0, 0.0],
        "user sector_preference 白酒 preference": [0.0, 1.0],
        "600519 holding_cost 1500 holding": [0.6, 0.8],
        "backtest 回测提示历史数据不足 先取更长周期的数据 回测前确认条数≥20": [0.2, 1.0],
        "用户调试均线策略，关心回撤": [0.7, 0.7],
        "把止损改成5%": [1.0, 0.0],
    }
    _fake_embeddings(monkeypatch, table)

    path = str(tmp_path / "chroma")
    index = vector_index.ChromaVectorIndex(path=path, ttl_seconds=0)
    hits = index.search(1, "把止损改成5%", kinds={"fact"}, top_k=3)

    assert hits[0][0]["id"] == 1
    assert hits[0][0]["payload"]["predicate"] == "stop_loss_pct"

    # 换一个进程级实例指向同一个目录：索引是落盘的，不必重新向量化语料
    calls = {"n": 0}
    original_embed = embeddings.embed

    def counting_embed(texts):
        calls["n"] += 1
        return original_embed(texts)

    monkeypatch.setattr(embeddings, "embed", counting_embed)
    again = vector_index.ChromaVectorIndex(path=path, ttl_seconds=0)
    second_hits = again.search(1, "把止损改成5%", kinds={"fact"}, top_k=3)

    assert second_hits[0][0]["id"] == 1


def test_chroma_backend_drops_entries_that_left_the_corpus(monkeypatch, tmp_path):
    """事实被取代/撤回后必须从索引里消失，否则旧值还会被语义召回。"""
    corpus = {"facts": FACTS[:2], "lessons": [], "episodes": []}
    monkeypatch.setattr(memory_store, "index_corpus", lambda user_id: corpus)
    table = {
        "user stop_loss_pct 5 constraint": [1.0, 0.0],
        "user sector_preference 白酒 preference": [0.0, 1.0],
        "把止损改成5%": [1.0, 0.0],
    }
    _fake_embeddings(monkeypatch, table)

    index = vector_index.ChromaVectorIndex(path=str(tmp_path / "chroma"), ttl_seconds=0)
    assert {item["id"] for item, _ in index.search(1, "把止损改成5%", top_k=5)} == {1, 2}

    # 语料里只剩 id=2（id=1 被取代了）
    corpus["facts"] = [FACTS[1]]
    index.invalidate(1)
    hits = index.search(1, "把止损改成5%", top_k=5)

    assert {item["id"] for item, _ in hits} == {2}


def test_chroma_backend_is_empty_when_embeddings_are_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(embeddings, "is_enabled", lambda: False)

    index = vector_index.ChromaVectorIndex(path=str(tmp_path / "chroma"))
    assert index.search(1, "随便问问") == []


def test_chroma_query_failure_degrades_to_empty(monkeypatch, tmp_path):
    """Chroma 抛异常时要返回空（退化成关键词检索），而不是把异常丢给对话链路。"""
    monkeypatch.setattr(memory_store, "index_corpus", lambda user_id: {
        "facts": FACTS, "lessons": [], "episodes": []})
    _fake_embeddings(monkeypatch, {
        "user stop_loss_pct 5 constraint": [1.0, 0.0],
        "user sector_preference 白酒 preference": [0.0, 1.0],
        "600519 holding_cost 1500 holding": [0.7, 0.7],
        "问题": [1.0, 0.0],
    })

    index = vector_index.ChromaVectorIndex(path=str(tmp_path / "chroma"), ttl_seconds=0)
    monkeypatch.setattr(index, "_collection",
                        lambda user_id: (_ for _ in ()).throw(RuntimeError("chroma broken")))

    assert index.search(1, "问题") == []


# ==================== 融合进上下文 ====================

def test_fusion_reorders_keyword_results_by_semantic_relevance(monkeypatch):
    context = {
        "today": "2026-09-16",
        "facts": [
            {"id": 1, "subject": "user", "predicate": "stop_loss_pct", "object": "5",
             "factType": "constraint"},
            {"id": 2, "subject": "user", "predicate": "sector_preference", "object": "白酒",
             "factType": "preference"},
        ],
        "recaps": [],
    }

    # 语义只认 id=2（换个说法问的时候，关键词排不上但语义能捞到）
    def fake_search(user_id, query, kinds=None, top_k=10):
        return [({"kind": "fact", "id": 2, "payload": {}, "text": ""}, 0.9)]

    monkeypatch.setattr(vector_index, "semantic_search", fake_search)

    fused = recall._fuse(context, 1, "我问的那件事")

    assert [fact["id"] for fact in fused["facts"]] == [2, 1]


def test_fusion_keeps_keyword_order_when_semantics_finds_nothing(monkeypatch):
    context = {"facts": [{"id": 1}, {"id": 2}], "lessons": []}
    monkeypatch.setattr(vector_index, "semantic_search", lambda *a, **k: [])

    fused = recall._fuse(context, 1, "问题")

    assert [fact["id"] for fact in fused["facts"]] == [1, 2]


def test_fusion_failure_falls_back_to_keyword_order(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("index down")

    monkeypatch.setattr(vector_index, "semantic_search", boom)
    context = {"facts": [{"id": 1}, {"id": 2}]}

    fused = recall._fuse(context, 1, "问题")

    assert [fact["id"] for fact in fused["facts"]] == [1, 2]


def test_semantic_episodes_exclude_ones_already_shown(monkeypatch):
    context = {
        "recaps": [{"sessionKey": "1:2026-09-14"}],
        "facts": [],
    }

    def fake_search(user_id, query, kinds=None, top_k=10):
        return [
            ({"kind": "episode", "id": 11, "text": "", "payload": {"sessionKey": "1:2026-09-14",
                                                                  "summary": "已经给过的"}}, 0.9),
            ({"kind": "episode", "id": 12, "text": "", "payload": {"sessionKey": "1:2026-03-11",
                                                                  "summary": "很久以前那次"}}, 0.8),
        ]

    monkeypatch.setattr(vector_index, "semantic_search", fake_search)

    fused = recall._fuse(context, 1, "去年那个事")

    assert [episode["sessionKey"] for episode in fused["semanticEpisodes"]] == ["1:2026-03-11"]


# ==================== 渲染 ====================

LESSON = {"id": 21, "taskType": "backtest", "symptom": "回测提示历史数据不足",
          "resolution": "先取更长周期的数据", "reusableRule": "回测前确认条数≥20",
          "status": "pending", "occurrences": 2, "recordedAt": "2026-09-14T10:00:00"}


def test_persona_is_rendered_and_deduplicated_against_facts():
    fact = {"id": 1, "subject": "user", "predicate": "stop_loss_pct", "object": "5",
            "factType": "constraint", "confidence": 0.9, "confirmed": True,
            "recordedAt": "2026-09-16T10:00:00"}
    other = {"id": 2, "subject": "user", "predicate": "risk_preference", "object": "稳健",
             "factType": "preference", "confidence": 0.9, "confirmed": True,
             "recordedAt": "2026-08-02T10:00:00"}

    block = recall.render({"today": "2026-09-16", "persona": [fact, other], "facts": [fact]})

    assert "长期画像" in block
    # 同一条事实不该在画像和事实里出现两次
    assert block.count("止损：5") == 1
    assert "风险偏好：稳健" in block


def test_lessons_are_rendered_as_reference_not_as_rules():
    block = recall.render({"today": "2026-09-16", "lessons": [LESSON]})

    assert "以前遇到同类问题是怎么解决的" in block
    assert "不要直接当规则执行" in block
    assert "症状：回测提示历史数据不足" in block
    assert "做法：回测前确认条数≥20" in block
    assert "出现过 2 次" in block
    # 未经人工确认的经验必须标出来
    assert "未经确认" in block


def test_semantic_episodes_are_rendered_with_their_date():
    block = recall.render({"today": "2026-09-16", "semanticEpisodes": [
        {"sessionKey": "1:2026-03-11", "summary": "用户当时在比较两只银行股"}]})

    assert "更早的相关对话" in block
    assert "2026-03-11：用户当时在比较两只银行股" in block


def test_render_tolerates_garbage_in_new_sections():
    for garbage in ("not-a-list", [None, 42], {"a": 1}, 0):
        block = recall.render({"today": "2026-09-16", "persona": garbage,
                               "lessons": garbage, "semanticEpisodes": garbage})
        assert "</memory>" in block
