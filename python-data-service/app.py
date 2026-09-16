"""
FastAPI 入口 — 为 stock-tracker Java 后端提供实时行情接口。
"""

import logging
import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import date, timedelta

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from akshare_client import (get_quote, get_history, get_minute_kline, get_overview,
                            search_stocks, warm_stock_list)
# 注意:quant_model(用了 sklearn) 改成 lazy import,
# 避免启动时因 sklearn 缺失导致整个 app 挂掉
# 真正的 import 在用到 analyze_stock 的 endpoint 函数里
from models import StockQuoteResponse, GlobalQuote, StockHistoryResponse, MetaData, DailyPrice, StockOverviewResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """启动预热：把"第一次调用才付的代价"挪到没人等的时段。

    目前只有一件事：全 A 股名单要 6 秒（上游按 18 页翻），而它是 `search_stock` 的必经之路。
    预热失败不影响服务（`search_stocks` 会自己按冷却重试），所以这里绝不让它抛出去。
    注意用 lifespan 而不是 `@app.on_event`（后者已弃用，且会让测试日志里多一堆警告）。
    """
    try:
        warm_stock_list(background=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"启动预热失败（不影响服务）：{e}")
    yield


app = FastAPI(
    title="Stock Data Service (akshare)",
    description="为 stock-tracker Java 后端提供实时行情、K 线历史、基本面数据",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 服务间鉴权（仅允许 Java 后端调用） ====================
# 密钥读取优先级：系统 env > .env 文件（与 llm_service 语义一致）。
# INTERNAL_API_TOKEN 未配置时不启用校验（本地开发兜底）；生产必须配置，
# 且与 Java 侧 application.properties 的 internal.api-token 保持一致。


def _load_env_file():
    from pathlib import Path

    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    try:
        raw = env_path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("\"'").strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        pass


_load_env_file()
INTERNAL_API_TOKEN = os.getenv("INTERNAL_API_TOKEN", "").strip()


@app.middleware("http")
async def require_internal_token(request: Request, call_next):
    """服务间鉴权：**fail-closed**（P4a）。

    <h3>为什么改成 fail-closed</h3>
    改造前是"配了 token 才校验"—— 于是 `.env` 丢一行、部署时忘了设环境变量，
    `/api/v1/agent/chat`、`/api/v1/strategies/*`、以及能读回外部原文的
    `/api/v1/external/archive/{id}` 就**全部无鉴权**。而 Java 侧的同类接口
    （`InternalMemoryController.authorized`）在未配置密钥时是"一律拒绝"：
    同一个信任边界上两种默认值，这本身就是缺陷。

    安全相关的默认值不能是"放开"。所以未配置时直接拒绝，并且**大声说清原因**
    （503 + 明确文案），而不是让调用方以为是自己的 token 错了。

    `/health` 不在 `/api/v1/` 下，保持可访问 —— 否则运维连"为什么全 503"都看不到。
    """
    path = request.url.path
    if path.startswith("/api/v1/"):
        if not INTERNAL_API_TOKEN:
            logger.error("INTERNAL_API_TOKEN 未配置：拒绝 %s（fail-closed），请在 .env 配置后再调用",
                         path)
            return JSONResponse(status_code=503,
                                content={"detail": "internal token not configured; "
                                                   "this service rejects /api/v1 without it"})
        if request.headers.get("x-internal-token", "") != INTERNAL_API_TOKEN:
            return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    return await call_next(request)


# ==================== 健康检查 ====================

@app.get("/health")
def health():
    import llm_service
    return {
        # 内部鉴权没配好时，整体状态必须是 degraded：这不是"小毛病"，
        # 而是"所有内部接口都在拒绝服务"（fail-closed 的另一半：让降级可见）
        "status": "ok" if INTERNAL_API_TOKEN else "degraded",
        "internal_auth": {"configured": bool(INTERNAL_API_TOKEN), "mode": "fail_closed"},
        "llm_available": llm_service._is_available(),
        "llm_model": llm_service.MODEL,
        "memory": _memory_health(),
        "tools": _tools_health(),
        "external": _external_health(),
        "metering": _metering_health(),
        "objective_facts": _objective_health(),
        "audit": _audit_health(),
    }


def _audit_health():
    """一轮体检（W3）的自检：它查什么、容差多少、**是否阻断主流程**。

    为什么值得暴露两件事：一是"审计到底在跑没有"不能靠读日志确认；
    二是它 `blocking: false` 这一点必须看得见 —— 否则下一个人会以为它是门禁，
    从而不敢改任何可能触发它的行为（审计的价值在于被发现，不在于吓住人）。
    """
    try:
        from agent import tool_audit

        return tool_audit.describe()
    except Exception as e:  # noqa: BLE001 —— 健康检查不能因为子系统异常而挂掉
        logger.warning(f"audit health probe failed: {e}")
        return {"error": "audit unavailable"}


def _objective_health():
    """客观事实通道（W1）的自检：这条通道定义了哪些键、标注是什么。

    为什么值得暴露："客观事实是代码写进去的"这件事必须**可验证**，不能靠读日志猜。
    键清单摆在这里等于把跨语言契约放到台面上：Java 侧与 Python 侧不一致时，
    两边一对比就看出来了（这正是取代链最怕的静默漂移）。
    """
    try:
        from agent import objective

        return objective.describe()
    except Exception as e:  # noqa: BLE001 —— 健康检查不能因为子系统异常而挂掉
        logger.warning(f"objective facts health probe failed: {e}")
        return {"error": "objective facts unavailable"}


def _memory_health():
    """记忆子系统的自检：一眼看出"语义召回到底在工作没有"。

    为什么值得暴露：向量后端与 embedding 都是**可缺失的增强**，
    缺了不会报错、只会让检索退化成关键词 —— 那种"功能静默降级"最难排查。
    没有 key 时这里会明确显示 semantic_recall_enabled=False。
    """
    try:
        from agent import embeddings, vector_index

        index_stats = vector_index.stats()
        embedding_stats = embeddings.cache_stats()
        return {
            "vector_backend": index_stats.get("backend"),
            "semantic_recall_enabled": bool(embedding_stats.get("enabled")),
            "embedding_model": embedding_stats.get("model"),
            "embedding_cache_entries": embedding_stats.get("entries"),
            "index": index_stats,
        }
    except Exception as e:  # noqa: BLE001 —— 健康检查本身不能因为子系统异常而挂掉
        logger.warning(f"memory health probe failed: {e}")
        return {"error": "memory subsystem unavailable"}


def _tools_health():
    """工具层自检：声明是否自洽、装填后要花多少上下文。

    为什么把工具定义的成本放出来：它是**看不见的成本** ——
    没人会在 CI 里发现"工具定义已经占了 5000 token"。工具变多时先来这里看数字。
    """
    try:
        from agent import tool_sets
        from agent.tool_registry import validate

        info = tool_sets.describe()
        info["declaration_problems"] = validate()
        return info
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tool health probe failed: {e}")
        return {"error": "tool layer unavailable"}


def _external_health():
    """外部数据源自检（T2a）：现在是活的还是被摘了、TTL 多久、缓存了几条。

    为什么值得暴露：外部源的降级是**静默**的（工具从装填里消失、模型改用别的路），
    不主动看这里，只会觉得"今天它怎么不查新闻了"。
    """
    try:
        from agent import external_source, offload

        info = external_source.snapshot()
        info["archive_dir"] = str(offload.archive_dir())
        info["offload_threshold_chars"] = external_source.OFFLOAD_THRESHOLD_CHARS
        return info
    except Exception as e:  # noqa: BLE001
        logger.warning(f"external health probe failed: {e}")
        return {"error": "external layer unavailable"}


def _metering_health():
    """计量（P1）：这一层只把已经发生的事显示出来，不做任何拒绝。

    它是"限多少"这个问题的唯一事实来源 —— 没有它，跨请求配额只能拍脑袋定阈值。
    进程内计数重启即清零（长期数据在账本的 `kind=usage` 事件里）。
    """
    try:
        from agent import metering

        return metering.snapshot()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"metering health probe failed: {e}")
        return {"error": "metering unavailable"}


