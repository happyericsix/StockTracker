# vendor/ — 第三方源码副本（含本地补丁）

这些是整个目录从上游项目下载的源码副本，不是本项目自己写的代码。
放在这里（而不是 pip 安装）的原因见下。

---

## 1. `thspypc_src/` — djj45/thspypc

同花顺协议实现（扫码登录 + HTTP 三步鉴权 + 设备指纹 + 自选股）。

- 上游：https://github.com/djj45/thspypc
- 来源：`main` 分支的 tar.gz
- 拉取脚本：`python-data-service/_fetch_ths.py`（可重复运行，会覆盖本目录）
- 本项目只用到它的 4 个能力：

  | 能力 | 位置 |
  |---|---|
  | 扫码登录 | `thspypc/qr_login.py` |
  | HTTP 三步鉴权（RSA → unified_login → mainverify） | `thspypc/protocol.py` |
  | 设备指纹 imei / mac64 | `thspypc/protocol.py` |
  | 自选股读取（docookie2 换 cookie + BlockManager） | `thspypc/blocks.py` |

### ⚠️ 本地补丁（重要）

`src/thspypc/client.py` 的 `_http_auth_and_tcp_login()` 里有一处**上游 bug**，本项目已就地修复：

```python
# 上游原样（会崩）：
return self._do_tcp_login(passport_fields, max_retries=1)

# 本项目修复后：
return self._do_tcp_login(passport_fields)
```

原因：`_do_tcp_login()` 的定义并不接受 `max_retries` 参数，所以走扫码路径会直接抛
`TypeError: got an unexpected keyword argument 'max_retries'`。
真正的重试逻辑在 `_do_tcp_login_raw()` 里（遍历 `MARKET_HOSTS`，遇 `VerifyCode=-1` 换 host），
该参数是上游重构时漏删的，去掉后行为不变。

**注意**：重新运行 `_fetch_ths.py` 会覆盖这个补丁，需要重新打一次。
修复处已加 `⚠️ 本地修复` 中文注释，用 `Select-String -Path "vendor\thspypc_src\src\thspypc\*.py" -Pattern "本地修复"` 可以找到。

---

## 2. `qrcode_lib/` — qrcode 7.3.1

终端渲染二维码用（thspypc 的 `render_qr_ascii()` 内部 `import qrcode`）。

- 上游：https://pypi.org/project/qrcode/7.3.1/
- 拉取脚本：`python-data-service/_fetch_qrcode.py`
- **为什么锁定 7.3.1**：7.4+ 保存 PNG 时会额外依赖 `pypng`，7.3.1 不需要。

---

## 为什么不做成 `pip install`

本机沙箱环境里 **PowerShell / curl 的 TLS 被拦截**，导致 `pip` 卡死装不上任何包
（但 Python 自己的 `urllib` / `requests` 完全正常，所以拉取脚本能用）。

另外 thspypc **没有发布到 PyPI**（`https://pypi.org/pypi/thspypc/json` 返回 404），
只能从 GitHub 取源码。

将来如果换到网络正常的环境，可以改成正式安装：

```powershell
pip install qrcode
# thspypc 未上 PyPI，仍需要 vendor 或 git clone
```

装好之后 `ths_client.py` 的 `_bootstrap()` 会自动优先用系统里装好的版本。
