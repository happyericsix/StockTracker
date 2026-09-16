# 同花顺扫码登录 — 新手实操教程（照着敲就能跑）

> 写给**刚入门、想自己动手**的你。每一节都按这个格式：
> **目标 → 需要什么 → 具体操作（命令/位置/点哪里）→ 预期看到什么 → 卡住了怎么办**。
> 术语（如 JWT、FastAPI、JPA）不懂没关系，标了 🔎 的地方自己上网查即可，不影响照做。
>
> 设计背景见：`docs/superpowers/specs/2026-09-05-ths-watchlist-sync-design.md`

---

## 第 0 步：准备（先花 30 分钟把环境备齐）

### 0.1 你需要的东西

| 需要 | 你有没有（我已帮你查过） | 说明 |
|---|---|---|
| 手机上的**同花顺 App** + 能登录的账号 | ❓ 请自查 | 扫码要用它。没有就去应用商店装并登录 |
| 电脑文本编辑器 | ✅ 推荐 VS Code | 用来打开/编辑项目文件 |
| 一个**终端/命令行**窗口 | ✅ Windows 用 PowerShell | 就是黑色的命令行窗口 |
| Python 虚拟环境 | ✅ 已有 | 位置：`stock-tracker\python-data-service\.venv`（Python 3.10） |
| Java / Node / MySQL | ✅ 都可用 | Java 21、Node 24、MySQL 能连（见 0.3） |
| Redis | ❌ **没装** | 只有第 4 步以后（跑 Java 后端）才需要，到时再解决，见 0.4 |
| Docker 桌面 | ⚠️ 装了但没启动 | 可选；不强制，见 0.4 |

### 0.2 打开项目的正确姿势

1. 用 VS Code 打开项目根文件夹：`E:\GitHubjob\Stock Tracker\stock-tracker`
   （VS Code：文件 → 打开文件夹 → 选到这个 `stock-tracker` 文件夹）
2. 在 VS Code 里打开终端：菜单 **终端 → 新建终端**（快捷键 Ctrl+`）
3. 以后所有命令都在这个终端里敲。**先确认你在项目根目录**，敲下面命令应该打印出 `stock-tracker`：

```powershell
Get-Location
```

### 0.3 你的环境真实状态（我已测过，直接信我）

- ✅ Python 虚拟环境可用：`stock-tracker\python-data-service\.venv\Scripts\python.exe`
  （里面已装好 requests / fastapi / uvicorn / akshare）
- ✅ Java 21 可用
- ✅ MySQL 能连（`127.0.0.1:3306` 通，root 密码默认 `123456`）
- ✅ Node 24 + 前端依赖 `node_modules` 已装好
- ❌ Redis 没装、Docker Desktop 没启动 → 见 0.4

### 0.4 Redis 怎么补（第 4 步之前弄好即可，现在不用管）

跑 Java 后端（端口 8080）依赖 Redis（缓存）。两条路任选：
- **路线 A（推荐，省事）**：启动 Docker Desktop 应用（双击图标等它变绿）→ 在终端执行：
  ```powershell
  docker run -d --name stock-redis -p 6379:6379 redis:7-alpine
  ```
- **路线 B**：自己装一个 Windows 版 Redis（上网搜 "redis windows 安装"，装完默认就监听 6379）。
- 判断 Redis 是否就绪：终端执行 `Test-NetConnection 127.0.0.1 -Port 6379`，看到 `TcpTestSucceeded : True` 即可。
> 现在先跳过，第 1~3 步只需要 Python，不需要 Redis。

### 0.5 最常用的三条命令（背下来）

```powershell
# 1) 启动 Python 数据服务（后面会反复用）。注意：必须先 cd 进 python-data-service 目录
cd stock-tracker\python-data-service
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8000