@app.get("/api/v1/external/archive/{offload_id:path}")
def external_archive(offload_id: str):
    """按存档编号取回外部数据的**完整原文**（T2.2）。

    为什么要有这个接口：超长结果落盘之后，如果没有任何取回方式，落盘就只是
    "把文件堆在磁盘上"。模型进上下文的只有摘要与编号（它没有读文件的能力），
    但人和排查流程需要能顺着编号看到原文 —— 账本里的 `meta.offload_id` 就是这条线索。
    """
    from agent import offload

    record = offload.load(offload_id)
    if record is None:
        return JSONResponse(status_code=404, content={"detail": "archive not found", "id": offload_id})
    return {
        "offload_id": record.get("offload_id"),
        "tool": record.get("tool"),
        "created_at": record.get("created_at"),
        "chars": record.get("chars"),
        "rows": record.get("rows"),
        "payload": record.get("payload"),
    }


# ==================== 股票搜索（Autocomplete）====================

@app.get("/api/v1/stocks/search")
def stock_search(keyword: str = Query(default="", description="搜索关键词（代码或名称）")):
    results = search_stocks(keyword)
    return {"keyword": keyword, "count": len(results), "results": results}


# ==================== 实时行情 ====================

def _quote_num(raw):
    """归一化腾讯行情里取到的字段。

    停牌或无数据时可能是空串或 '-'，统一成 None（前端据此显示"无涨跌信息"）。
    注意 '0.00' 是合法的"平盘"，**不能**当成缺失。
    """
    if raw is None:
        return None
    s = str(raw).strip()
    return s if s not in ("", "-", "--") else None


