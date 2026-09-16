# 同花顺扫码登录 + 自选股同步 — 设计文档 (THS QR Login & Watchlist Sync Design)

> 状态：草案待评审 · 日期：2026-09-05 · 场景：**纯个人本地自用**
> 本文档覆盖 **登录页"同花顺扫码登录" + Phase 1（自选股自动同步）**；真实持仓（数量/成本）为 Phase 2 预留通道，见 §11。

## 1. 背景与目标

用户希望：**登录页直接显示同花顺二维码，用手机里已登录的同花顺 App 扫码即登录本项目，登录成功后自动把同花顺自选股同步进项目**（设备装有同花顺 → 扫码即达，无需再输项目账号或贴 Cookie）。

**可行性结论（调研核实）**
- ❌ **官方 OAuth/App 唤起授权不存在**：同花顺没有面向第三方网站的"用同花顺账号登录"开放授权（类似微信 OpenSDK/OAuth 那种），官方开放 API 面向机构/收费、以行情数据为主（iFinD 类），个人开发者拿不到账号授权通道。
- ✅ **扫码登录可行（非官方协议，社区实测）**：[djj45/thspypc](https://github.com/djj45/thspypc) 逆向并实测同花顺二维码登录协议：`upass.10jqka.com.cn/scan/creatCode` 生成二维码 → 手机同花顺 App 扫码 → `getInfoNew` 轮询（~4s）返回会话凭证（account+password）→ 支持 **30 天免登录缓存**（凭证有效期内无需再扫，过期自动回退扫码）。同一条代码路径还能管理自选股分组（`ugc.10jqka.com.cn`）。
- ✅ **自选股读取可行**：[sunnysab/ths-favorite](https://github.com/sunnysab/ths-favorite) 网页版接口，可拉取分组/"我的自选"/加入价格/加入时间（`selfstock_detail`）。
- ⚠️ 同花顺是平台而非券商：真实持仓/成本在**券商交易系统**，账号层没有"读真实持仓"开放接口。自选里的"加入价/时间"**不是**真实买入成本，只能当参考价。

**因此技术路线定为：扫码登录（主） + 贴 Cookie（备用降级） + 账号密码（不做，撞滑块/验证码）。**

## 2. 范围与非目标

**范围内（Phase 1）**
- 登录页新增"同花顺扫码登录"标签页：二维码渲染 → 手机同花顺 App 扫码 → 轮询确认 → 项目签发 JWT 并登录。
- 首次扫码自动**创建/绑定项目账号**（个人自用单账号模式，见 §6.2），后续扫码直接登录。
- 登录成功后**自动触发一次自选股同步**；会话凭证加密持久化，30 天内免重扫，可手动"重新同步/解绑"。
- 会话失效（凭证过期/同花顺接口变更）时的可读提示与重扫引导。

**非目标（本阶段不做）**
- 真实持仓数量/成本价的自动拉取（Phase 2，见 §11）。
- 写回同花顺（本项目→同花顺）的增删。
- 多租户/公开部署（非官方协议合规风险，明确不做）。
- 账号密码自动登录。

## 3. 总体架构与数据流

### 3.1 扫码登录流程

```
登录页（Vue）→ 点“同花顺扫码登录”
   │ ① POST /api/v1/ths/qr/create（无需 JWT，返回 qrSessionId + 二维码内容）
   ▼
Java ThsQrController → WebClient → Python /api/v1/ths/qr/create
   │ ② ths_client.create_qr() → 调 upass.10jqka.com.cn/scan/creatCode → qrid
   ▼
前端渲染二维码（内容 ≈ mobile.10jqka.com.cn/?source=PC&qrid=<qrid>）
   │ ③ 手机同花顺 App 扫码确认
   ▼
前端每 ~4s GET /api/v1/ths/qr/poll?qrSessionId=…
   │ ④ Python 轮询 getInfoNew → status=3 → 返回会话凭证(account/password/expireTime)
   ▼
Java：凭证加密入库(ThsBinding) → 找到/创建项目用户 → 签发 JWT
   │ ⑤ 返回 {token, user, firstLogin} 给前端 → 前端跳转首页
   ▼
⑥ Java 后台异步触发 syncFavorites() → 自选股入库（30 天内再次打开直接复用凭证自动同步，免重扫）
```

### 3.2 自选股同步数据流（与上一版设计一致）

```
POST /api/v1/ths/sync (JWT) ──► Java ThsSyncService
        │ WebClient + X-Internal-Token
        ▼
Python FastAPI /api/v1/ths/selfstocks
        │ ths_client.py 带会话调同花顺网页自选接口(ugc.10jqka.com.cn)
        ▼
返回 [{code:"688023", market:"SH", price, addedAt}]
        │ Java 映射为内部规范代码 + 等价去重 + upsert
        ▼
favorite_stocks 表（stock_symbol, user_id, buy_price, quantity, buy_date）
        ▼
Dashboard / 行情 / 预警 全复用现有链路
```

约束：
- 会话凭证只存在于 Python 侧内存转发 + Java 侧加密持久化，**不落日志、不随 DTO 出网**。
- 同步"只增不改"：**只新增缺失的股票，已存在的一律不动**。
- ⚠️ **同花顺的「加入价」绝不写入 `favorite_stocks.buy_price`**（2026-09-12 修正）。

  > 原设计写的是"同花顺加入价仅在**新建行**写入 `buy_price` 并前端标注来源"，
  > 这条**已废弃**。原因：`buy_price` 的语义是"用户真实买入成本"，
  > 而 `PnlPercentEvaluator`（盈亏百分比预警）正是读它算盈亏的。
  > 一旦把"加入自选时的参考价"混进去，用户的盈亏就会基于错误成本触发。
  > 两个含义不同的值共用一个字段，后端分不清哪个是哪个。
  >
  > **正确做法**：同步时只写 `stock_symbol`（+ 名称），
  > `buy_price` / `quantity` / `buy_date` 全部留空，由用户在 Dashboard 自己填。
  > `price`/`addedAt` 这两个字段仍可由 Python 接口返回给前端**仅作展示**，
  > 但不落库为成本。

## 4. 股票代码映射规则（Phase 1 核心）

> **实现变更（2026-09-12）**：下面的映射表已按**实测返回格式**重写。
> 原设计假设同花顺返回 `code.market`（如 `600519.SH`），实际**不是**——
> 它返回的是「代码列表 + 数字市场码列表」，用第一个逗号分隔：
>
> ```json
> "selfstock": "688023|1A0001|399006|300033|,17|16|32|33|"
>               └─── 代码，竖线分隔 ───┘ └─ 数字市场码，同样竖线分隔 ─┘
> ```
>
> Python 侧 `parse_selfstock()` 已把它解析成 `{"code", "market", "market_type"}`，
> **Java 拿到的已经是带前缀的 `market`**，直接拼即可，不需要再做数字码映射。

数字市场码 → 市场前缀（Python 侧映射表，实测补全）：

| market_type | 含义 | 前缀 | 写库规范 |
|---|---|---|---|
| 17 | 沪市 A 股 | SH | `SH600519` |
| 33 | 深市 A 股 | SZ | `SZ000001` |
| 18 | 科创板 | SH | `SH688023` |
| 20 | 沪市 ETF | SH | `SH510300` |
| 36 | 深市 ETF | SZ | `SZ159915` |
| 16 | 沪市指数 | SH | `SH1A0001` |
| 32 | 深市指数 | SZ | `SZ399006` |
| 48 | 中证指数 | ZS | — |
| 71 / 151 | 北交所 | BJ | `BJ830799` |

- ⚠️ **16 / 32 这两个指数码是实测遇到的坑**：thspypc 的 `market_abbr()` 表里没有，
  直接用会显示成裸数字 `"16"` / `"32"`。Python 侧 `_MARKET_TYPE_ABBR` 已补全，
  并加了「按代码推市场」兜底（`688…`→SH、`00/30…`→SZ、`8…`→BJ）。
- 写库统一 `SH600519` 前缀大写规范（与 Python `normalize_symbol` 的腾讯格式 `sh600519` 语义一致）。
- **查重必须做"底层代码等价"判定**：同步前把该用户现有 symbol 归一化为去市场前缀的纯代码再比对，防止 `600519` 与 `SH600519` 双行。
- 指数（`1A0001` 上证指数、`399006` 创业板指）也会出现在自选列表里。
  若下游（行情/预警）不支持指数，需在同步时按 `market_type ∈ {16,32,48}` 过滤掉——
  这一点原设计没考虑到，Java 侧实现时需明确决定。

## 5. Python 数据服务设计

### 5.1 新增 `python-data-service/ths_client.py`

职责：封装同花顺扫码登录 + 网页版自选接口（非官方逆向协议，参考 thspypc 扫码流程与 ths-favorite 自选接口常量）。

> **实现变更（2026-09-12）**：下方法清单已按**实际落地代码**更新。
> 原设计里的 `list_groups` / `detail()` / `is_valid()` / `login_with_session()` /
> `save_credentials()` 均**未实现**（前三个是范围裁剪，后两个由 Java 侧接管凭证管理）。

```python
# ---- 实际实现（python-data-service/ths_client.py）----

class ThsApiError(Exception):
    """同花顺接口出错，message 是可直接展示给用户的中文"""

class ThsClient:
    # ---- 扫码登录（底层用 thspypc.qr_login）----
    def create_qr(self) -> dict          # creatCode → {"qrid", "qr_url"}
    def poll_qr(self, qrid, wait_seconds=120, on_status=None) -> dict
                                         # getInfoNew 轮询 → status 1=未扫/2=待确认/3=已确认
                                         # ok 时返回 {"account", "password", "expire_time"}

    # ---- 自选股（唯一取数入口）----
    def get_self_stocks(self, account, password) -> list
                                         # 内部串起：三步鉴权 → docookie2 换 cookie → 读列表
                                         # 返回 [{"code","market","price","addedAt"}, ...]

# ---- 模块级工具函数（供验证脚本复用，避免出现第二份实现）----
def http_auth(account, password) -> dict      # RSA公钥 → unified_login → mainverify
def get_http_cookies(auth) -> dict            # passport 三字段 → docookie2.php → cookies
def parse_selfstock(raw) -> list              # 解析 "代码|代码|,市场码|市场码|" 格式
```

**关于凭证缓存**：Python 侧**不做** 30 天免登录缓存。扫码拿到的 `account`/`password`
交给 Java 侧加密存进 `ths_bindings` 表，由 Java 决定何时复用、何时要求重扫——
这样只有一处凭证管理逻辑（对应 §6.1）。

- 异常统一抛 `ThsApiError`（携带可读中文消息）→ FastAPI 层转 502 → Java 捕获给用户明确提示。
- 常量（UA、`upass.10jqka.com.cn` / `ugc.10jqka.com.cn` URL 等）集中在文件头部，接口变更一处修改。
- 不实现 TCP 长连接推送（v4 协议复杂），用"登录后自动同步 + 手动刷新"。

### 5.2 `app.py` 新增路由（除扫码两端点外，均受 `x-internal-token` 中间件保护）

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/v1/ths/qr/create` | 内部 token | 生成二维码：返回 `{qrSessionId, qrUrl, pollIntervalMs, expiresInSec}` |
| GET | `/api/v1/ths/qr/poll?qrSessionId=...` | 内部 token | 轮询扫码状态；ok 时返回会话凭证 |
| POST | `/api/v1/ths/selfstocks` | 内部 token | **body** 传 `{account, password}`，返回「我的自选」平铺列表（Java 同步时唯一要调的取数接口） |

> ⚠️ **必须用 POST，不能用 GET query 参数**（2026-09-12 实测修正）。
> 原设计写的是 `GET /api/v1/ths/selfstocks?account=...&password=...`，
> 但 uvicorn 的访问日志会把整个 URL 打出来，凭证会明文落盘：
>
> ```
> INFO: 127.0.0.1 - "GET /api/v1/ths/selfstocks?account=mx_xxx&password=<32位hex> HTTP/1.1" 401
> ```
>
> 这违反 §10「凭证不出 DTO、不打日志」。改成 POST 后日志只剩
> `"POST /api/v1/ths/selfstocks HTTP/1.1" 200`，凭证不再出现。

> **实现变更（2026-09-12）**：原设计的 `/api/v1/ths/groups`（返回全部分组）
> 与 `/api/v1/ths/status`（凭证有效性探测）**均已移除**：
> - **分组**：项目只需要一份股票列表，自定义分组用不到；该接口也未经验证，
>   只会多一个失败点。将来真要多分组同步，按 thspypc `blocks.py` 的
>   `/optdata/selfgroup/open/api/group/v1/query` 加回即可。
> - **status（Python 侧）**：它做的事就是"试着读一次自选股"，同步失败时自然暴露，
>   不值得单开接口。**Java 侧的 `/api/v1/ths/status`（第 6.4 节）保留**——
>   它返回的"绑定状态 + 最后同步摘要"来自 Java 自己的 `ths_bindings` 表，
>   不经过 Python。

- 扫码会话有短 TTL（如 120s，超时前端提示重开），由 Python 内存/本地缓存维护 qrSessionId ↔ qrid 映射。
- 响应统一 `{"ok": true, "data": ...}` / `{"ok": false, "error": "..."}`。

## 6. Java 后端设计

### 6.1 新增实体与仓库

`entity/ThsBinding.java`（新表 `ths_bindings`，一用户一行，唯一约束 user_id）

| 列 | 类型 | 说明 |
|---|---|---|
| id | Long PK | |
| user | ManyToOne → users | |
| sessionEncrypted | TEXT | AES/GCM 加密的同花顺会话凭证（account/password） |
| expireTime | datetime | 同花顺侧凭证过期时间（约 30 天） |
| lastSyncAt | datetime | 上次成功同步时间 |
| lastSyncCount | int | 上次同步条目数 |
| lastError | varchar | 最近一次失败原因（用户可读） |

新增 `repository/ThsBindingRepository.java`（`findByUserId`）。

### 6.2 账号供给策略（个人自用单账号）

- 项目保留现有 `users` + JWT 体系不动。
- 首次扫码成功：若库里没有任何用户 → 自动创建默认用户（username 可取自同花顺账号脱敏，如 `ths_<uid尾4位>`，随机密码仅存哈希、不可登录密码入口——密码登录入口在个人模式下可保留原账号）；若已存在用户 → 直接绑定到该用户。
- 后续扫码：按 ThsBinding 归属用户直接登录。
- 该策略在 `ThsSyncService`/`AuthService` 扩展处集中实现，避免散落各处。

### 6.3 新增服务

`service/ThsQrService.java`
- `createQr()` → 调 Python 建 qr，本地 Redis 存 qrSessionId ↔ 过期；
- `pollQr(qrSessionId)` → 调 Python 轮询；status=ok 时拿凭证 → 加密存 ThsBinding → 定位/创建用户 → 返回 `(user, firstLogin)`。

`service/ThsSyncService.java`
- `syncFavorites(userId)`：解密凭证 → **POST** Python `/api/v1/ths/selfstocks`（body 传 account/password，WebClient 15s 超时）→ §4 映射与去重 → upsert → 更新 lastSync*；异常记 `lastError`。
- `status(userId)` / `unbind(userId)`。**注意**：`status()` 只读本地 `ths_bindings` 表，不调 Python。

### 6.4 新增控制器

| 方法 | 路径 | JWT | 说明 |
|---|---|---|---|
| POST | `/api/v1/ths/qr/create` | ❌ | 登录页用；返回 qrSessionId + qr_url |
| GET | `/api/v1/ths/qr/poll` | ❌ | 轮询；ok 后返回 `{token, user}`（签发 JWT） |
| POST | `/api/v1/ths/sync` | ✅ | 手动触发同步 |
| GET | `/api/v1/ths/status` | ✅ | 绑定状态 + 最后同步摘要 |
| DELETE | `/api/v1/ths/bind` | ✅ | 解绑（清凭证） |

- 扫码两端点进入 `SecurityConfig` 白名单；DTO 新增 `ThsQrCreateResponse`、`ThsQrPollResponse`、`ThsSyncResult`、`ThsStatusResponse`，均不含凭证明文。

## 7. 前端设计

- `frontend/src/api/ths.js`：`createQr()` / `pollQr(qrSessionId)` / `sync()` / `status()` / `unbind()`。
- `Login.vue`：改造为两个标签页——「账号密码」（现有）与「同花顺扫码」：
  - 扫码页：请求二维码 → 前端渲染（qrcode 组件，内容 = 后端返回的 qr_url）→ 轮询 `pollQr` → 成功拿 token 跳首页；120s 超时给"二维码已过期，点击刷新"。
  - 失败态（凭证无效/接口异常）：显示原因 + 提供"改用贴 Cookie"折叠入口（备用降级，cookie 注入后端仍保留）。
- 登录成功后由后端自动同步（前端无需额外动作）；Dashboard 顶部放轻量"重新同步"入口。
- 说明文案：仅个人自用；"加入价 ≠ 成本价"。

## 8. 接口契约示例

`POST /api/v1/ths/qr/create` → 200
```json
{ "code": 0, "data": { "qrSessionId": "s_abc123", "qrUrl": "http://mobile.10jqka.com.cn/?source=PC&qrid=xxx", "pollIntervalMs": 4000, "expiresInSec": 120 } }
```

`GET /api/v1/ths/qr/poll?qrSessionId=s_abc123`（未扫/已扫）
```json
{ "code": 0, "data": { "status": "pending" } }        // pending / scanned
```
（扫码确认后）
```json
{ "code": 0, "data": { "status": "ok", "token": "<JWT>", "user": { "id": 1, "username": "ths_1234" }, "firstLogin": true, "syncStarted": true } }
```

`POST /api/v1/ths/sync` → 200
```json
{ "code": 0, "message": "同步完成", "data": { "added": 12, "unchanged": 3, "lastSyncAt": "2026-09-05T10:00:00" } }
```

Python `POST /api/v1/ths/selfstocks`（body: `{"account":"mx_xxx","password":"<32位hex>"}`）→ 200
```json
{ "ok": true, "data": [ { "code": "688023", "market": "SH", "price": 123.45, "addedAt": "20240101" } ] }
```

## 9. 数据模型变更汇总

- 新增表：`ths_bindings`（§6.1）；扫码 qrSessionId 走 Redis 短 TTL，不入库。
- `favorite_stocks` 结构不变（`buy_price/quantity/buy_date` 已存在），只新增写入语义。
- Phase 2 真实持仓：新增独立表 `positions`（§11），与自选解耦。

## 10. 安全与凭据处理（个人自用基准）

- 同花顺会话凭证 = 敏感凭据：Java 侧 AES/GCM 加密入库；仅在 `ThsSyncService`/`ThsQrService` 内部解密使用，不出 DTO、不打日志。
- 密钥走环境变量/`secrets/`（沿用仓库惯例），不入库不入 git。
- 扫码接口走 HTTPS；JWT 沿用现有 RSA 机制。
- **Python 侧不落任何凭证**（2026-09-12 修正）：不做 `~/.ths_qr_credentials.json` 缓存，
  `account`/`password` 只作为 HTTP 请求参数在内存中穿过，请求结束即消失。
  凭证管理唯一发生在 Java 侧（加密存 `ths_bindings`），避免两处状态不一致。
- 复用现有：实体 `@JsonIgnore`、服务间 `X-Internal-Token`、DTO 裁剪。

## 11. Phase 2 预留：真实持仓（数量 + 成本）通道

- 新表 `positions(user_id, stock_symbol, quantity, cost_price, source, updated_at)`，与 `favorite_stocks` 解耦（自选=关注，持仓=真实持有）。
- 通道候选（确认券商后选型）：① **miniQMT / QMT**（合规首选，Python 加 `qmt_client.py` 定时拉）；② **CSV 导入**（零门槛兜底）；③ 手动维护。
- 扫码获得的同花顺账号与券商持仓无直接关系——若同花顺 App 内已绑定券商并展示持仓，那仍是券商通道数据，需按上表另接，扫码只解决"身份 + 自选"。

## 12. 测试计划

> 实现变更（2026-09-12）：Python 侧最终没写单元测试，改用**可执行的验证脚本**
> （每个脚本自己跑一遍真实链路，比 mock 更能发现协议问题）。
> 三个脚本都 `import ths_client`，测的就是线上那份实现。

- Python（脚本验证，替代原计划的 `tests/test_ths_client.py`）：
  - `test_auth_only.py` —— 只验三步鉴权（RSA 公钥 → unified_login → mainverify → docookie2）
  - `test_selfstock_only.py` —— 只验自选股取数，并排对比几种参数写法
  - `test_ths_endpoints.py` —— 走 HTTP 接口验证（和 Java 调用路径一致）
- Java：`ThsQrServiceTest`（poll ok → 建用户/绑定/签发 JWT；超时/失败分支）；`ThsSyncServiceTest`（等价去重 `600519` vs `SH600519` 不重复；**新建行不写 buy_price**、已存在不改数量成本；Python 异常 → lastError）。
- 手动冒烟：真实同花顺 App 扫码 → 自动登录 → Dashboard 出现自选并带实时价；凭证失效后自动引导重扫。

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 扫码协议为逆向、可能改版/风控 | 登录/同步失败 | 常量集中在 `ths_client.py` 头部、错误可读、凭证失效引导重扫；个人自用低频率 |
| 凭证过期 | 需重扫一次 | `ths_bindings.expireTime` 前端预告 + 同步失败时引导重扫 |
| 重复股票（前缀 vs 裸码） | 双行 | §4 等价去重 + `ThsSyncServiceTest` |
| 自选「加入价」误当成本 | 盈亏算错 | **同步时不写入 `buy_price`**（§3.2），用户自己填；Phase 2 独立 positions |
| 逆向项目自身有 bug | 扫码等路径直接崩 | 已在 `vendor/thspypc_src/.../client.py` 就地修复一处（见 `vendor/README.md`），重下载后需重打补丁 |
| 非官方协议合规 | 仅限个人自用 | 不做多租户/公开注册；README 声明 |

## 14. 实施任务清单（Phase 1）

> 进度（2026-09-14）：**Python + Java 两侧全部完成并端到端实测通过**。
> 实测链路：手机扫码 → Java 建号(ths_cx00) → 加密存凭证 → 签发 JWT
> → 同步 4 只自选股入库（buy_price 全为 NULL）→ 二次扫码登进同一账号、去重生效。

- [x] Python：新增 `ths_client.py`（扫码 create_qr/poll_qr、三步鉴权、device 指纹、get_self_stocks）
- [x] Python：`app.py` 加 `/api/v1/ths/qr/create`、`/api/v1/ths/qr/poll`、`/api/v1/ths/selfstocks`
- [x] Python：接口层端到端验证（`test_ths_endpoints.py`）
- [x] Java：新增 `ThsBinding` + `ThsBindingRepository` + 建表；配置项 `ths.aes.key`
- [x] Java：`ThsQrService`（poll→绑定/建用户/签发 JWT。**未用 Redis**，见下方说明）
- [x] Java：`ThsSyncService`（sync/status/unbind + 代码映射与等价去重）
- [x] Java：`ThsController` + DTO + Security 白名单
- [ ] 前端：`api/ths.js` + `Login.vue` 扫码标签页（qrcode 渲染 + 轮询 + 过期刷新）
- [ ] 前端：`Profile.vue` 绑定入口（已登录时扫码 = 绑定到当前账号）
- [ ] 端到端冒烟：前端页面扫码 → 自动登录 → 自动同步 → Dashboard 展示

### 实现变更说明（2026-09-14）

**① 未使用 Redis 存 qrSessionId。** 原设计 §5.2 说"qrSessionId 走 Redis 短 TTL"，
但实际不需要：扫码会话状态由 **Python 侧内存**维护（`app.py` 的 `QR_STORE`），
Java 只是原样透传 `qrSessionId`，自身无状态。少一个依赖、少一处状态不一致。

**② 扫码端点按「路径白名单」容忍无效 token。** 前端 `request.js` 会无条件带上
localStorage 里的 token（含过期的），而 Spring OAuth2 资源服务器一见
`Authorization` 头就验证、失败直接 401 —— 会导致登录页扫码失效。
解法：自定义 `authenticationEntryPoint`，**仅对公开路径**塞匿名身份放行，其余路径照常 401。
⚠️ 注意 `AnonymousAuthenticationToken.isAuthenticated()` 返回 true，
所以**绝不能全局容忍**，否则 `authenticated()` 会被绕过（开发期实测到过该漏洞）。
白名单常量 `PUBLIC_PATHS` 与 `authorizeHttpRequests` 的 permitAll 需同步维护。

**③ 用户绑定规则（重要变更）。** 原 §6.2 是"库里没用户就建、有就复用第一个"，
现改为按同花顺账号识别，支持"扫码既能登录、也能绑定"：

| 场景 | 行为 |
|---|---|
| 已登录时扫码 | 绑定到**当前登录用户**（"先登录再扫码"） |
| 未登录，且该同花顺账号**已绑过** | 登进**原账号**（不新建）★ 保证二次扫码进同一账号 |
| 未登录，且从未绑过 | 自动建号（`ths_<账号尾4位>`），扫完直接进 |

- 识别方式：遍历 `ths_bindings` 逐条解密比对 account（密文无法用 SQL 查）。
  个人自用绑定记录极少（1~2 条），开销可忽略；**多租户场景需额外加账号哈希列建索引**。
- 建号时密码为随机 UUID 的 BCrypt 哈希 —— 该账号**只能扫码登录**。
- [ ] 文档：README 增补"同花顺扫码登录（个人自用）"与风险声明
