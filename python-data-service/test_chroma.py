"""test_chroma.py —— ChromaDB hello world 验证"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

import chromadb
from chromadb.config import Settings

# 数据存到 ./chroma_test 文件夹
print("=== 1. 连接 ChromaDB ===")
client = chromadb.PersistentClient(path="./chroma_test")
collection = client.get_or_create_collection(name="test", metadata={"hnsw:space": "cosine"})
print(f"OK. collection: {collection.name}")

# 存 3 条数据
print()
print("=== 2. 存 3 条数据 ===")
collection.add(
    ids=["1", "2", "3"],
    documents=[
        "贵州茅台是中国最知名的白酒品牌",
        "宁德时代是全球最大的动力电池制造商",
        "海康威视是全球领先的安防产品供应商",
    ],
    metadatas=[
        {"stock": "600519", "type": "company"},
        {"stock": "300750", "type": "company"},
        {"stock": "002415", "type": "company"},
    ],
)
print("OK. 已存 3 条")

# 查
print()
print("=== 3. 语义搜索: '白酒龙头' ===")
results = collection.query(
    query_texts=["白酒龙头"],
    n_results=2,
)
print(f"找到 {len(results['documents'][0])} 条:")
for i, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"  [{i+1}] (距离={dist:.3f}) {meta['stock']}: {doc}")

print()
print("=== 4. 语义搜索: '电池股' ===")
results = collection.query(query_texts=["电池股"], n_results=2)
for i, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"  [{i+1}] (距离={dist:.3f}) {meta['stock']}: {doc}")

print()
print("=== 5. 语义搜索: '监控摄像头' ===")
results = collection.query(query_texts=["监控摄像头"], n_results=2)
for i, (doc, meta, dist) in enumerate(zip(results["documents"][0], results["metadatas"][0], results["distances"][0])):
    print(f"  [{i+1}] (距离={dist:.3f}) {meta['stock']}: {doc}")

print()
print("=== 6. 数据存储位置 ===")
import os
if os.path.exists("./chroma_test"):
    size = sum(os.path.getsize(os.path.join("./chroma_test", f))
               for f in os.listdir("./chroma_test") if os.path.isfile(os.path.join("./chroma_test", f)))
    print(f"  ./chroma_test/ 存在, 文件数: {len(os.listdir('./chroma_test'))}")
    print(f"  文件: {os.listdir('./chroma_test')}")