# 2) 停止服务：在终端按 Ctrl + C
# 3) 另开一个终端窗口（用于敲 curl 测试；第 1 个窗口留着跑服务）
```
🔎 不懂就问搜索引擎的词：`uvicorn 是什么`、`curl 是什么`、`端口是什么`。

---

## 第 1 步：写一个"扫码探针"脚本（目标：手机扫码后打印出登录凭证）

> 时间：1~2 小时（第一次）。**这是全部工作里风险最高的一步**：同花顺接口是社区逆向的，
> 字段名可能和网上资料不一样。卡住是正常的，卡久了把输出贴出来我们一起对。
> 此步**完全不碰**你的项目现有代码，写一个独立的临时脚本，失败不影响任何东西。

### 1.1 建文件

在 VS Code 里，左侧文件树找到 `python-data-service` 文件夹 → 右键 → **新建文件** →
名字输入：`ths_probe_v2.py`（注意在 `python-data-service` 目录下，不是别处）。

> 📌 **命名说明（2026-09-12 更新）**：这一节原来叫 `ths_probe.py`。
> 后来照着你最初那份手写代码的风格重做了一版，命名为 **`ths_probe_v2.py`**，
> 原 `ths_probe.py` 已删除。下文出现的文件名统一按 `ths_probe_v2.py` 理解。

### 1.2 往文件里写（先抄下面的框架，`# TODO` 处是你自己补的）

```python
# ths_probe_v2.py — 同花顺扫码登录探针（临时脚本，验证用）
import requests
import time

# 模拟浏览器，别让服务器觉得你是机器人
HEADERS = {"User-Agent": "Mozilla/5.0"}

# TODO 1: 用 requests.Session() 建一个会话（保持 cookie）
#         提示：session = requests.Session()
#         然后 requests 换成 session 来发请求

# TODO 2: 调同花顺"取二维码"接口，拿到 qrid
#         参考地址（社区逆向，若 404 就试 POST）：
#         https://upass.10jqka.com.cn/scan/creatCode
#         把返回内容 print() 出来，找到 qrid 字段

# TODO 3: 拼二维码内容并打印，格式类似：
#         qr_url = "http://mobile.10jqka.com.cn/?source=PC&qrid=" + qrid
#         然后 print(qr_url)
#         把打印出的这一长串地址，复制到任意"在线二维码生成"网站
#         （搜索"二维码生成器"），生成二维码图片，用手机同花顺 App 扫它

# TODO 4: 轮询"确认状态"接口，每 4 秒一次，最多等 120 秒
#         参考地址：
#         https://upass.10jqka.com.cn/scan/getInfoNew   （带 qrid 参数）
#         循环里：
#           1) 发请求
#           2) print 原始返回内容（这一步最重要！先看清楚字段长什么样）
#           3) 如果返回里表示"已确认/成功"，就 break
#           4) 没成功就 time.sleep(4)
#         🔎 自己查：如何在 Python 里 while 循环 + 提前退出
```

> ⚠️ **最重要的原则：先 print 原始返回，不要猜字段名。** 接口返回的 JSON 里到底叫
> `qrid` 还是 `qrId`、成功状态是 `status=3` 还是别的，**以你打印出来的为准**。
> 这一步就是"逆向工程"：打印 → 看 → 适配。

### 1.3 运行它

```powershell
# 在终端先确保在 python-data-service 目录
cd stock-tracker\python-data-service
.\.venv\Scripts\python.exe ths_probe_v2.py
```

### 1.4 预期看到什么

- 先打印出一个二维码 URL；
- 你用手机同花顺 App 扫码并确认后，脚本继续打印出服务器的返回（里面有登录凭证/会话信息）。

### 1.5 卡住自查表

| 现象 | 可能原因与对策 |
|---|---|
| 网络报错/超时 | 检查能否访问外网；接口地址可能变了，上网搜最新资料 |
| 返回空或错误码 | 缺少 User-Agent 或 cookie；用 `session.get(...)` 而不是裸 `requests.get` |
| 手机扫了没反应 | 二维码内容必须能被同花顺识别；确认 qr_url 拼对了；换用图片二维码扫 |
| 完全没思路 | 卡 >1 小时就把**你的代码 + 打印输出**贴给我，一起对 |

