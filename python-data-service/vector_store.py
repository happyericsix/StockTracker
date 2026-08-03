"""
vector_store.py —— 向量数据库封装（ChromaDB + 智谱 Embedding）

使用方式：
    from vector_store import VectorStore

    store = VectorStore("chat_history")  # 集合名
    store.add("user_001", "茅台是白酒龙头", {"symbol": "600519"})
    results = store.search("user_001", "白酒股", top_k=3)

数据存储位置：
    ./chroma_data/  (项目目录下，自动创建)
"""
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

import chromadb
from chromadb.config import Settings as ChromaSettings

import embedding_helper

logger = logging.getLogger(__name__)

# 数据存储目录
DEFAULT_DATA_DIR = str(Path(__file__).parent / "chroma_data")


class ZhipuEmbeddingFunction(chromadb.EmbeddingFunction):
    """
    智谱 Embedding 的 ChromaDB 适配器
    ChromaDB 在 add/query 时会自动调用
    """

    def __init__(self, model: str = None):
        self.model = model or embedding_helper.DEFAULT_MODEL

    def __call__(self, input: List[str]) -> List[List[float]]:
        """ChromaDB 调这个方法，把文本转成向量"""
        return embedding_helper.embed_texts(input, model=self.model)


class VectorStore:
    """向量存储封装"""

    def __init__(self, collection_name: str, data_dir: str = None):
        """
        Args:
            collection_name: 集合名（类似 MySQL 的表名）
            data_dir: 数据存储目录
        """
        self.collection_name = collection_name
        self.data_dir = data_dir or os.getenv("CHROMA_DATA_DIR", DEFAULT_DATA_DIR)

        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

        self.client = chromadb.PersistentClient(
            path=self.data_dir,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        # 用智谱 Embedding（如果 key 没配，会在这里报错）
        self.embedding_fn = ZhipuEmbeddingFunction()
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"},  # 余弦相似度
        )
        logger.info(f"VectorStore 初始化完成: collection={collection_name}, dir={self.data_dir}")

    def add(self, doc_id: str, text: str, metadata: dict = None):
        """
        添加一条数据

        Args:
            doc_id: 唯一 ID（用 qq_id + 时间戳就行）
            text: 要存的文本
            metadata: 元数据（用于过滤，如 {qq_id, role, ts, symbol}）
        """
        try:
            self.collection.add(
                ids=[doc_id],
                documents=[text],
                metadatas=[metadata or {}],
            )
            logger.debug(f"已存: {doc_id} -> {text[:50]}")
        except Exception as e:
            logger.error(f"VectorStore.add 失败: {e}")

    def add_batch(self, items: List[dict]):
        """
        批量添加
        items = [{"id": "...", "text": "...", "metadata": {...}}, ...]
        """
        if not items:
            return
        try:
            self.collection.add(
                ids=[item["id"] for item in items],
                documents=[item["text"] for item in items],
                metadatas=[item.get("metadata", {}) for item in items],
            )
            logger.info(f"批量存入 {len(items)} 条")
        except Exception as e:
            logger.error(f"VectorStore.add_batch 失败: {e}")

    def search(
        self,
        query: str,
        top_k: int = 5,
        where: dict = None,
        where_document: dict = None,
    ) -> List[dict]:
        """
        语义搜索

        Args:
            query: 查询文本
            top_k: 返回几条
            where: 元数据过滤（如 {"qq_id": "12345"} 只搜这个用户的）
            where_document: 文档内容过滤

        Returns:
            [{"id": ..., "text": ..., "metadata": {...}, "distance": ...}, ...]
        """
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where,
                where_document=where_document,
            )
            # 整理成 list of dict
            items = []
            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    items.append({
                        "id": doc_id,
                        "text": results["documents"][0][i] if results["documents"] else "",
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "distance": results["distances"][0][i] if results["distances"] else 0.0,
                    })
            return items
        except Exception as e:
            logger.error(f"VectorStore.search 失败: {e}")
            return []

    def count(self) -> int:
        """返回集合里的数据条数"""
        return self.collection.count()

    def delete_collection(self):
        """删整个集合（慎用）"""
        self.client.delete_collection(self.collection_name)
        logger.warning(f"已删除集合: {self.collection_name}")


# ==================== 单例缓存 ====================
_stores: dict = {}

def get_store(collection_name: str) -> VectorStore:
    """获取或创建 VectorStore 单例"""
    if collection_name not in _stores:
        _stores[collection_name] = VectorStore(collection_name)
    return _stores[collection_name]