@app.get("/api/v1/quote/{symbol}", response_model=StockQuoteResponse)
def stock_quote(symbol: str):
    data = get_quote(symbol)
    if data is None:
        return StockQuoteResponse(globalQuote=None, note="No data")

    price = data.get("最新价", "")
    if not price or price == "0.0":
        price = None
    today_str = str(date.today())

    return StockQuoteResponse(
        globalQuote=GlobalQuote(symbol=symbol, price=price, lastTradingDay=today_str,
                                name=data.get("名称", symbol),
                                # 涨跌方向：get_quote() 已经解析好了，这里必须带上，
                                # 否则 Java 侧的 @JsonProperty 取不到值，前端就没有涨跌可判断。
                                previousClose=_quote_num(data.get("昨收")),
                                change=_quote_num(data.get("涨跌额")),
                                changePercent=_quote_num(data.get("涨跌幅"))),
    )


# ==================== K 线历史 ====================

@app.get("/api/v1/history/{symbol}", response_model=StockHistoryResponse)
def stock_history(
    symbol: str,
    start_date: str = Query(default="", description="起始日期 yyyyMMdd"),
    end_date: str = Query(default="", description="结束日期 yyyyMMdd"),
    period: str = Query(default="day", description="周期：day / week / month"),
):
    records = get_history(symbol, start_date=start_date, end_date=end_date, period=period)
    if records is None:
        return StockHistoryResponse(metaData=MetaData(symbol=symbol), timeSeries={})

    time_series = {}
    for r in records:
        day_key = r.get("date", "")
        time_series[day_key] = DailyPrice(
            open=r.get("open", "0"), high=r.get("high", "0"),
            low=r.get("low", "0"), close=r.get("close", "0"),
            volume=r.get("volume", "0"),
        )

    return StockHistoryResponse(metaData=MetaData(symbol=symbol), timeSeries=time_series)


# ==================== 分钟 K 线（A 股）====================

@app.get("/api/v1/minute/{symbol}")
def stock_minute(
    symbol: str,
    period: int = Query(default=5, description="分钟周期：1 / 5 / 15 / 30 / 60"),
):
    """返回 A 股分钟 K 线（akshare 数据源）。仅支持 A 股。直接返回 list，兼容 DailyStockResponse。"""
    records = get_minute_kline(symbol, period=period)
    if records is None:
        return []
    out = []
    for r in records:
        try:
            out.append({
                "date": r.get("date", ""),
                # 保留数据源原始小数位（如 "1296.300"），转 float 会丢尾零
                "open": str(r.get("open", "0")),
                "close": str(r.get("close", "0")),
                "high": str(r.get("high", "0")),
                "low": str(r.get("low", "0")),
                "volume": int(float(r.get("volume", 0))),
            })
        except (ValueError, TypeError):
            continue
    return out


# ==================== 基本面概况 ====================

@app.get("/api/v1/overview/{symbol}", response_model=StockOverviewResponse)
def stock_overview(symbol: str):
    data = get_overview(symbol)
    if data is None:
        return StockOverviewResponse(symbol=symbol, name=symbol)

    return StockOverviewResponse(
        symbol=symbol,
        name=data.get("名称", symbol),
        marketCapitalization=data.get("总市值", "N/A"),
        peRatio=data.get("市盈率-动态", "N/A"),
        description="",
        sector="",
        industry="",
        dividendYield="N/A",
    )