**做完这一步 → 提交一次代码**（🔎 自己查：`git add`、`git commit` 怎么用），
提交说明写 `feat(ths): probe script proves qr login works`。

---

## 第 2 步：让探针能"读出我的自选股"

> 目标：用第 1 步拿到的登录状态，请求同花顺的自选股接口，把你自己的自选股列表打印出来。

### 2.1 做什么

在第 1 步同一个 `ths_probe_v2.py` 文件里**继续加代码**（先别删旧代码）：

```python
# TODO 5: 用第 1 步得到的登录状态（cookie 等），请求自选股接口
#         域名参考：ugc.10jqka.com.cn 下的自选相关接口
#         接口路径网上搜（社区开源项目 ths-favorite 有整理，搜 GitHub: sunnysab/ths-favorite）
#         🔎 上网搜：ths-favorite 看它请求了哪些地址、返回什么字段
# 提示：打印返回，找到类似 {"code": "600519", "market": "SH"} 这种结构，
#       它们就是你同花顺里的自选股（600519 是贵州茅台）
```

### 2.2 预期看到什么

终端打印出几行股票数据，且和你手机同花顺里的自选股对得上。

### 2.3 卡住自查

- 返回"未登录/无权限"→ 登录状态没带对（cookie 没传），回去检查第 1 步。
- 不知道接口地址 → 去 GitHub 搜 `ths-favorite` 或 `thspypc`，看别人的 README/代码里写的 URL。
- 记下一个样例：你自选里某只股票打印出来的原始 JSON，存到记事本，第 5 步写测试要用。

---

## 第 3 步：把探针整理成正式模块 + 网页接口

> 目标：把验证过能跑的代码，从"临时脚本"变成"项目正规军"：
> `ths_client.py`（可复用模块）+ `app.py` 里加两个接口，让 Java 后端能调用。
> 做完这步，Python 侧就完工了。

### 3.1 先看两个"范本文件"（学习它们的样子）

- `python-data-service\akshare_client.py`：注意它的风格——顶部说明、`HEADERS`、函数用中文注释、
  出错时返回 `None` 而不是让程序崩掉。
- `python-data-service\app.py`：看里面 `@app.get("/api/v1/...")` 的写法（怎么定义一个网页接口）。
  **重点**：`/api/v1/` 开头的接口已有"内部密钥"保护（app.py 第 75 行附近），你不用自己加鉴权。

### 3.2 新建 `python-data-service\ths_client.py`

右键 `python-data-service` → 新建文件 → 输入 `ths_client.py`。内容框架：

```python
"""
同花顺客户端：扫码登录 + 读取自选股
（非官方逆向接口，仅供个人自用）
"""
import time
import requests

HEADERS = {"User-Agent": "Mozilla/5.0"}


class ThsApiError(Exception):
    """同花顺接口出错（带给人看的中文提示）"""
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class ThsClient:
    def __init__(self, timeout: int = 10):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.timeout = timeout

    # ---- 扫码登录 ----
    def create_qr(self) -> dict:
        """第 1 步的 TODO 1-3 整理到这里：返回 {"qrid":..., "qr_url":...}
        失败时 raise ThsApiError("取二维码失败: <原因>")"""
        # TODO 你来实现

    def poll_qr(self, qrid: str, wait_seconds: int = 120) -> dict:
        """第 1 步的 TODO 4 整理到这里：轮询直到确认
        成功返回 {"account":..., "password":..., "expire_time":...}
        超时 raise ThsApiError("等待扫码超时，请重新生成")"""
        # TODO 你来实现

    # ---- 自选股 ----
    def get_self_stocks(self, account: str, password: str) -> list:
        """第 2 步整理到这里：返回自选列表，元素形如
        {"code":"688023","market":"SH","price":123.45,"addedAt":"20240101"}
        失败 raise ThsApiError("读取自选股失败: <原因>")"""
        # TODO 你来实现

    # 注意：这里传的是 account/password，不是 cookie。
    # 因为扫码拿到的就是「账号+密码」，要读自选股还得先做三步鉴权换 cookie
    # （这件事在 get_self_stocks 内部完成，见设计文档 §5.1）。
```

