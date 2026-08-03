"""test_handler.py —— 端到端测试 handler"""
import sys
if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

import qq_handler

cases = [
    ("99999999", "茅台"),
    ("99999999", "宁德时代"),
    ("99999999", "600519"),
    ("99999999", "帮我分析下茅台能买吗"),
    ("99999999", "002594 k线"),
    ("99999999", "你好"),
    ("99999999", "帮助"),
]

for user_id, msg in cases:
    print(f"\n>>> 用户 [{user_id}]: {msg}")
    replies = qq_handler.handle_message(user_id, msg)
    for i, r in enumerate(replies, 1):
        preview = r.replace("\n", " | ")[:120]
        print(f"    [{i}] {preview}{'...' if len(r) > 120 else ''}")
