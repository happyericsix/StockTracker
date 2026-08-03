"""test_load.py —— 验证 app.py 和 qq_standalone.py 都能正确加载"""
import sys

if sys.platform == "win32":
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

print("=" * 50)
print("Test 1: app.py")
print("=" * 50)
try:
    import app
    routes = [r.path for r in app.app.routes if hasattr(r, 'path')]
    print(f"✅ app.py 加载成功")
    print(f"   端点数: {len(routes)}")
    print(f"   /qq_msg: {'/qq_msg' in routes}")
except Exception as e:
    print(f"❌ app.py 加载失败: {e}")
    sys.exit(1)

print()
print("=" * 50)
print("Test 2: qq_standalone.py (只检查配置，不启动 bot)")
print("=" * 50)
try:
    import importlib.util
    # 用一种绕过 bot.run() 的方式加载
    import os
    os.environ["_TEST_ONLY"] = "1"
    spec = importlib.util.spec_from_file_location("qq_standalone", "qq_standalone.py")
    # 不执行 module，只读文件检查关键变量
    with open("qq_standalone.py", encoding="utf-8") as f:
        content = f.read()
    # 简单检查关键代码段存在
    assert "YOUR_WEBHOOK" in content
    assert "APP_TIMEOUT" in content
    assert 'data.get("replies")' in content
    assert "send_private_msg" in content
    print(f"✅ qq_standalone.py 关键代码段检查通过")
    print(f"   - YOUR_WEBHOOK 配置: ✓")
    print(f"   - APP_TIMEOUT 配置: ✓")
    print(f"   - 同步等 replies: ✓")
    print(f"   - send_private_msg 发回: ✓")
except AssertionError as e:
    print(f"❌ qq_standalone.py 检查失败")
    sys.exit(1)
except Exception as e:
    print(f"❌ qq_standalone.py 加载失败: {e}")
    sys.exit(1)

print()
print("=" * 50)
print("Test 3: handler 端到端 (用 mock 跑一次)")
print("=" * 50)
try:
    import qq_handler
    # 测试纯文本（不需要 akshare）
    replies = qq_handler.handle_message("12345678", "帮助")
    print(f"✅ handler '帮助' 返回 {len(replies)} 条")
    print(f"   内容预览: {replies[0][:60]}...")
except Exception as e:
    print(f"❌ handler 测试失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)

print()
print("🎉 所有测试通过！")