> 📌 **命名说明（2026-09-12）**：上面的方法名按**实际落地代码**更新。
> 教程原文写的是 `list_groups()`，实际实现叫 `get_self_stocks()` ——
> 因为同花顺的「自定义分组」功能最终没有采用（项目只需要一份股票列表），
> 只保留「我的自选」这一个来源。详见设计文档 §5.1 / §5.2 的实现变更说明。

**把第 1、2 步写通的代码搬进这三个方法**，`raise ThsApiError` 替换原来的直接打印。

### 3.3 在 `app.py` 末尾加两个接口

打开 `python-data-service\app.py`，滚到文件最底部，照着现有接口的格式加：

```python
# ==================== 同花顺扫码登录 ====================

@app.get("/api/v1/ths/qr/create")
def ths_qr_create():
    """生成同花顺登录二维码。返回 qrUrl 给前端渲染。"""
    # TODO: 创建 ThsClient，调 create_qr()
    # 成功返回形如：
    # {"ok": True, "data": {"qrSessionId": "本地自己编一个随机串", "qrUrl": "...", "pollIntervalMs": 4000}}
    # 失败捕获 ThsApiError，返回 {"ok": False, "error": "中文提示"}
    # 🔎 自己查：python 怎么生成随机字符串（random / uuid）


@app.get("/api/v1/ths/qr/poll")
def ths_qr_poll(qrSessionId: str = ""):
    """前端每 4 秒轮询一次：手机扫码确认了吗？
    确认了返回 {"ok": True, "data": {"status": "ok", "session": {...}}}
    没确认返回 {"ok": True, "data": {"status": "pending"}}"""
    # TODO 你来实现。注意 qrSessionId→qrid 的对应关系要先存起来
    # （最简单：模块级 dict，比如 QR_STORE = {}，key 是 qrSessionId）
```

> 提示：第 3.3 里"qrSessionId 对应关系存哪"是**故意留给你想的设计点**。
> 最简单做法是模块顶部放一个字典 `QR_STORE = {}`。想想：为什么不能只存一个全局变量？
> 🔎 自己查：`python dict`、`python 模块级变量`。

### 3.4 测试并跑起来

```powershell
cd stock-tracker\python-data-service

# 1) 先写/跑测试（可选但推荐，参照 tests 目录里其他测试文件的写法）
# 2) 启动服务（终端会一直挂着，别关）
.\.venv\Scripts\python.exe -m uvicorn app:app --port 8000
```

**另开一个新终端窗口**，敲下面命令验证（看到 JSON 输出就成功）：

```powershell
curl "http://localhost:8000/api/v1/ths/qr/create"
```

### 3.5 预期看到什么

- 服务启动时终端显示 `Uvicorn running on http://0.0.0.0:8000`；
- curl 返回一串 `{"ok": true, "data": {...}}`。

### 3.6 卡住自查

| 现象 | 对策 |
|---|---|
| `ModuleNotFoundError: ths_client` | app.py 和 ths_client.py 必须在**同一目录** `python-data-service` 下 |
| curl 报连接拒绝 | 服务没启动成功，看第 1 个终端窗口的报错 |
| 接口返回 401 | 你给 curl 加了 token 相关参数？本地开发没配 token 时不会拦，先去掉多余参数 |

**做完提交**：`git commit -m "feat(ths): python client + qr endpoints"`

---

## 第 4 步：Java 侧写"调用 Python 的客户端"

> 目标：Java 后端能调到你在第 3 步写的 Python 接口。
> 这一步是"抄作业"：你项目里 `AkshareStockClient.java` 已经示范了怎么调 Python，
> 你照着它的样子再写一个。

### 4.1 打开范本文件（必读）

