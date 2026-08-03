"""test_integration.py —— 端到端测试：handler + 向量数据库"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

import time
import qq_handler
import vector_store

# 用一个测试 QQ 号
TEST_QQ = "test_user_9999"

# 清空之前测试残留
try:
    store = vector_store.get_store("chat_history")
    print(f"[init] 向量库初始条数: {store.count()}")
except Exception as e:
    print(f"[init] 向量库初始化: {e}")
    sys.exit(1)

# 模拟几轮对话
conversations = [
    ("茅台", "查茅台行情"),
    ("宁德时代", "查宁德行情"),
    ("帮我分析下海康威视", "分析海康"),
    ("002594 k线", "K线"),
    ("我的自选", "自选股（未绑定会失败）"),
    ("你好", "闲聊"),
]

print()
print("=== 模拟 6 轮对话 ===")
for msg, label in conversations:
    print(f"\n>>> [{label}] 用户说: {msg}")
    replies = qq_handler.handle_message(TEST_QQ, msg)
    if replies:
        preview = replies[0].replace("\n", " | ")[:100]
        suffix = "..." if len(replies[0]) > 100 else ""
        print(f"    回复: {preview}{suffix}")
    else:
        print(f"    回复: (无)")
    time.sleep(0.5)  # 等异步存储

# 等待异步存储完成
print()
print("=== 等待异步存储完成 (3秒) ===")
time.sleep(3)

# 验证
store = vector_store.get_store("chat_history")
print(f"\n=== 验证: 向量库共 {store.count()} 条 ===")

# 搜索测试
print()
print("=== 搜索测试 ===")

tests = [
    ("白酒股", "应该找到茅台对话"),
    ("新能源电池", "应该找到宁德对话"),
    ("海康", "应该找到海康分析"),
    ("K线", "应该找到 002594 K线"),
]

for query, expected in tests:
    print(f'\n>>> 搜: "{query}" ({expected})')
    results = store.search(query, top_k=2, where={"qq_id": TEST_QQ})
    for r in results:
        meta = r.get("metadata", {})
        role = meta.get("role", "?")
        icon = "👤" if role == "user" else "🤖"
        text = r.get("text", "")[:60]
        dist = r.get("distance", 0)
        print(f"  {icon} (距离={dist:.3f}) {text}")

# 通过 handler 搜索
print()
print("=== 通过 QQ handler 搜索 ===")
for query in ["我之前问过什么白酒股", "我之前问过什么新能源", "我之前问过什么海康"]:
    print(f'\n>>> {query}')
    replies = qq_handler.handle_message(TEST_QQ, query)
    for r in replies:
        print(f"  {r[:120]}")
    print()

print("🎉 集成测试完成")
