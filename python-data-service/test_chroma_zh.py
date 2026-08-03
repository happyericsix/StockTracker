"""test_chroma_zh.py —— 智谱 Embedding + ChromaDB 中文测试

用法：
1. 在 .env 里加 ZHIPU_API_KEY=你的key
2. python test_chroma_zh.py
"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

import time
from vector_store import VectorStore


def test():
    print("=== 1. 检查智谱 key ===")
    import embedding_helper
    if not embedding_helper._is_available():
        print("❌ ZHIPU_API_KEY 未配置！")
        print("   在 .env 里加: ZHIPU_API_KEY=你的key")
        print("   去 https://bigmodel.cn/ 申请（送 2000 万 tokens）")
        return
    print("✅ key 已配置")

    print()
    print("=== 2. 创建 VectorStore ===")
    # 测试前先清空（防止上次残留数据影响）
    try:
        import chromadb
        c = chromadb.PersistentClient(path="./chroma_test_zh")
        try:
            c.delete_collection("stock_kb")
        except:
            pass
    except:
        pass

    store = VectorStore("stock_kb", data_dir="./chroma_test_zh")
    print(f"✅ collection 创建好，当前条数: {store.count()}")

    print()
    print("=== 3. 存 5 条股票信息 ===")
    docs = [
        {"id": "1", "text": "贵州茅台是中国最知名的白酒品牌，A股股王", "metadata": {"symbol": "600519"}},
        {"id": "2", "text": "五粮液是中国第二大白酒企业，浓香型代表", "metadata": {"symbol": "000858"}},
        {"id": "3", "text": "宁德时代是全球最大的动力电池制造商，新能源龙头", "metadata": {"symbol": "300750"}},
        {"id": "4", "text": "海康威视是全球领先的安防产品供应商", "metadata": {"symbol": "002415"}},
        {"id": "5", "text": "科大讯飞是国内语音AI龙头，主攻人工智能", "metadata": {"symbol": "002230"}},
    ]
    store.add_batch(docs)
    print(f"✅ 存了 {len(docs)} 条，当前总数: {store.count()}")

    print()
    print("=== 4. 语义搜索测试 ===")

    queries = [
        "白酒股",           # 期望找到茅台、五粮液
        "新能源车",         # 期望找到宁德
        "AI公司",          # 期望找到科大讯飞
        "摄像头监控",      # 期望找到海康威视
        "消费类股票",      # 期望找到茅台、五粮液
    ]

    for q in queries:
        print(f'\n>>> 问: "{q}"')
        results = store.search(q, top_k=2)
        for i, r in enumerate(results, 1):
            print(f"  [{i}] (距离={r['distance']:.3f}) {r['metadata'].get('symbol', '?')}: {r['text'][:60]}")

    print()
    print("=== 5. 元数据过滤 ===")
    print('\n>>> 问: "公司" + 过滤只找 symbol=300750')
    results = store.search("公司", top_k=3, where={"symbol": "300750"})
    for r in results:
        print(f"  ({r['distance']:.3f}) {r['metadata'].get('symbol')}: {r['text'][:60]}")

    print()
    print("🎉 全部通过！")
    print()
    print("下一步：把这个集成到 QQ 机器人，让所有聊天自动存到向量库")


if __name__ == "__main__":
    test()