`stock-tracker\src\main\java\com\happyericsix\stocktracker\client\AkshareStockClient.java`

重点看三处（我帮你标好了）：
- 第 34~47 行：构造函数怎么读配置、怎么给请求加 `X-Internal-Token` 头 → **照抄**；
- 第 92~107 行：`getStockQuote`——一个完整"调 Python 并解析返回"的例子 → **照抄结构**；
- 第 109~114 行：出错时返回一个"空对象"而不是抛异常 → **学习这种降级思路**。

### 4.2 新建两个文件

都在目录 `stock-tracker\src\main\java\com\happyericsix\stocktracker\` 下，右键新建。

**文件 1：`dto\ThsQrCreateResponse.java`**（数据类，对应 Python 返回的 JSON）
先打开 `dto\StockQuoteResponse.java` 学 record 写法，然后：

```java
package com.happyericsix.stocktracker.dto;

/**
 * Python /api/v1/ths/qr/create 的返回。
 * 字段名要和 Python 返回的 JSON 一一对应（Java record 自动映射）。
 */
public record ThsQrCreateResponse(
        boolean ok,
        ThsQrData data,
        String error
) {
    public record ThsQrData(
            String qrSessionId,
            String qrUrl,
            long pollIntervalMs
    ) {}
}
```

**文件 2：`client\ThsClient.java`**（调 Python 的客户端）

```java
package com.happyericsix.stocktracker.client;

import com.happyericsix.stocktracker.dto.ThsQrCreateResponse;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

import java.time.Duration;

/**
 * 调 Python 数据服务的同花顺接口。
 * 整体照抄 AkshareStockClient 的构造方式（baseUrl + X-Internal-Token + 15s 超时）。
 */
@Service
public class ThsClient {

    private final WebClient webClient;
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(15);

    public ThsClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                     @Value("${internal.api-token:}") String internalToken) {
        // TODO 照抄 AkshareStockClient 第 36~47 行的 WebClient 构造逻辑
    }

    public ThsQrCreateResponse createQr() {
        // TODO 照抄 AkshareStockClient.getStockQuote 的结构：
        //     webClient.get().uri("/api/v1/ths/qr/create").retrieve()
        //              .bodyToMono(ThsQrCreateResponse.class).timeout(...).block()
        //     出错时 catch 后返回一个 ok=false 的对象（看 emptyQuote 的降级写法）
        return null; // 先占位，编译能过再说
    }
}
```

### 4.3 编译验证（能编译过就算第一步成功）

```powershell
cd stock-tracker
.\mvnw.cmd compile
```
看到 `BUILD SUCCESS` 即通过。🔎 自己查：`mvnw` 是什么（Maven 包装器）。

### 4.4 完整验证（需要 Python 服务在跑 + MySQL + Redis）

> 先按 0.4 把 Redis 弄起来（MySQL 你已经能连）。
> 然后启动 Java 后端：VS Code 里直接跑 `StocktrackerApplication.java`，或在终端：
```powershell
cd stock-tracker
.\mvnw.cmd spring-boot:run
```
启动日志最后出现 `Started StocktrackerApplication` 即成功。之后你可以通过 Java 的接口间接
验证（等第 5 步有完整接口再验证也行，本步能 `compile` 通过就达标）。

**做完提交**：`feat(ths): java client for qr endpoints`

---

## 第 5 步：Java 业务核心（建表 + 服务 + 接口）

> 目标：完整闭环——手机扫码确认 → 系统自动创建/找到你的用户 → 发给你一个登录令牌(JWT)
> → 把你的同花顺自选股写进数据库。**这是最值得你认真写的一步。**

### 5.1 先加一个配置项

打开 `stock-tracker\src\main\resources\application.properties`，**在文件末尾加一行**：
```properties
# 同花顺会话加密密钥（本地随便填，别提交到 git；格式见 AesGcmCipher 里你的实现）
ths.aes.key=${THS_AES_KEY:dev-only-key-change-me-0000}
```

### 5.2 建实体（对应数据库表）

打开范本 `entity\FavoriteStock.java`（看 JPA 注解怎么写），然后新建
`entity\ThsBinding.java`：

```java
package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.*;

