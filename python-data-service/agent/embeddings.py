"""向量化：OpenAI 兼容的 embedding 接口 + 本地缓存（M2）。

供应商：智谱（`.env.example` 里已经有 `ZHIPU_API_KEY` / `ZHIPU_EMBEDDING_MODEL=embedding-2`），
或任何 OpenAI 兼容端点（硅基流动的 `BAAI/bge-m3` 等）。

三个刻意的设计：

1. **fail-open**：没配 key、网络失败、返回格式不对 —— 一律返回 None，
   调用方退化成纯关键词检索（也就是 M1 的行为）。向量是<b>增强</b>，不是依赖：
   记忆系统绝不能因为第三方 embedding 服务抖动而影响用户对话。
2. **本地缓存**：embedding 按内容确定，同一条事实不该每轮对话重算。
   落一份 jsonl（一行一条 `sha256 → 向量`），进程内再套一层 dict。
3. **不发 `dimensions`**：bge-m3 这类模型不支持 Matryoshka 截断，带了会直接 400
   （TencentDB Agent Memory 的文档里专门提示过这一点）。维度由模型自己决定。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
_DEFAULT_MODEL = "embedding-2"

_lock = threading.Lock()
_cache: dict[str, list[float]] = {}
_cache_loaded = False


def _config() -> dict:
    # 延迟读取环境变量：.env 由 llm_service 在导入时加载，早读会拿到空值
    api_key = (os.getenv("EMBEDDING_API_KEY") or os.getenv("ZHIPU_API_KEY") or "").strip()
    model = (os.getenv("EMBEDDING_MODEL") or os.getenv("ZHIPU_EMBEDDING_MODEL")
             or _DEFAULT_MODEL).strip()
    base_url = (os.getenv("EMBEDDING_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    return {"api_key": api_key, "model": model, "base_url": base_url}


def is_enabled() -> bool:
    """配了 key 才算启用；没配就安静地退化（不报错、不刷日志）。"""
    key = _config()["api_key"]
    return bool(key) and key != "your-api-key-here"


def _cache_path() -> Path:
    raw = os.getenv("EMBEDDING_CACHE_PATH", "./.memory_index/embeddings.jsonl")
    return Path(raw)


def _timeout() -> float:
    try:
        return float(os.getenv("EMBEDDING_TIMEOUT", "8"))
    except ValueError:
        return 8.0


def _cache_key(text: str) -> str:
    model = _config()["model"]
    return hashlib.sha256(f"{model}:{text}".encode("utf-8")).hexdigest()


def _load_cache() -> None:
    global _cache_loaded
    if _cache_loaded:
        return
    with _lock:
        if _cache_loaded:
            return
        path = _cache_path()
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = record.get("k")
                    vector = record.get("v")
                    if isinstance(key, str) and isinstance(vector, list) and vector:
                        _cache[key] = vector
        except Exception as exc:  # noqa: BLE001
            logger.debug("embedding 缓存读取失败: %s", exc)
        _cache_loaded = True


def _persist(new_records: list[dict]) -> None:
    if not new_records:
        return
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for record in new_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 —— 缓存写不进去只是慢一点，不该影响功能
        logger.debug("embedding 缓存写入失败: %s", exc)


def _normalize(vector: list[float]) -> list[float]:
    norm = sum(value * value for value in vector) ** 0.5
    if not norm:
        return vector
    return [value / norm for value in vector]


def embed(texts) -> list[list[float]] | None:
    """批量向量化。任何失败都返回 None（调用方退化成关键词检索）。"""
    if not texts:
        return []
    if not is_enabled():
        return None

    _load_cache()
    wanted = [str(text) for text in texts]
    keys = [_cache_key(text) for text in wanted]

    missing_indexes = [i for i, key in enumerate(keys) if key not in _cache]
    if missing_indexes:
        config = _config()
        payload = {"model": config["model"], "input": [wanted[i] for i in missing_indexes]}
        try:
            response = requests.post(
                f"{config['base_url']}/embeddings",
                json=payload,
                headers={"Authorization": f"Bearer {config['api_key']}",
                         "Content-Type": "application/json"},
                timeout=_timeout(),
            )
            response.raise_for_status()
            body = response.json()
            vectors = [item.get("embedding") for item in (body.get("data") or [])]
            if len(vectors) != len(missing_indexes):
                logger.warning("embedding 返回数量不符：期望 %d 实际 %d",
                               len(missing_indexes), len(vectors))
                return None
            records = []
            for index, vector in zip(missing_indexes, vectors):
                if not isinstance(vector, list) or not vector:
                    return None
                normalized = _normalize([float(value) for value in vector])
                _cache[keys[index]] = normalized
                records.append({"k": keys[index], "v": normalized})
            _persist(records)
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedding 调用失败，本次退化为关键词检索: %s", exc)
            return None

    return [_cache[key] for key in keys]


def embed_one(text) -> list[float] | None:
    vectors = embed([text])
    if not vectors:
        return None
    return vectors[0]


def cache_stats() -> dict:
    _load_cache()
    return {"enabled": is_enabled(), "model": _config()["model"], "entries": len(_cache),
            "path": str(_cache_path())}


def reset_cache_for_tests() -> None:
    """仅供测试：清掉进程内缓存，让下一次调用重新读文件。"""
    global _cache_loaded
    with _lock:
        _cache.clear()
        _cache_loaded = False
