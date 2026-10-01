# 不用终端启动项目（全 GUI 操作）

> 2026-09-19 · 目标：**不敲任何命令**把 Stock Tracker 跑起来。
> 命令行的等价做法见 README 与 `start-all.ps1 -Status`。

## 0. 最省事：双击一个文件

**双击仓库根目录的 `start-all.bat`** —— 它会依次检查/启动 MySQL、Redis、Python、Java、前端，
最后打印一张状态表（哪个在线、哪个离线）与两个入口地址。

要看状态或停止服务，不需要改脚本：
把这个文件**复制成快捷方式**（右键 → 复制，然后在空白处右键 → 粘贴快捷方式），
再右键快捷方式 → 属性 → 在"目标"末尾加上 ` -Status`（或 ` -Stop`）。

> `start-all.bat` 只是 `start-all.ps1` 的包装：`.ps1` 双击是"用记事本打开"，
> `.bat` 双击才是"运行"。它同时绕过 PowerShell 执行策略。
> **它内部必须是纯 ASCII**：`cmd.exe` 用系统 OEM 代码页解析 `.bat`，
> 中文注释会被误读、甚至破坏行的解析（这个坑实际踩过）。

下面是各部分**单独启动**的做法（比如只想重启其中一个）。

---

## 1. MySQL（3306）— Windows 服务，不用管

它已经作为系统服务在跑（服务名 `MySQL80`）。要手动启停：

- `Win + R` → 输入 `services.msc` → 回车
- 找到 **MySQL80** → 右键 → 启动 / 停止 / 重新启动
- 或者：`Ctrl + Shift + Esc`（任务管理器）→ **服务** 标签 → 找到 MySQL80 → 右键

（本机是原生安装的 MySQL，不是容器。`application.properties` 指向 `localhost:3306/stockdb`。）

## 2. Redis（6379）— Docker Desktop

1. 开始菜单打开 **Docker Desktop**，等左下角出现绿色 "Engine running"
2. 左侧 **Containers** → 找到 `stock-redis` → 点那一行的 ▶（Start）
3. 容器不存在时（首次或换机器）：
   - 左侧 **Images** → 搜索框输 `redis` → 拉取 `redis:7-alpine`
   - 在镜像行点 **Run** → 展开 **Optional settings** → **Host port** 填 `6379` → **Run**
4. 验证：Containers 里 `stock-redis` 显示 Running 即可

> Redis 是可以跳过的：Java 侧缓存是"Caffeine(本地) + Redis(分布式)"两层，
> 没有 Redis 时给 Java 加一个环境变量 `SPRING_CACHE_TYPE=caffeine` 就能跑（缓存退化为本地）。
> 见下面第 4 节。

## 3. Python 数据服务（8000）— 在 IDE 里右键 Run

1. 用 PyCharm / IDEA 打开 **`python-data-service`** 这个目录（不是仓库根目录）
2. **File → Project Structure → SDK** 选 `python-data-service/.venv/Scripts/python.exe`
   （这个 venv 已经建好、依赖已装）
3. 打开 **`app.py`** → 点右上角绿色 ▶，或右键 → **Run 'app'**
4. 控制台出现 `Uvicorn running on http://127.0.0.1:8000` 即成功

> ⚠️ 这一步以前**不可能做到**：`app.py` 原先没有 `if __name__ == "__main__":` 入口，
> 右键 Run 只会导入模块然后立刻退出，什么都不起。2026-09-19 补上了这个入口
> （参数与 `start.ps1` 对齐：127.0.0.1:8000）。
>
> 这个入口**不带热重载**（热重载要求把应用写成 `"app:app"` 字符串，会依赖"工作目录正好是这个目录"，
> 而 IDE 的运行配置里工作目录常是仓库根）。要热重载就在 Run Configuration 里：
> - Module name: `uvicorn`
> - Parameters: `app:app --reload --port 8000`
> - Working directory: `python-data-service`

## 4. Java 后端（8080）— 在 IDE 里点绿色三角

1. 用 IDEA 打开**仓库根目录**（`.idea` 已经在这个仓库里）
2. 打开 `src/main/java/com/happyericsix/stocktracker/StocktrackerApplication.java`
3. 点类名左边（或右上角）的绿色 ▶ → **Run 'StocktrackerApplication'**
4. 控制台出现 `Tomcat started on port 8080` 即成功（首次启动要编译，约 20–40 秒）

**Redis 没起时**：`Run → Edit Configurations…` → 选中这个配置 →
**Environment variables** 里加一行 `SPRING_CACHE_TYPE=caffeine` → 保存后再 Run。

**端口被占**：同一个配置的 **VM options** 或 **Program arguments** 里加 `--server.port=8081`，
前端那边也要改代理目标（`frontend/vite.config.js` 的 `proxy['/api'].target`）。

## 5. 前端（5173）

**方式 A：VS Code（本机装在 `D:\soft\Microsoft VS Code`）**

仓库根目录已放好 `.vscode/tasks.json`（7 个任务），用 VS Code 打开**仓库根目录**后：

- `Ctrl + Shift + P` → 输入 `Run Task` → 回车 → 选 **前端: dev（http://localhost:5173）**
- 同一个菜单里还有：**全栈: 启动** / **全栈: 查看状态** / **全栈: 停止** /
  **Python: 数据服务（uvicorn --reload, :8000）** / **Java: 后端（:8080）** / **前端: build**

> `.vscode/` 在 `.gitignore` 第 35 行 —— 所以这份配置**只在本机生效、不进版本库**。
> 换机器要重新放一份（或者直接用 `start-all.bat`）。
>
> ⚠️ 我验证了：JSON 语法、里面引用的路径（`start-all.ps1` / `mvnw.cmd` /
> `.venv\Scripts\python.exe` / `frontend/package.json`）与 npm scripts 名都真实存在，
> 并且把任务里的命令原样跑了一遍（状态任务返回 5 个服务全在线）。
> **没验证的**是 VS Code 任务面板本身的执行（那要在界面里点一次），所以第一次点完请看一眼终端面板。

**方式 B：IDEA 的 npm 工具窗口**（需要 IDEA **Ultimate** + Node.js 插件）

右键 **`frontend/package.json`** → **Show npm Scripts** → 底部 npm 面板里双击 **`dev`**。

**方式 C：让它跟着一键脚本走**

`start-all.bat` 已经把前端一起拉起来了 —— 平时不需要单独启动它。


## 6. 起来之后怎么看是否正常

| 检查 | 位置 | 期望 |
|---|---|---|
| 四个端口 | `start-all.bat -Status` | 全部"在线" |
| Python + LLM | 浏览器打开 http://127.0.0.1:8000/health | `"status":"ok"`、`"llm_available":true` |
| 接口文档 | http://127.0.0.1:8000/docs | FastAPI 的接口列表 |
| 应用 | http://localhost:5173 | 登录后进自选股；点开一只股票看 K 线与事件区 |

## 7. 各部分之间的依赖（排查顺序）

```
浏览器 :5173  ──/api 代理──►  Java :8080  ──►  MySQL :3306
                                   │              （用户/自选/策略/新闻落库）
                                   └──►  Python :8000  ──► 外部数据源 + DeepSeek
                                        （行情/指标/资讯/解读）
Redis :6379 只服务 Java 的缓存层（可选，见第 2/4 节）
```

**页面白屏或转圈** → 先看 Java（8080）在不在；
**行情/资讯出不来但页面能开** → 看 Python（8000）在不在；
**登录不了** → 看 MySQL（3306）在不在。