@Entity
@Table(name = "ths_bindings")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ThsBinding {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User user;

    @Column(nullable = false, length = 4096)
    private String sessionEncrypted;   // 加密后的同花顺登录凭证

    private java.time.LocalDateTime expireTime;   // 凭证过期时间
    private java.time.LocalDateTime lastSyncAt;   // 上次同步时间
    private Integer lastSyncCount;                // 上次同步条数
    private String lastError;                     // 最近一次错误（给人看的中文）
}
```
> 启动后 JPA 会自动建这张表（`ddl-auto=update`，见 application.properties 第 8 行），不用手动建库表。

新建 `repository\ThsBindingRepository.java`（抄 `FavoriteStockRepository` 的样子）：
```java
package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.ThsBinding;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;
import java.util.Optional;

@Repository
public interface ThsBindingRepository extends JpaRepository<ThsBinding, Long> {
    Optional<ThsBinding> findByUserId(Long userId);
}
```

### 5.3 服务层：ThsSyncService（同步自选股）

打开范本 `service\StockService.java`（看 `@Service`、`@Transactional`、构造器注入怎么写），
新建 `service\ThsSyncService.java`：

```java
package com.happyericsix.stocktracker.service;

// imports 自己加（参照 StockService）

@Service
public class ThsSyncService {

    // 依赖：ThsBindingRepository、FavoriteStockRepository、ThsClient
    // 构造器注入写法参照 StockService 第 40~49 行

    /**
     * 同步某用户的同花顺自选股到 favorite_stocks 表。
     * 规则（重要）：
     *   - 新股票 → 插入新行
     *   - 已有股票 → 跳过，绝不覆盖用户手填的 buyPrice / quantity
     */
    @Transactional
    public void syncFavorites(Long userId) {
        // TODO 实现：
        // 1. binding = thsBindingRepository.findByUserId(userId)，没有就 return
        // 2. 解密 binding.sessionEncrypted 拿到会话 → ThsClient.listGroups()
        // 3. 查出该用户现有自选股列表（favoriteStockRepository.findByUserId(userId)）
        // 4. 对每只同花顺股票：
        //    转成项目代码格式（如 600519.SH → SH600519）— 这步逻辑单独写个工具方法
        //    已存在 → 跳过；不存在 → 新建 FavoriteStock（buyPrice 可填同花顺参考价）
        // 5. 更新 binding 的 lastSyncAt / lastSyncCount
    }

    /** 工具方法：同花顺格式转项目格式。可单独测试。 */
    static String toProjectSymbol(String code, String market) {
        // 示例：code="600519", market="SH" → "SH600519"
        // 规则自己定，注意：纯代码 600519 与带前缀 SH600519 要能去重（见设计文档 §4）
        // TODO 实现
        return market + code;
    }
}
```

### 5.4 服务层：ThsQrService（扫码 → 发令牌）

先读 `service\AuthService.java`（重点第 49~52 行：怎么把"用户"变成"令牌"），
再新建 `service\ThsQrService.java`：

```java
package com.happyericsix.stocktracker.service;

// imports 自己加

@Service
public class ThsQrService {

    // 依赖（自己决定要哪些，参照 AuthService 的构造器注入）：
    //   ThsClient(Java侧)、ThsBindingRepository、UserRepository、
    //   TokenService、AuthenticationManager、PasswordEncoder
    //   + 一个异步执行器（参照 StockService 里 priceRefreshExecutor 的注入方式）

    public ThsQrCreateResponse createQr() {
        // TODO：调 ThsClient.createQr()，把 qrSessionId 暂存（Map 或 Redis），返回给前端
    }

