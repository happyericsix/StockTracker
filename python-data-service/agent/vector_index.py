"""向量索引 + RRF 融合（M2）。

<h3>两套后端，同一个接口</h3>
- {@code NumpyVectorIndex}（默认）：进程内暴力余弦。单用户语料是几十到几百条，
  这个规模下它比向量库更快、更简单，也没有"索引与事实源不一致"的问题。
- {@code ChromaVectorIndex}：落盘的持久化索引，`VECTOR_BACKEND=chroma` 时启用。
  什么时候该切（触发条件，不是凭感觉）：
  1. **多实例/多 worker 部署** —— 现在每个进程各建一份索引、TTL 还各不相同，这是第一个真触发点；
  2. 语料涨到每用户几千条，或者要做跨用户检索；
  3. 需要元数据过滤 + 大批量增量更新（不再全量重建）；
  4. 不想在冷启动时等一次全量向量化。
  两套后端对外只有 build/search/invalidate 三个方法，切换只改一个环境变量。

<h3>为什么用 RRF 而不是"向量分数 + 关键词分数"相加</h3>
两路召回的分数量纲完全不同（余弦 0~1 vs 关键词命中次数），直接相加需要对权重调参，
而且换 embedding 模型就得重调。RRF（Reciprocal Rank Fusion）只用<b>名次</b>：
`score = Σ 1/(k + rank)`，k 取 60（Graphiti / TencentDB Agent Memory 都用这个值），
不需要调参、对量纲免疫。Java 的关键词排序（含标的全匹配、类型优先级、用户确认）
作为一路名次保留，恰好弥补纯向量的短板 —— 股票代码这种精确匹配向量并不擅长。

<h3>失败行为</h3>
embedding 不可用 → `search()` 返回空 → 融合结果退化成关键词顺序。
Chroma 初始化失败 → 自动退回 numpy 实现。整条链路没有"向量不可用就报错"的分支。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from agent import embeddings, memory_store

logger = logging.getLogger(__name__)

RRF_K = 60
INDEX_TTL_SECONDS = float(300)
MAX_INDEX_ITEMS = 500
BACKEND_NUMPY = "numpy"
BACKEND_CHROMA = "chroma"


def reciprocal_rank_fusion(rankings, k: int = RRF_K) -> dict:
    """把多路"有序 id 列表"融成一个 {id: 分数} 字典。

    只用名次、不用分数：不同召回路径的分数量级不可比，名次可比。
    """
    fused: dict = {}
    for ranking in rankings or []:
        for rank, item_id in enumerate(ranking or []):
            if item_id is None:
                continue
            fused[item_id] = fused.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return fused


def _fact_text(fact: dict) -> str:
    parts = [str(fact.get("subject") or ""), str(fact.get("predicate") or ""),
             str(fact.get("object") or ""), str(fact.get("factType") or "")]
    return " ".join(part for part in parts if part)


def _lesson_text(lesson: dict) -> str:
    parts = [str(lesson.get("taskType") or ""), str(lesson.get("symptom") or ""),
             str(lesson.get("resolution") or ""), str(lesson.get("reusableRule") or "")]
    return " ".join(part for part in parts if part)


def _episode_text(episode: dict) -> str:
    points = episode.get("keyPoints")
    joined = "；".join(str(point) for point in points) if isinstance(points, list) else ""
    return " ".join(part for part in [str(episode.get("summary") or ""), joined] if part)


_TEXT_BUILDERS = {"fact": _fact_text, "lesson": _lesson_text, "episode": _episode_text}


def _build_items(corpus: dict) -> list:
    """把 Java 给的语料转成索引项（kind / id / text / payload）。"""
    items = []
    for kind, key in (("fact", "facts"), ("lesson", "lessons"), ("episode", "episodes")):
        for entry in corpus.get(key) or []:
            if not isinstance(entry, dict):
                continue
            text = _TEXT_BUILDERS[kind](entry)
            if not text.strip():
                continue
            items.append({"kind": kind, "id": entry.get("id"), "text": text, "payload": entry})
            if len(items) >= MAX_INDEX_ITEMS:
                return items
    return items


class NumpyVectorIndex:
    """进程内暴力余弦索引（默认实现，零外部依赖）。"""

    def __init__(self, ttl_seconds: float = INDEX_TTL_SECONDS):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._cache: dict = {}   # user_id -> {"items": [...], "vectors": [...], "built_at": ts}

    def backend(self) -> str:
        return BACKEND_NUMPY

    def _build(self, user_id):
        items = _build_items(memory_store.index_corpus(user_id))
        if not items:
            return {"items": [], "vectors": [], "built_at": time.time()}
        vectors = embeddings.embed([item["text"] for item in items])
        if vectors is None:
            return {"items": [], "vectors": [], "built_at": time.time()}
        return {"items": items, "vectors": vectors, "built_at": time.time()}

    def _get(self, user_id, force: bool = False):
        if not embeddings.is_enabled():
            return {"items": [], "vectors": [], "built_at": time.time()}
        with self._lock:
            cached = self._cache.get(user_id)
            fresh = cached and not force and (time.time() - cached["built_at"]) < self._ttl
            if fresh:
                return cached
        built = self._build(user_id)
        with self._lock:
            self._cache[user_id] = built
        return built

    def search(self, user_id, query, kinds=None, top_k: int = 10) -> list:
        """语义召回，返回 [(item, score)]，按相似度降序。任何不可用情况都返回空列表。"""
        if not user_id or not query:
            return []
        try:
            index = self._get(user_id)
        except Exception as exc:  # noqa: BLE001 —— 索引构建失败不影响对话
            logger.debug("语义索引构建失败 user=%s: %s", user_id, exc)
            return []
        if not index["items"]:
            return []

        query_vector = embeddings.embed_one(str(query))
        if query_vector is None:
            return []

        scored = []
        for item, vector in zip(index["items"], index["vectors"]):
            if kinds and item["kind"] not in kinds:
                continue
            # 向量都已归一化，点积即余弦
            similarity = sum(a * b for a, b in zip(query_vector, vector))
            scored.append((item, similarity))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:max(1, top_k)]

    def invalidate(self, user_id=None):
        """写操作（巩固/撤回）之后可以主动失效，让下一轮拿到新语料。"""
        with self._lock:
            if user_id is None:
                self._cache.clear()
            else:
                self._cache.pop(user_id, None)

    def stats(self) -> dict:
        with self._lock:
            return {"backend": BACKEND_NUMPY, "users": len(self._cache),
                    "items": sum(len(entry["items"]) for entry in self._cache.values()),
                    "enabled": embeddings.is_enabled()}


class ChromaVectorIndex:
    """落盘的 Chroma 索引（`VECTOR_BACKEND=chroma`）。

    <h3>只当"向量仓库"用，不让它碰 embedding</h3>
    collection 用 `embedding_function=None` 创建，向量全部由我们自己算（走智谱/OpenAI 兼容接口，
    并且有本地缓存）。这样换 embedding 供应商不需要动索引代码，也不会出现
    "Chroma 偷偷调了别的模型" 这种难查的问题。

    <h3>失效策略</h3>
    `invalidate()` 只清进程内的"新鲜度"记录；下次访问时重新从 Java 拉语料，
    upsert 新条目并删除已经不在语料里的旧 id —— 事实被取代/撤回后，
    索引里那份不能继续被召回。这一步是"派生索引"和"事实源"保持一致的关键。
    """

    def __init__(self, path: str = None, ttl_seconds: float = INDEX_TTL_SECONDS):
        import chromadb  # 延迟导入：没装 chromadb 时不影响到 numpy 后端

        self._path = path or os.getenv("CHROMA_DATA_DIR", "./chroma_data")
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._client = chromadb.PersistentClient(path=self._path)
        self._built_at: dict = {}

    def backend(self) -> str:
        return BACKEND_CHROMA

    def _collection(self, user_id):
        name = f"memory_user_{user_id}"
        try:
            # 归一化向量下，cosine 与 l2 的排序结果一致；这里显式声明空间只是更贴合语义
            return self._client.get_or_create_collection(
                name=name, metadata={"hnsw:space": "cosine"})
        except Exception:  # noqa: BLE001 —— 老/新版本对 metadata 的处理不一样，退回默认空间
            return self._client.get_or_create_collection(name=name)

    def _ensure_fresh(self, user_id, force: bool = False) -> bool:
        if not embeddings.is_enabled():
            return False
        with self._lock:
            built_at = self._built_at.get(user_id, 0)
            if not force and (time.time() - built_at) < self._ttl:
                return True

        items = _build_items(memory_store.index_corpus(user_id))
        if not items:
            return False
        vectors = embeddings.embed([item["text"] for item in items])
        if vectors is None:
            return False

        collection = self._collection(user_id)
        ids = [str(item["id"]) for item in items]
        metadatas = [{"kind": item["kind"], "payload": json.dumps(item["payload"], ensure_ascii=False)}
                     for item in items]

        # 清掉已经不在语料里的旧条目（事实被取代/撤回后不能再被召回）
        try:
            existing = set(collection.get(include=[]).get("ids") or [])
        except Exception:  # noqa: BLE001
            existing = set()
        stale = existing - set(ids)
        if stale:
            try:
                collection.delete(ids=list(stale))
            except Exception as exc:  # noqa: BLE001
                logger.debug("Chroma 清理陈旧条目失败: %s", exc)

        collection.upsert(ids=ids, embeddings=vectors, documents=[item["text"] for item in items],
                          metadatas=metadatas)
        with self._lock:
            self._built_at[user_id] = time.time()
        return True

    def search(self, user_id, query, kinds=None, top_k: int = 10) -> list:
        if not user_id or not query:
            return []
        try:
            if not self._ensure_fresh(user_id):
                return []
            query_vector = embeddings.embed_one(str(query))
            if query_vector is None:
                return []
            where = {"kind": {"$in": list(kinds)}} if kinds else None
            result = self._collection(user_id).query(
                query_embeddings=[query_vector], n_results=max(1, top_k), where=where,
                include=["metadatas", "distances"])
        except Exception as exc:  # noqa: BLE001 —— 索引不可用就退化成关键词检索
            logger.warning("Chroma 检索失败，本轮退化为关键词检索: %s", exc)
            return []

        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        hits = []
        for metadata, distance in zip(metadatas, distances):
            try:
                payload = json.loads(metadata.get("payload") or "{}")
            except (json.JSONDecodeError, TypeError, AttributeError):
                continue
            kind = metadata.get("kind")
            hits.append(({"kind": kind, "id": payload.get("id"), "text": "", "payload": payload},
                         # 只用于展示/排序提示；RRF 只看名次，所以距离的具体刻度不影响结果
                         1.0 - float(distance)))
        return hits

    def invalidate(self, user_id=None):
        with self._lock:
            if user_id is None:
                self._built_at.clear()
            else:
                self._built_at.pop(user_id, None)

    def stats(self) -> dict:
        with self._lock:
            return {"backend": BACKEND_CHROMA, "path": self._path,
                    "users": len(self._built_at), "enabled": embeddings.is_enabled()}


def configured_backend() -> str:
    return (os.getenv("VECTOR_BACKEND") or BACKEND_NUMPY).strip().lower()


def _create_index():
    """按配置挑后端；Chroma 起不来就退回 numpy（绝不让索引问题影响对话）。"""
    if configured_backend() == BACKEND_CHROMA:
        try:
            index = ChromaVectorIndex()
            logger.info("语义索引后端：chroma (%s)", index.stats().get("path"))
            return index
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chroma 不可用，回退到进程内索引: %s", exc)
    return NumpyVectorIndex()


_INDEX = _create_index()


def semantic_search(user_id, query, kinds=None, top_k: int = 10) -> list:
    return _INDEX.search(user_id, query, kinds, top_k)


def invalidate(user_id=None) -> None:
    _INDEX.invalidate(user_id)


def stats() -> dict:
    return _INDEX.stats()