# ==================== 量化分析 ====================

@app.get("/api/v1/indicators/{symbol}")
def stock_indicators(symbol: str):
    """量化指标分析（默认不包含模型预测）"""
    try:
        # lazy import:避免启动时因 sklearn 缺失导致整个 app 挂掉
        from quant_model import analyze_stock
        records = get_history(symbol)
        if records is None or len(records) < 20:
            return {"symbol": symbol, "error": "历史数据不足 20 条"}

        prices = []
        for r in records:
            try:
                prices.append(float(r["close"]))
            except (ValueError, KeyError):
                continue

        if len(prices) < 20:
            return {"symbol": symbol, "error": f"有效数据不足({len(prices)}条)"}

        result = analyze_stock(prices, symbol)
        return result
    except Exception as e:
        logger.error(f"量化分析失败 {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": "量化分析暂不可用，请稍后重试"}


# ==================== 回测接口 ====================

@app.get("/api/v1/backtest/{symbol}")
def run_backtest_endpoint(
    symbol: str,
    capital: float = Query(default=100000, description="初始资金"),
):
    """
    对指定股票运行回测，返回所有策略的结果。

    策略包括：
    - signal: 基于规则技术信号（RSI/MACD/均线）
    - buy_and_hold: 买入持有基准
    """
    try:
        from backtest import run_comprehensive_backtest, BacktestEngine
        # lazy import:同上,analyze_stock 也可能缺依赖
        from quant_model import analyze_stock

        records = get_history(symbol)
        if not records or len(records) < 20:
            return {"symbol": symbol, "error": "数据不足，至少需要 20 个交易日"}

        prices = [float(r["close"]) for r in records
                  if r.get("close") and float(r["close"]) > 0]

        if len(prices) < 20:
            return {"symbol": symbol, "error": "有效价格数据不足"}

        # 运行分析获取信号和预测
        analysis = analyze_stock(prices, symbol)
        signal = analysis.get("signal", {})

        # 运行回测
        results = run_comprehensive_backtest(
            symbol, prices,
            signal=signal,
            predictions=None,
            rl_result=None,
            initial_capital=capital,
        )

        # 格式化输出
        output = {
            "symbol": symbol,
            "data_points": len(prices),
            "initial_capital": capital,
            "buy_and_hold_return_pct": round(results["buy_and_hold"] * 100, 2),
            "best_strategy": results["best_mode"],
            "strategies": {},
        }

        for mode, r in results.items():
            if mode in ("best_mode", "best_return", "buy_and_hold"):
                continue
            if hasattr(r, 'total_return_pct'):
                output["strategies"][mode] = {
                    "total_return_pct": r.total_return_pct,
                    "annualized_return_pct": round(r.annualized_return * 100, 2),
                    "sharpe_ratio": r.sharpe_ratio,
                    "max_drawdown_pct": r.max_drawdown_pct,
                    "win_rate": round(r.win_rate * 100, 1),
                    "total_trades": r.total_trades,
                    "excess_return_pct": round(r.excess_return * 100, 2),
                    "grade": BacktestEngine()._grade(r),
                }

        return output

    except Exception as e:
        logger.error(f"回测失败 {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": "回测暂不可用，请稍后重试"}


# ==================== 模型实验摘要 ====================

@app.get("/api/v1/models/summary")
def models_summary(symbol: str = Query(default=None, description="可选：按股票代码筛选")):
    """
    获取 MLflow 训练实验摘要。

    Returns:
        {"experiments": [...], "best_models": {...}}
    """
    try:
        from mlflow_utils import get_model_summary, load_best_model

        summary = get_model_summary(symbol)
        best = load_best_model()

        return {
            "total_runs": len(summary),
            "runs": summary[:20],  # 最多返回 20 条
            "best_model": {
                "run_id": best["run_id"] if best is not None and hasattr(best, '__getitem__') else None,
            } if best is not None else None,
        }
    except Exception as e:
        logger.error(f"获取模型摘要失败: {e}", exc_info=True)
        return {"error": "模型摘要暂不可用"}


# ==================== Agent 与策略接口 ====================