    public ThsQrPollResponse pollQr(String qrSessionId) {
        // TODO：
        // 1. 调 ThsClient.pollQr(...)（Java 侧还没写 poll 方法？回去第 4 步补）
        // 2. 手机还没扫 → 返回 status=pending
        // 3. 扫了且成功 →
        //    a. 找该用户的 ThsBinding，没有就新建
        //    b. 加密存凭证
        //    c. 拿/建 User（参照 AuthService.register 里 User.builder() 的写法）
        //    d. 参照 AuthService 第 49~52 行：造 Authentication → tokenService.generateToken(...)
        //    e. 返回 {status:"ok", token, username}
        // 4. 同步自选可以后台异步触发：syncFavorites(user.getId())
    }
}
```

### 5.5 控制器（对外接口）

先读 `controller\AuthController.java`（薄控制器模板），新建 `controller\ThsController.java`：

```java
package com.happyericsix.stocktracker.controller;

// imports 自己加

@RestController
@RequestMapping("/api/v1/ths")
public class ThsController {

    // 依赖注入 ThsQrService / ThsSyncService / UserRepository

    @GetMapping("/qr/create")
    public Result<ThsQrCreateResponse> createQr() { /* TODO */ }

    @GetMapping("/qr/poll")
    public Result<ThsQrPollResponse> pollQr(@RequestParam String qrSessionId) { /* TODO */ }

    @PostMapping("/sync")
    public Result<String> sync(Authentication authentication) { /* TODO 拿用户名→userId→syncFavorites */ }

    @GetMapping("/status")
    public Result<ThsStatusResponse> status(Authentication authentication) { /* TODO */ }
}
```
> 🔎 自己查：`@RestController`、`@RequestMapping`、`@RequestParam`、`Authentication` 是什么。
> 你项目统一用 `Result<T>` 包返回（看 `controller\StockController.java` 怎么用 `Result.success(...)`）。

### 5.6 改 SecurityConfig（放行两个"不需要登录"的接口）

打开 `config\SecurityConfig.java`，**改第 45 行**那一句，把扫码接口也放行（因为扫码发生在登录前）：

```java
.requestMatchers("/api/v1/auth/login", "/api/v1/auth/register",
                 "/api/v1/ths/qr/create", "/api/v1/ths/qr/poll").permitAll()
```

### 5.7 验证

```powershell
cd stock-tracker
.\mvnw.cmd test          # 先保证编译+现有测试通过
.\mvnw.cmd spring-boot:run   # 启动（需要 MySQL + Redis 都在跑）
```
然后另开终端，按顺序验证（换成你的真实地址）：
```powershell
curl "http://localhost:8080/api/v1/ths/qr/create"
# 用返回的 qrSessionId 轮询（手机扫码后）：
curl "http://localhost:8080/api/v1/ths/qr/poll?qrSessionId=刚才的值"
# 轮询返回 token 后，再同步：
curl -X POST "http://localhost:8080/api/v1/ths/sync" -H "Authorization: Bearer 刚才的token"
```

### 5.8 预期看到什么

扫码确认后 poll 返回里带一个 `token`；用 token 调 sync 后，数据库 `favorite_stocks` 表
出现你的自选股（可用 Navicat/IDEA 数据库面板查看，或登录前端 Dashboard 看）。

**做完提交**：`feat(ths): qr login issues jwt + sync favorites`

---

## 第 6 步：前端登录页加"扫码"标签

> 目标：网页上点一下就能出二维码，手机扫完自动登录。需要等第 5 步后端真通了再做。

### 6.1 装二维码组件

```powershell
cd stock-tracker\frontend
npm install qrcode
```

### 6.2 新建 `frontend\src\api\ths.js`

打开 `frontend\src\api\auth.js` 看 4 行封装的风格，然后新建 `ths.js`：
```js
import request from './request.js'

