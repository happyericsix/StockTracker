"""
embedding_helper.py —— 智谱 Embedding API 封装

为什么用智谱：
- 国内访问快
- 中文效果优秀（专门针对中文训练）
- 新用户送 2000 万 tokens 免费额度（够用半年）
- API 兼容 OpenAI 风格，简单

环境变量：
- ZHIPU_API_KEY: 必填
- ZHIPU_EMBEDDING_MODEL: 默认 embedding-2
  (备选: embedding-3, bge-large-zh)
"""
import logging
import os
from pathlib import Path
from typing import List

import requests

logger = logging.getLogger(__name__)


# ===== 加载 .env 文件 (兜底逻辑，避免 llm_service 没先 import 时读不到) =====
def _load_dotenv():
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        logger.warning(f"加载 .env 失败: {e}")


_load_dotenv()

ZHIPU_API_KEY = os.getenv("ZHIPU_API_KEY", "").strip()
ZHIPU_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = os.getenv("ZHIPU_EMBEDDING_MODEL", "embedding-2")
EMBEDDING_DIM = 1024  # embedding-2 的向量维度
TIMEOUT = 30


def _is_available() -> bool:
    return bool(ZHIPU_API_KEY) and ZHIPU_API_KEY != "your-key-here"


def embed_texts(texts: List[str], model: str = None) -> List[List[float]]:
    """
    把一段文本列表转成向量列表

    Args:
        texts: 文本列表（最多 64 条/次）
        model: 模型名，默认 embedding-2

    Returns:
        每个文本对应的向量（embedding-2 是 1024 维）
    """
    if not _is_available():
        raise RuntimeError(
            "ZHIPU_API_KEY 未配置。请在 .env 里设置 ZHIPU_API_KEY=你的key"
        )

    if not texts:
        return []

    model = model or DEFAULT_MODEL

    # 智谱 API 限制单次最多 64 条
    if len(texts) > 64:
        # 分批调用
        all_embeddings = []
        for i in range(0, len(texts), 64):
            all_embeddings.extend(embed_texts(texts[i:i+64], model))
        return all_embeddings

    url = f"{ZHIPU_BASE_URL}/embeddings"
    headers = {
        "Authorization": f"Bearer {ZHIPU_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": texts,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        embeddings = [item["embedding"] for item in data["data"]]
        logger.info(f"智谱 Embedding 成功: {len(texts)} 条, 维度 {len(embeddings[0]) if embeddings else 0}")
        return embeddings
    except requests.exceptions.HTTPError as e:
        logger.error(f"智谱 API 错误: {e.response.status_code} {e.response.text[:300]}")
        raise
    except Exception as e:
        logger.error(f"智谱 Embedding 异常: {e}")
        raise


def embed_one(text: str, model: str = None) -> List[float]:
    """单条文本 Embedding"""
    return embed_texts([text], model)[0]