@app.post("/api/v1/agent/chat")
async def agent_chat(req: Request):
    """站内聊天机器人入口。

    请求体：{"user_id": "<任意字符串>", "message": "<用户输入>",
             "session_id": "<可选，会话标识>",
             "role": "<可选，这一轮是谁在跑，如 strategy_critic>",
             "history": [{"role": "user|assistant", "content": "..."}]}

    正式契约是 user_id。历史上 Java 的 ChatService.callLlm 发的是 user_name，
    导致 user_id 取到空串、直接落到下面的空 replies 分支 —— LLM 完全没被调用，
    而 Java 收到空数组后用"没理解你的问题"兜底，整条链路看起来像坏了却没有任何报错。
    所以这里同时接受两个键名，让这一类"两边字段名对不上就静默失效"的故障无法再发生。

    `history` 是多轮对话的关键：改造前这里只收 user_id + message，agent 每轮都是
    全新失忆状态，"它呢？""把止损改成 5%" 这类追问必然失效。历史由 Java 侧从
    message 表带过来（见 ChatService.buildHistory），Python 侧只做清洗与预算裁剪
    （agent/memory.py），保持无状态。

    契约由 tests/test_agent_chat_contract.py 钉住。
    """
    try:
        import agent.react_agent as react_agent
        data = await req.json()
        user_id = str(data.get("user_id") or data.get("user_name") or "").strip()
        message = data.get("message", "").strip()
        if not user_id or not message:
            return {"replies": [], "strategy_json": None}
        history = data.get("history")
        if not isinstance(history, list):
            history = []
        session_id = str(data.get("session_id") or "").strip()
        # 角色（W2）：由**调用方**声明"这一轮是谁在跑"，缺省 = 主 agent。
        # 它只是个标记（决定账本条目算不算"用户的对话"），**不是权限** ——
        # 权限仍然只由 scopes 决定，角色不能靠自己的名字主张任何能力。
        role = str(data.get("role") or "").strip() or None
        # 记忆账本用数字 userId（user_id 字段是用户名，两者语义不同）
        memory_user_id = data.get("memory_user_id")
        if isinstance(memory_user_id, str):
            memory_user_id = memory_user_id.strip() or None
        # 工具装填（T1）：调用方可以显式声明模式与权限，两者都不做任何意图猜测。
        # 不传 = 全给 + 普通用户默认权限，与改造前行为一致。
        tool_mode = str(data.get("tool_mode") or "").strip() or None
        scopes = data.get("scopes")
        if not isinstance(scopes, list) or not scopes:
            scopes = None
        result = await asyncio.to_thread(
            react_agent.run_agent, user_id, message,
            history=history, session_id=session_id, memory_user_id=memory_user_id,
            tool_mode=tool_mode, scopes=scopes, role=role
        )
        return result
    except Exception as e:
        logger.error(f"agent chat error: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"], "strategy_json": None}


@app.post("/api/v1/memory/consolidate")
async def memory_consolidate(req: Request):
    """把一个会话总结成"前情提要"（由 Java 侧在用户跨天进入新会话时触发）。

    巩固是异步、可重试的后台工作：
    - 事件太少、模型不可用、输出不是合法 JSON —— 都只返回结构化错误，<b>不落库</b>，
      宁可这次没有摘要，也不要一条没法追溯的坏摘要；
    - 摘要带 sourceEventIds，可回查账本；重新生成只会追加新版本，不覆盖旧版本。
    """
    try:
        import agent.consolidate as consolidate
        data = await req.json()
        user_id = data.get("user_id")
        session_key = str(data.get("session_key") or "").strip()
        if user_id is None or not session_key:
            return {"error": "user_id and session_key are required"}
        return await asyncio.to_thread(consolidate.consolidate_session, user_id, session_key)
    except Exception as e:
        logger.error(f"memory consolidate error: {e}", exc_info=True)
        return {"error": "consolidate failed"}