export const createQr = () => request.get('/ths/qr/create')
export const pollQr = (qrSessionId) => request.get('/ths/qr/poll', { params: { qrSessionId } })
export const syncFavorites = () => request.post('/ths/sync')
```

### 6.3 改 `frontend\src\pages\Login.vue`

在"登录/注册"之外加一个"同花顺扫码"入口。核心逻辑（写到 `<script setup>` 里）：
1. 点按钮 → `createQr()` → 拿 `qrUrl`；
2. 用 qrcode 组件把 `qrUrl` 画成二维码显示（🔎 自己查：`qrcode npm 用法`）；
3. `setInterval` 每 4 秒 `pollQr(qrSessionId)`；
4. 返回 `status === 'ok'` → `localStorage.setItem('token', token)` → `router.push('/dashboard')`
   （抄现有 Login.vue 第 30~31 行的写法）；
5. 记得 `clearInterval`（组件卸载/成功时），否则登录后还在轮询。

### 6.4 验证

```powershell
cd stock-tracker\frontend
npm run dev
```
浏览器打开 `http://localhost:5173` → 登录页点扫码 → 手机同花顺扫码 → 自动进 Dashboard。

**做完提交**：`feat(ths): qr login ui`

---

## 第 7 步：端到端收尾

- Dashboard 顶部加"重新同步"按钮（调 `syncFavorites()`，参照 Dashboard.vue 现有按钮）。
- 过期处理：凭证过期时提示"请重新扫码"。数据来源是 **Java 自己的 `ths_bindings` 表**
  （`expireTime` / `lastSyncAt` / `lastError`），不需要调 Python——
  Python 侧的 `/ths/status` 已移除（见设计文档 §5.2 实现变更说明）。
- README 增补功能说明与免责（个人自用、非官方协议、自选加入价≠成本价）。

---

## 附：总体检查清单（做完一项打个勾）

> 进度更新：2026-09-14。**第 0~5 步全部完成并实测通过。**
>
> 实测链路：手机扫码 → Java 自动建号(`ths_cx00`) → AES/GCM 加密存凭证 → 签发 JWT
> → 同步 4 只自选股入库（`buy_price` 全为 NULL，未污染盈亏）
> → 二次扫码登进**同一账号**（`firstLogin=false`）、去重生效（`added=0, unchanged=4`）。
>
> **关于第 3 步的接口数量**：教程正文写的是 2 个（`qr/create` + `qr/poll`），
> 实际做了 **3 个** —— 多出一个 `/api/v1/ths/selfstocks`（读「我的自选」列表）。
> 原设计里的 `/ths/groups`（分组）和 Python 侧 `/ths/status` 已按需求移除，
> 详见设计文档 §5.2「实现变更」。
>
> **第 5 步的三个实现变更**（都写进设计文档 §14 了，这里只列要点）：
> ① 未用 Redis（Java 侧无状态，会话在 Python 内存）
> ② 扫码端点需对「无效 token」按路径例外处理，否则登录页扫码会 401
> ③ 用户绑定改为「按同花顺账号识别」，支持扫码登录与绑定共用一套身份

- [x] 0. 环境备齐（同花顺 App、VS Code、终端、Python venv 确认）
- [x] 1. `ths_probe_v2.py` 手机扫码成功打印登录凭证
- [x] 2. 探针读出自己同花顺的自选股
- [x] 3. `ths_client.py` + `app.py` 的 ths 接口，curl 通
- [x] 4. Java `ThsClient` 编译通过
- [x] 5. 扫码 → JWT → 同步入库 全通（MySQL+Redis 在跑）
- [ ] 6. 前端扫码登录自动跳转
- [ ] 7. 收尾（重同步按钮、过期提示、README）

## 附：建议的查资料顺序（按你卡住的点搜）

1. 完全不懂终端 → 搜 `powershell 基础命令`
2. Python 语法不熟 → 搜 `python 入门` / 边写边查
3. 不懂 FastAPI → 搜 `fastapi 教程 第一个接口`
4. 不懂 Java/Spring → 搜 `spring boot 入门 controller`
5. 不懂数据库表 → 搜 `jpa entity 是什么`
6. 同花顺接口细节 → GitHub 搜 `ths-favorite`、`thspypc`（社区逆向，字段以你打印为准）
