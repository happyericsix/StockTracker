# Stock Data Service (akshare)

为 stock-tracker Java 后端提供数据服务的 Python 微服务，基于 [akshare](https://github.com/akfamily/akshare) 获取 A 股/港股/美股行情数据。

## 快速启动

`ash
# 1. 创建虚拟环境（推荐）
python -m venv venv
venv\Scripts\activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务（热重载）
uvicorn app:app --reload --host 0.0.0.0 --port 8000
`

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /health | 健康检查 |
| GET | /api/v1/quote/{symbol} | 实时行情 |
| GET | /api/v1/history/{symbol} | K 线历史 |
| GET | /api/v1/overview/{symbol} | 基本面概况 |

## 与 Java 后端集成

在 StockService.java 中新增 AkshareStockClient，通过 WebClient 调用 http://python-data-service:8000/api/v1/...，替换原有的 ChoiceStockClient。

### docker-compose 示例

`yaml
python-data:
  build: ./python-data-service
  container_name: stock-data
  ports:
    - "8000:8000"
`

## 数据说明

- A 股代码直接传数字即可，如 600519（无需 .SH 后缀）
- 历史 K 线默认前复权（djust="qfq"）
- akShare 数据来源于东方财富等公开财经网站，仅供学习参考

## 同花顺扫码登录（个人自用）

协议实现分两部分：

- **扫码登录 + 自选股解析** ← 社区开源项目 [djj45/thspypc](https://github.com/djj45/thspypc)，
  源码副本在 `stock-tracker/vendor/thspypc_src/`（含一处本地补丁，见 `vendor/README.md`）
- **三步鉴权 + 自选股取数** ← 本项目实测（2026-09-12 验证通过），代码在 `ths_client.py`

| 文件 | 作用 |
|---|---|
| **`ths_client.py`** | **正式模块**，唯一实现：扫码 + 鉴权 + 读自选股（对外 3 个方法） |
| `test_ths_endpoints.py` | 走 HTTP 接口验证（和 Java 后端调用路径一致） |
| `test_auth_only.py` | 只验三步鉴权（测的就是 `ths_client` 里的实现） |
| `test_selfstock_only.py` | 只验自选股取数（并排对比几种取数方式） |
| `ths_probe_v2.py` | 探针：扫码 → 鉴权 → 读自选，每步打印（初版验证用，保留作参考） |
| `ths_probe_simple.py` | 纯 `requests` 裸写的最短版本（教学用） |
| `_fetch_ths.py` / `_fetch_qrcode.py` | 重新拉取 vendor 依赖 |

> ⚠️ **不要出现第二份实现。** 三步鉴权曾经在 `ths_probe_v2.py` 和 `ths_client.py`
> 里各写了一份，这会导致"探针能用、接口不能用"这种最难查的 bug。
> 现在验证脚本一律 `import ths_client`，只测那一份。

**范围说明**：只同步「我的自选」这一个列表。同花顺的**自定义分组已按需求移除**
（项目只需要一份股票列表，分组接口也没验证过，白多一个失败点）。
Python 侧的凭证有效性探测接口 `/ths/status` 同样已移除——它做的事就是"试着读一次
自选股"，同步失败时自然会暴露；Java 侧若要「绑定状态 + 同步摘要」，读自己的
`ths_bindings` 表即可。

`app.py` 新增的接口（均需 `x-internal-token` 头）：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/v1/ths/qr/create | 生成登录二维码 |
| GET | /api/v1/ths/qr/poll | 轮询扫码状态 |
| POST | /api/v1/ths/selfstocks | 读取「我的自选」列表（Java 同步时调这个） |

> ⚠️ `/selfstocks` 必须用 **POST**：凭证走 body 而不是 URL query。
> 用 GET 的话账号密码会明文进 uvicorn 访问日志（实测确认过）。

**免责**：非官方逆向协议，仅供个人本地自用，不做多租户/公开部署。
自选股里的「加入价」是同花顺记录的加入自选时价格，**不是真实买入成本**——
所以同步入库时不要写进 `buy_price`，否则会污染盈亏计算。

## 开发小工具

改完 Python 代码后跑一下，能在运行前发现「变量名写错」这类 NameError：

```powershell
.\.venv\Scripts\python.exe _check_undefined.py
```

（项目没装 pyflakes/ruff，所以自带一个轻量的；要检查更多文件就编辑脚本里的 `TARGETS`。）