@app.get("/api/v1/agent/diagnostic/{symbol}")
async def agent_diagnostic(symbol: str):
    """Return risk metrics and optional model diagnostics for one symbol.

    <h3>为什么这里必须走管线（P4b）</h3>
    改造前它直接调 `tool_registry.execute_tool(name, args)`，**完全绕过策略层**：
    没有 ToolContext、没有 scope 检查、没有预算、没有外部源门禁。
    今天它只调两个只读工具，爆炸半径小；但它是"以后加工具时顺手复用一下"的天然候选，
    而它不会报错、只会悄悄跳过所有策略 —— 这类入口必须在它长出后果之前先关掉。

    对外契约保持不变（裸负载），所以这里仍然在末尾解包信封。
    """
    try:
        import agent.tool_registry as tool_registry
        from agent import tool_contract, tool_pipeline, tool_scope

        def collect():
            token = tool_scope.begin(
                user_id="internal-diagnostic",
                session_key=None,
                scopes=tool_registry.DEFAULT_USER_SCOPES,
                request_id=f"diagnostic:{symbol}",
            )
            try:
                def payload(name, args):
                    # 工具层统一返回 {ok, data, error, meta} 信封；
                    # 这个端点的对外契约保持不变（裸负载），所以在这里解包。
                    # 走管线（而不是直接 execute_tool）意味着：授权、预算、外部源门禁、
                    # 计量一个都不会被跳过。
                    envelope = tool_pipeline.call_tool_bounded(
                        name, args, spec=tool_registry.get_spec(name),
                        handler=tool_registry.execute_tool)
                    if not tool_contract.is_ok(envelope):
                        logger.warning("diagnostic 工具被拒/失败 tool=%s: %s",
                                       name, tool_contract.error_text(envelope))
                    return tool_contract.data_of(envelope)

                return {
                    "symbol": symbol,
                    "risk": payload("get_risk_metrics", {"symbol": symbol}),
                    "model_status": payload("get_model_status", {"symbol": symbol}),
                    "model_consensus": payload("get_model_consensus", {"symbol": symbol}),
                    "disclaimer": "模型诊断仅作低权重参考，不构成投资建议，不参与策略买卖决策。",
                }
            finally:
                tool_scope.reset(token)

        return await asyncio.to_thread(collect)
    except Exception as e:
        logger.error(f"agent diagnostic error for {symbol}: {e}", exc_info=True)
        return {"symbol": symbol, "error": "模型诊断暂不可用"}


@app.post("/api/v1/strategies/validate")
async def validate_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    try:
        data = await req.json()
        strategy_json = data.get("strategy_json", {})
        if not isinstance(strategy_json, dict):
            return {"valid": False, "error": "strategy_json must be an object", "normalized": None}
        cfg, err = validate_strategy_config(strategy_json)
        return {"valid": err is None, "error": err, "normalized": cfg.model_dump() if cfg else None}
    except Exception as e:
        logger.error(f"strategy validation error: {e}", exc_info=True)
        return {"valid": False, "error": "策略校验失败，请稍后重试", "normalized": None}


@app.post("/api/v1/strategies/backtest")
async def backtest_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import run_backtest, run_backtest_realistic
    from akshare_client import get_history
    try:
        data = await req.json()
        cfg_json = data.get("strategy_json", {})
        cfg, err = validate_strategy_config(cfg_json)
        if err:
            return {"valid": False, "error": err}
        # 默认真实化执行（次日开盘成交/整手/税费/涨跌停/节假日守卫）；
        # 传 "execution":"ideal" 可回退到旧的理想模型做 A/B 对比。
        execution = str(data.get("execution", "realistic")).lower()
        runner = run_backtest if execution == "ideal" else run_backtest_realistic
        records = await asyncio.to_thread(get_history, cfg.symbol)
        backtest = await asyncio.to_thread(runner, cfg_json, records or [])
        return {"valid": True, "backtest": backtest}
    except Exception as e:
        logger.error(f"strategy backtest error: {e}", exc_info=True)
        return {"valid": False, "error": "策略回测失败，请稍后重试"}


@app.post("/api/v1/strategies/evaluate-bar")
async def evaluate_bar_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import evaluate_bar
    from akshare_client import get_history
    from akshare_client import get_minute_kline
    try:
        data = await req.json()
        cfg, err = validate_strategy_config(data.get("strategy_json", {}))
        if err:
            return {"valid": False, "error": err}
        symbol = data.get("symbol") or cfg.symbol
        period = str(data.get("period", "day")).lower()
        if period in ("1", "5", "15", "30", "60"):
            records = await asyncio.to_thread(get_minute_kline, symbol, int(period))
        else:
            records = await asyncio.to_thread(get_history, symbol)
        if not records:
            return {"error": "insufficient history data", "signal": "hold", "matched_conditions": []}
        bar_time = str(records[-1].get("date", ""))
        position = data.get("position")
        if isinstance(position, dict) and "quantity" in position and "shares" not in position:
            position = dict(position)
            position["shares"] = position["quantity"]
        result = await asyncio.to_thread(
            evaluate_bar, cfg.model_dump(), records, data.get("date", "") or bar_time, position
        )
        result["bar_time"] = bar_time
        return result
    except Exception as e:
        logger.error(f"strategy evaluate-bar error: {e}", exc_info=True)
        return {"error": "行情评估失败，请稍后重试", "signal": "hold", "matched_conditions": []}


