"""test_e2e.py —— 模拟 NapCat 测端到端"""
import sys
import json
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

import urllib.request

# 模拟 NapCat 推 webhook
def send_qq_msg(user_id, message):
    req = urllib.request.Request(
        "http://localhost:8000/qq_msg",
        method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"user_id": user_id, "message": message}).encode("utf-8"),
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read())
            return data.get("replies", [])
    except Exception as e:
        return [f"ERROR: {e}"]


print("=== 测试端到端 ===\n")
for msg in ["茅台", "宁德时代", "你好", "我之前问过什么白酒股"]:
    print(f">>> {msg}")
    replies = send_qq_msg("test_12345", msg)
    for r in replies:
        print(f"  << {r[:200]}")
    print()