# ==================== 同花顺扫码登录 ====================

QR_STORE: dict = {}

# 扫码会话有效期（秒）。同花顺自己给二维码 ~120 秒，这里对齐。
QR_SESSION_TTL = 120
QR_POLL_INTERVAL_MS = 4000


def _ths_error(message: str, status_code: int = 502):
    return JSONResponse(status_code=status_code, content={"ok": False, "error": message})


@app.get("/api/v1/ths/qr/create")
def ths_qr_create():
    import ths_client
    import uuid

    client = ths_client.ThsClient()
    try:
        qr = client.create_qr()
    except ths_client.ThsApiError as e:
        logger.warning(f"ths qr create failed: {e.message}")
        return _ths_error(e.message)

    # 清掉过期的旧会话，避免字典无限增长
    now = time.time()
    for sid in [k for k, v in QR_STORE.items() if now - v["created_at"] > QR_SESSION_TTL]:
        QR_STORE.pop(sid, None)

    qr_session_id = uuid.uuid4().hex
    QR_STORE[qr_session_id] = {
        "client": client,          # 复用同一个 Session（同花顺 cookie 在里面）
        "qrid": qr["qrid"],
        "created_at": now,
    }
    logger.info(f"ths qr created: session={qr_session_id} qrid={qr['qrid']}")

    return {
        "ok": True,
        "data": {
            "qrSessionId": qr_session_id,
            "qrUrl": qr["qr_url"],
            "pollIntervalMs": QR_POLL_INTERVAL_MS,
            "expiresInSec": QR_SESSION_TTL,
        },
    }

#进行轮询检查是否属于有效期内
@app.get("/api/v1/ths/qr/poll")
def ths_qr_poll(qrSessionId: str = ""):
    import ths_client

    entry = QR_STORE.get(qrSessionId)
    if entry is None:
        return _ths_error("二维码已过期或不存在，请点击刷新重新生成", status_code=404)

    try:
        session = entry["client"].poll_qr(entry["qrid"], wait_seconds=3)
    except ths_client.ThsApiError as e:
        # 用 e.code 判断类型，**不要对 e.message 做字符串匹配**。
        # TIMEOUT = 手机还没扫，属于正常状态 → 返回 pending 让前端继续轮询。
        # （曾经写成 `if "超时" in e.message`：文案一改就失效，
        #   而且别的错误消息里恰好含"超时"时还会误判成正常状态。）
        if e.code == ths_client.ThsApiError.TIMEOUT:
            if time.time() - entry["created_at"] > QR_SESSION_TTL:
                QR_STORE.pop(qrSessionId, None)
                return _ths_error("二维码已过期，请点击刷新重新生成", status_code=404)
            return {"ok": True, "data": {"status": "pending"}}
        logger.warning(f"ths qr poll failed [{e.code}]: {e.message}")
        QR_STORE.pop(qrSessionId, None)
        return _ths_error(e.message)

    # 扫到了 —— 会话用完即弃
    QR_STORE.pop(qrSessionId, None)
    logger.info(f"ths qr confirmed: account={session['account']}")
    return {"ok": True, "data": {"status": "ok", "session": session}}


# ==================== 读取自选股 ====================


@app.post("/api/v1/ths/selfstocks")
async def ths_selfstocks(req: Request):

    import ths_client

    try:
        body = await req.json()
    except Exception:
        return _ths_error("请求体不是合法 JSON", status_code=400)

    account = str(body.get("account") or "")
    password = str(body.get("password") or "")
    if not account or not password:
        return _ths_error("缺少 account 或 password", status_code=400)

    client = ths_client.ThsClient()
    try:
        data = client.get_self_stocks(account, password)
    except ths_client.ThsApiError as e:
        # 日志里只记 code 和错误摘要，绝不记凭证
        logger.warning(f"ths selfstocks failed [{e.code}]: {e.message}")
        return _ths_error(e.message)

    return {"ok": True, "data": data}


