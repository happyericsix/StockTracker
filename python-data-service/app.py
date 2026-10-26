"""
FastAPI 入口 — 为 StockTracker Java 后端提供实时行情接口。
"""

import hmac
import logging
import asyncio
import os
import time

import requests
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from akshare_client import (get_quote, get_history, get_minute_kline, get_overview,
                            search_stocks, warm_stock_list)
# news_client 是模块级导入而不是 lazy：它只依赖 akshare / akshare_client，
# 两者本来就是这个进程的启动依赖，不存在"某个可选包缺失导致 app 起不来"的风险。
# （与之对照：quant_model 导了 lightgbm/numpy 一票重依赖，所以它必须 lazy —— 见下面的注释。）
import news_client
import news_clustering
import news_topics
import news_understanding
import sentiment_client
# 交易日历：行情接口要回答"这个价是哪一天的"，前端要回答"今天休市、休到哪天"。
# 它只依赖 akshare（本进程的启动依赖），所以可以模块级导入。
import trading_calendar
# 注意:quant_model(带 lightgbm 等重依赖) 改成 lazy import,
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
    # 交易日历同理：第一次取要打新浪，挪到没人等的时段。
    # 它比股票名单更关键 —— 拿不到就只能说"不知道今天开不开市"。
    try:
        trading_calendar.warm(background=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"交易日历预热失败（不影响服务）：{e}")
    # MCP 工具挂载（2026-10-20）：按 .env 的 MCP_SERVERS 把远端只读工具装进
    # ToolSpec 注册表。未配置时是 no-op；某台服务器连不上只记状态，不炸启动。
    # 放在预热段是因为 list_tools 要真实握手——不能让第一个用户请求付这个代价。
    try:
        import asyncio

        from agent import mcp_tools

        # mount_all 是同步阻塞（握手 + list_tools），放进线程别卡事件循环
        await asyncio.to_thread(mcp_tools.mount_all)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"MCP 工具挂载失败（不影响服务）：{e}")
    yield


app = FastAPI(
    title="Stock Data Service (akshare)",
    description="为 StockTracker Java 后端提供实时行情、K 线历史、基本面数据",
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
        # 常量时间比较：非常量时间的 == 在理论上可被计时侧信道逐字节猜 token
        if not hmac.compare_digest(request.headers.get("x-internal-token", ""), INTERNAL_API_TOKEN):
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
        "mcp": _mcp_health(),
        "metering": _metering_health(),
        "objective_facts": _objective_health(),
        "audit": _audit_health(),
        "review": _review_health(),
        "strategy_review": _strategy_review_health(),
        "trading_committee": _committee_health(),
        "execution_contract": _contract_health(),
    }


def _mcp_health():
    """MCP 挂载状态：哪些服务器挂上了/挂了、共挂了几个工具。"""
    try:
        from agent import mcp_tools

        return mcp_tools.snapshot()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"mcp health probe failed: {e}")
        return {"error": "MCP 挂载状态暂不可用"}


def _strategy_review_health():
    """策略审查的自检：**哪一半是算术、哪一半靠模型**，以及证据是什么形式。

    把这条分界线摆出来，是因为两者的可信度完全不同：确定性检查"一条不漏"是硬要求，
    语义检查则可能判错 —— 混在一起说，会让人以为整个审查都同样可靠。
    """
    try:
        from agent import strategy_review

        return strategy_review.describe()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"strategy review health probe failed: {e}")
        return {"error": "策略复盘子系统暂不可用"}


def _committee_health():
    """多角色委员会的自检：**角色、预算、as-of 守卫**这三件事必须能被看见。

    为什么值得放进 /health：这三条正是"把决策交给模型"之后最容易悄悄失效的东西 ——
    预算设小了、as-of 守卫被绕过、角色少了两个，运行起来都不会报错，
    只会让决策变得不可解释。
    """
    try:
        from agent import trading_committee

        return trading_committee.describe()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"trading committee health probe failed: {e}")
        return {"error": "交易委员会子系统暂不可用"}


def _contract_health():
    """执行契约的自检：**当前生效**的封闭集与口径（含 decision_mode）。"""
    try:
        from agent import execution_contract as ec

        return ec.describe()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"execution contract health probe failed: {e}")
        return {"error": "执行契约子系统暂不可用"}


def _review_health():
    """裁决者（critic）的自检：它**没有工具、不阻断、判"合法"必须引证据**。

    这三条必须能被看见：一个"审查者"如果自己能被说服，它比没有更糟
    （它会给出一个看起来很权威的肯定）。
    """
    try:
        from agent import review

        return review.describe()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"review health probe failed: {e}")
        return {"error": "复盘子系统暂不可用"}


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
        return {"error": "审计子系统暂不可用"}


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
        return {"error": "客观事实子系统暂不可用"}


def _memory_health():
    """记忆子系统的自检：一眼看出"语义召回到底在工作没有"。

    为什么值得暴露：向量后端与 embedding 都是**可缺失的增强**，
    缺了不会报错、只会让检索退化成关键词 —— 那种"功能静默降级"最难排查。
    没有 key 时这里会明确显示 semantic_recall_enabled=False。

    java_reachable 是 2026-10-06 记忆系统 P0 排查后补的：当时部署编排漏配
    JAVA_BASE_URL，Python 容器里的默认 http://localhost:8080 指向自己，
    所有记忆读回调用静默失败（fail-open 连 warning 都没有），而这里只看
    embedding，导致 /health 显示 ok。探针只验证"HTTP 层能不能到达 Java"——
    任何状态码（含 400/401）都算可达，只有连接异常才算断。
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
            "java_reachable": _java_memory_reachable(),
        }
    except Exception as e:  # noqa: BLE001 —— 健康检查本身不能因为子系统异常而挂掉
        logger.warning(f"memory health probe failed: {e}")
        return {"error": "记忆子系统暂不可用"}


def _java_memory_reachable() -> bool:
    """探测 Python → Java 记忆接口的连通性（只看传输层，不要求业务成功）。

    任何 HTTP 状态码都算可达——包括 4xx/5xx：缺参 400、无 token 401、甚至被
    全局异常处理器包成的 500，都证明请求到达了 Java（服务器上实测就是 500，
    一开始写成 "<500 才算通" 导致探针误报 False，而真实记忆调用是通的）。
    只有连接被拒/超时（requests 抛异常）才是真的断。
    """
    try:
        from agent import memory_store

        base = memory_store._base_url()
        requests.get(f"{base}/api/v1/internal/memory/context", timeout=3)
        return True
    except Exception:  # noqa: BLE001 —— 连接被拒/超时 = 不可达
        return False


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
        return {"error": "工具层暂不可用"}


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
        return {"error": "外部数据层暂不可用"}


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
        return {"error": "计量子系统暂不可用"}


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


# ==================== 市场状态（交易日历） ====================

@app.get("/api/v1/market/status")
def market_status():
    """今天开不开市、休到哪天、这个价是哪一天的。

    <h3>为什么要有这条接口</h3>
    休市日腾讯行情照样返回上一交易日的收盘价，前端如果只看行情自身，
    **没有任何办法**分辨"现在是交易中的实时价"还是"周五的收盘价"。
    交易日历是唯一的外部事实来源，所以它必须单独暴露出来。

    返回结构见 `trading_calendar.status()`。`note` 是已经拼好的中文句子，
    前端直接渲染即可；结构化字段留给需要做判断的地方（例如给行情卡打
    "最近交易日收盘"的标记）。
    """
    return trading_calendar.status()


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


@app.get("/api/v1/dividends/{symbol}")
def dividends(symbol: str):
    """分红送配（内部接口）：模拟盘的除权除息调整数据源。

    每股口径（cash_per_share / stock_per_share），只含已定除权除息日的行；
    拉取失败返回空列表（结算侧 fail-open：拿不到就不调整，下次再试）。
    """
    from akshare_client import get_dividends
    try:
        rows = get_dividends(symbol)
    except Exception as e:
        logger.error(f"dividends query failed for {symbol}: {e}")
        rows = None
    return {"symbol": symbol, "dividends": rows or []}


@app.get("/api/v1/valuation/{symbol}")
def valuation_history_endpoint(symbol: str, window: int = 1250):
    """PE-TTM 与滚动 5 年分位的日线序列（K 线页估值副图）。

    与 market_context 估值语境、DSL pe_percentile 条件同一分位口径；
    上游 12h 缓存（get_valuation_history），这里不再叠加。fail-open。
    """
    from akshare_client import get_valuation_percentile_series
    rows = get_valuation_percentile_series(
        str(symbol).strip(), window=max(250, min(int(window), 2500)))
    return {"ok": bool(rows), "items": rows,
            "note": "分位=当日 PE 在此前 window 个交易日（含当日）中的百分位；早期不足 250 根为空"}


@app.get("/api/v1/quote/{symbol}", response_model=StockQuoteResponse)
def stock_quote(symbol: str):
    data = get_quote(symbol)
    if data is None:
        return StockQuoteResponse(globalQuote=None, note="No data")

    price = data.get("最新价", "")
    if not price or price == "0.0":
        price = None

    # 这个价属于哪一个交易日 —— **不能**写 `date.today()`。
    #
    # 原来的写法是 `lastTradingDay=str(date.today())`，于是一个周日的行情被标成"周日的数据"：
    # 腾讯在休市日回的其实是周五收盘价、`涨跌幅` 也是周五当天的涨跌，前端却渲染成
    # "更新 2026-09-20"。用户看到的直接反应就是"明明在休息日，为什么还有今天的涨跌"。
    # 现在改成问交易日历：今天开市且已过集合竞价 → 今天；否则 → 上一个交易日。
    market = trading_calendar.status()
    quote_day = market.get("quoteDate") or str(date.today())

    return StockQuoteResponse(
        globalQuote=GlobalQuote(symbol=symbol, price=price, lastTradingDay=quote_day,
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


@app.get("/api/v1/market/fund-flow")
def market_fund_flow(limit: int = 60):
    """大盘主力资金流日线（净额单位亿元，升序，最后一根最新）。

    Dashboard 资金卡与助手工具 get_market_fund_flow 共用；中间件已按
    /api/v1/* 校验 X-Internal-Token。fail-open：取不到 items=[]（前端软失败）。
    """
    from akshare_client import get_market_fund_flow
    rows = get_market_fund_flow(limit=max(1, min(int(limit), 250)))
    return {"ok": bool(rows), "items": rows,
            "note": "主力净流入=超大单+大单（东财优先，机房不可达时自动切腾讯源；"
                    "腾讯源日级仅主力+当日四档）。正=净流入。"}


# ==================== 个股资金流（K 线页主力资金卡，2026-10-19） ====================

@app.get("/api/v1/fund-flow/{symbol}")
def stock_fund_flow(symbol: str, limit: int = Query(default=30)):
    """个股主力资金流日线（腾讯源，与大盘同接口换代码）。fail-open：items=[]。"""
    from akshare_client import get_stock_fund_flow
    rows = get_stock_fund_flow(symbol, limit=max(1, min(int(limit), 50)))
    return {"ok": bool(rows), "items": rows,
            "note": "主力净流入=超大单+大单（腾讯口径）；四档仅当日有值。正=净流入。"}


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
        # lazy import:避免启动时因重依赖(lightgbm/numpy)缺失导致整个 app 挂掉
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


def _llm_callables(data, scope_id):
    """BYOK（2026-10-25）：把请求体的 llm_providers 绑定成 LLM 调用对。

    契约（见 llm_service.user_providers）：请求体带 llm_providers 字段（哪怕空数组，
    = 用户没配 Key）即用户级调用——只用用户自己的供应商，绝不落到部署者的全局 key；
    不带该字段 = 系统级调用（新闻解读等共享流水线），走 env 供应商。

    返回 (completion, completion_stream)：用户级时是绑定了 providers 的 partial，
    系统级时 (None, None)（调用方各自回落 llm_service.*）。Java 保存配置时已校验
    过形状，这里再校验一次是纵深防御；失败抛 ValueError，由端点兜成错误响应——
    契约被破坏必须让人看见，静默换回全局 key 等于替陌生人付钱。
    """
    import llm_service

    providers = llm_service.user_providers(data.get("llm_providers"), scope_id)
    if providers is None:
        return None, None
    return llm_service.bind_user_calls(providers)


def _parse_agent_chat_body(data):
    """agent chat 请求体的公共解析（非流式 /api/v1/agent/chat 与流式 /stream 共用）。

    返回 None = 空值守卫（缺 user_id 或 message），调用方快速返回空 replies；
    否则返回可直接展开进 run_agent 的关键字 dict。字段清洗规则两边必须一字不差
    ——历史上两边分叉出现过"user_name/user_id 对不上、LLM 静默没被调用"的故障，
    契约由 tests/test_agent_chat_contract.py 钉住。
    """
    user_id = str(data.get("user_id") or data.get("user_name") or "").strip()
    message = data.get("message", "").strip()
    if not user_id or not message:
        return None
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
    # 聚焦对象（agent 互通 P2）：用户从资讯雷达"问 AI"进入的聚焦对话。
    # 只认 {"type":"news_event","event_id":n}；其余形状忽略（run_agent 内 fail-open）。
    focus = data.get("focus")
    if not isinstance(focus, dict):
        focus = None
    # 连发中断（2026-10-20）：epoch 是 Java 侧的聊天轮次号。不带（旧客户端/其他
    # 调用方）= 不启用中断，行为不变。
    epoch = data.get("epoch")
    if isinstance(epoch, bool) or not isinstance(epoch, int):
        epoch = None
    # BYOK（2026-10-25）：llm_providers 绑定成用户自己的 LLM 调用（冷却名按用户
    # 命名空间隔离）。scope 优先数字 memory_user_id（与记忆账本同一身份口径）。
    completion, completion_stream = _llm_callables(
        data, memory_user_id if memory_user_id is not None else user_id)
    return {"user_id": user_id, "message": message, "history": history,
            "session_id": session_id, "role": role, "memory_user_id": memory_user_id,
            "tool_mode": tool_mode, "scopes": scopes, "focus": focus, "epoch": epoch,
            "completion": completion, "completion_stream": completion_stream}


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
        parsed = _parse_agent_chat_body(data)
        if parsed is None:
            return {"replies": [], "strategy_json": None}
        # 连发中断：受理即登记"该用户最新轮次"，仍在跑的旧轮次会在下一个
        # 步骤边界发现落后并自行退出（见 agent/react_agent.py 的 _EPOCHS）。
        if parsed["epoch"] is not None:
            react_agent.note_epoch(parsed["user_id"], parsed["epoch"])
        result = await asyncio.to_thread(
            react_agent.run_agent, parsed["user_id"], parsed["message"],
            **{k: parsed[k] for k in ("history", "session_id", "memory_user_id",
                                      "tool_mode", "scopes", "role", "focus", "epoch",
                                      "completion", "completion_stream")}
        )
        return result
    except Exception as e:
        logger.error(f"agent chat error: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"], "strategy_json": None}


# 流式端点的在途 worker 任务（仅为攥住引用防 GC，见 agent_chat_stream）
_STREAM_TASKS: set = set()


@app.post("/api/v1/agent/chat/stream")
async def agent_chat_stream(req: Request):
    """agent_chat 的流式版（2026-10-20，前端流式输出）。

    请求体与非流式版完全一致（含 epoch 连发中断契约）；响应是 text/event-stream，
    每个事件一行：data: {"type": "...", ...}，按序三种：

    - {"type":"delta","text":"..."}      文本增量（边生成边推，用于前端打字机效果）
    - {"type":"result","result":{...}}   最终结果，形状与非流式版返回值相同（权威）
    - {"type":"error","error":"..."}     worker 异常（调用方按失败兜底）

    Java 侧以 result 事件落库，delta 只做展示；连发取消时 Java 断开本流，
    worker 里的 run_agent 靠 epoch 在下一步骤边界自行退出。
    """
    from fastapi.responses import StreamingResponse

    try:
        import agent.react_agent as react_agent
        data = await req.json()
        parsed = _parse_agent_chat_body(data)
    except Exception as e:
        logger.error(f"agent chat stream body error: {e}", exc_info=True)
        parsed = None

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    def _emit(evt):
        # worker 跑在 to_thread 的线程里：跨线程投递必须走 call_soon_threadsafe
        loop.call_soon_threadsafe(queue.put_nowait, evt)

    def _work():
        try:
            if parsed is None:
                _emit({"type": "result", "result": {"replies": [], "strategy_json": None}})
                return
            if parsed["epoch"] is not None:
                react_agent.note_epoch(parsed["user_id"], parsed["epoch"])
            result = react_agent.run_agent(
                parsed["user_id"], parsed["message"],
                **{k: parsed[k] for k in ("history", "session_id", "memory_user_id",
                                          "tool_mode", "scopes", "role", "focus", "epoch",
                                          "completion", "completion_stream")},
                on_delta=lambda text: _emit({"type": "delta", "text": text}),
            )
            _emit({"type": "result", "result": result})
        except Exception as exc:  # noqa: BLE001
            logger.error(f"agent chat stream worker error: {exc}", exc_info=True)
            _emit({"type": "error", "error": str(exc)})
        finally:
            _emit(_SENTINEL)

    # 持有任务引用：事件循环对 task 只存弱引用，不攥住会被 GC 半路收走（官方文档明示）
    _stream_tasks = _STREAM_TASKS
    task = asyncio.create_task(asyncio.to_thread(_work))
    _stream_tasks.add(task)
    task.add_done_callback(_stream_tasks.discard)

    async def event_gen():
        import json as _json
        while True:
            evt = await queue.get()
            if evt is _SENTINEL:
                break
            yield f"data: {_json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


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
        # BYOK：巩固记的是这个用户的账，用用户自己的 Key（没配就降级跳过这次摘要）
        completion, _ = _llm_callables(data, user_id)
        return await asyncio.to_thread(consolidate.consolidate_session, user_id, session_key,
                                       completion=completion)
    except Exception as e:
        logger.error(f"memory consolidate error: {e}", exc_info=True)
        return {"error": "会话总结生成失败，请稍后重试"}


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


@app.post("/api/v1/strategies/validate-config")
async def validate_strategy_config_endpoint(req: Request):
    """轻量校验：只验 DSL 结构（不取数、不回测）。

    策略编辑面板保存前由 Java 调用 —— 把"改坏了的规则"挡在落库之前，
    而不是等下一次结算在痕迹里炸出一串 unknown/skip。
    """
    from agent.strategy_schema import validate_strategy_config
    try:
        data = await req.json()
        cfg, err = validate_strategy_config(data.get("strategy_json", {}))
        if err:
            return {"valid": False, "error": _friendly_rule_error(err)}
        return {"valid": True}
    except Exception as e:
        logger.error(f"strategy validate-config error: {e}", exc_info=True)
        return {"valid": False, "error": "校验服务异常，请稍后重试"}


def _friendly_rule_error(err: str) -> str:
    """pydantic 的报错第一行是英文元信息（"1 validation error for StrategyConfig"），
    人话在 "Value error, ..." 那行 —— 编辑面板要给用户看的是后者。找不到就退回第一行。"""
    for line in str(err).splitlines():
        stripped = line.strip()
        if stripped.startswith("Value error,"):
            message = stripped[len("Value error,"):].strip()
            # pydantic 会把 [type=..., input_value=...] 元数据追加在句尾 —— 那是给
            # 开发者看的，用户只需要那句中文。截掉它。
            bracket = message.find(" [type=")
            return message[:bracket].strip() if bracket > 0 else message
    return str(err).splitlines()[0] if str(err) else "规则结构无法识别"


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


# 一次矩阵回测最多取多少只标的：这是**样本外验证**，不是全市场扫描。
# 上限定得小是刻意的：既是取数成本，也是"别用参数搜索替代思考"的纪律。
MAX_MATRIX_SYMBOLS = 12


@app.post("/api/v1/strategies/backtest-matrix")
async def backtest_matrix_endpoint(req: Request):
    """多标的 × 多时段的样本外验证 + 成本归因（**确定性，不调模型**）。

    为什么需要它：单个标的、单段历史的那个回测数字，既可能是运气，也可能是换手磨出来的。
    这张表回答的是"这条规则换一只票、换一段行情，还成不成立"，以及
    "亏损里有多少是摩擦吃掉的"。请求体：

        {"strategy_json": {...}, "symbols": ["600519", "000001"], "segments": 3,
         "start_date": "2025-01-01", "end_date": "2025-12-31", "period": "day"}
    """
    from agent.strategy_engine import evaluate_strategy_matrix
    from agent.strategy_schema import validate_strategy_config
    from akshare_client import ADJUST_MODE, get_history
    try:
        data = await req.json()
        cfg, err = validate_strategy_config(data.get("strategy_json", {}))
        if err:
            return {"valid": False, "error": err}
        symbols = [str(item).strip() for item in (data.get("symbols") or []) if str(item).strip()]
        if not symbols and cfg.symbol:
            symbols = [cfg.symbol]
        if not symbols:
            return {"valid": False, "error": "symbols 不能为空"}
        segments = max(1, min(int(data.get("segments") or 3), 12))
        start_date = str(data.get("start_date") or "").strip()
        end_date = str(data.get("end_date") or "").strip()
        period = str(data.get("period") or "day")

        def collect():
            return {symbol: get_history(symbol, start_date, end_date, period)
                    for symbol in symbols[:MAX_MATRIX_SYMBOLS]}

        records_by_symbol = await asyncio.to_thread(collect)
        result = await asyncio.to_thread(
            evaluate_strategy_matrix, cfg.model_dump(), records_by_symbol,
            segments=segments, start_date=start_date, end_date=end_date,
            adjust_mode=ADJUST_MODE)
        result["valid"] = True
        result["strategy_name"] = cfg.name
        result["requested_symbols"] = symbols
        return result
    except Exception as e:
        logger.error(f"strategy backtest-matrix error: {e}", exc_info=True)
        return {"valid": False, "error": "矩阵回测失败，请稍后重试"}


@app.post("/api/v1/strategies/evaluate-bar")
async def evaluate_bar_endpoint(req: Request):
    """评估某一天的信号。响应里带**执行口径**（fill_basis / fingerprint）与证据快照。

    `settlement_kind` 由调用方声明（"daily" 或 "realtime"）——它决定成交价口径，
    而口径必须随结果一起落库。缺省时不猜：契约会归一成 `unknown_*`，
    让"口径不明"这条记录自己显形（`agent/execution_contract.py`）。
    """
    from agent import execution_contract as ec
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import evaluate_bar
    from akshare_client import ADJUST_MODE, get_history
    from akshare_client import get_minute_kline
    settlement_kind = None
    try:
        data = await req.json()
        settlement_kind = str(data.get("settlement_kind") or "").strip() or None
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
            # 无数据也要带口径：否则调用方只能猜这条记录属于哪个口径，或者干脆不记
            # error_detail：机器可读的失败细节（写法沿用盯盘端点把异常类名放进
            # reason 的先例）——Java 会把它原样落进痕迹快照，排查不用再翻两边日志。
            return {"error": "历史 K 线数据不足，暂时无法评估", "signal": "hold", "matched_conditions": [],
                    "decision": ec.DECISION_SKIP, "skip_reason": ec.SKIP_DATA_UNAVAILABLE,
                    "error_detail": {"type": "history_empty", "symbol": symbol},
                    **ec.no_bar_facts(settlement_kind, ADJUST_MODE)}
        bar_time = str(records[-1].get("date", ""))
        position = data.get("position")
        if isinstance(position, dict) and "quantity" in position and "shares" not in position:
            position = dict(position)
            position["shares"] = position["quantity"]
        result = await asyncio.to_thread(
            evaluate_bar, cfg.model_dump(), records, data.get("date", "") or bar_time, position,
            settlement_kind, ADJUST_MODE
        )
        result["bar_time"] = bar_time
        # 盘中路径附带"盯盘事件"（纯代码检测，零 LLM 成本）：结算侧拿到非空 events
        # 才会去调盯盘交易员（/agent/intraday-decide）。规则信号在这里降级为一条事件
        # （rule_signal）—— agent 模式下"要不要按规则动"由盯盘 agent 判断。
        if settlement_kind == ec.SETTLEMENT_REALTIME:
            from agent import intraday_watch
            day_records = await asyncio.to_thread(get_history, symbol)
            events = intraday_watch.detect_events(
                records, day_records or [], position, cfg.model_dump(),
                signal=str(result.get("signal") or "").lower() or None)
            result["events"] = events
        return result
    except Exception as e:
        logger.error(f"strategy evaluate-bar error: {e}", exc_info=True)
        return {"error": "行情评估失败，请稍后重试", "signal": "hold", "matched_conditions": [],
                "decision": ec.DECISION_SKIP, "skip_reason": ec.SKIP_DATA_UNAVAILABLE,
                "error_detail": {"type": type(e).__name__, "message": str(e)[:200]},
                **ec.no_bar_facts(settlement_kind, ADJUST_MODE)}


@app.post("/api/v1/agent/decide")
async def agent_decide_endpoint(req: Request):
    """**agent 决策**：多角色委员会辩论后给出决策（与 `evaluate-bar` 同形状）。

    <h3>为什么响应形状与 evaluate-bar 完全一致</h3>
    结算侧（Java）只需要换一个调用地址，**执行路径一行不改**：
    整手取整、5 元佣金下限、印花税、T+1、涨跌停挡单、DECIMAL 记账全部照旧。
    **模型决定要不要动，代码决定怎么动** —— 这是这次引入 agent 时唯一不能让步的一条。

    <h3>取数也是按 as-of 截断的</h3>
    取历史时就带上 `end_date = date`（而不是取回全部再在内存里裁）：
    两道防线比一道强，而且**源头就不给未来的数据**，比"拿到了再丢掉"更难出错。

    请求体：
        {"strategy_json": {...} | "symbol": "600519", "date": "2026-03-10",
         "settlement_kind": "daily", "position": {...}, "last_decision_at": "2026-03-06"}

    <h3>策略 DSL 的双重角色</h3>
    `strategy_json` 的 entry/exit 条件**不约束决策**（决策是裁量制的），
    但参与两处：①"召集门控"的一条触发理由（规则给出买/卖 = 值得开会）；
    ②以 `strategy_intent` 进证据包（委员会知道自己在替哪套规则做裁量，偏离要给理由）。
    这是"把决策源从规则换成 agent"的落点：同一张策略记录，换的是**谁做决定**，
    而 `fingerprint.decision_mode` 会把这个区别写进每一条痕迹。

    <h3>财务证据（fundamentals，fail-open）</h3>
    本端点自己取最近报告期的财务摘要（数据源在 Python 侧，Java 不用改）：
    24 小时 TTL 缓存（财报按季度披露，不需要每次开会都打数据源），
    `AGENT_FUNDAMENTALS=0` 可整体关闭。取不到就不带这块，委员会照旧只看行情。
    """

    # ==================== 财务摘要（fundamentals，带 TTL 缓存） ====================
    # 财报按季度披露：24h 缓存足够新鲜，又避免每次开会都打东方财富。
    # 失败/关闭返回 None（Python 侧 fail-open，缺席不是错误）。
    _FUNDAMENTALS_TTL_SECONDS = 24 * 3600
    if not hasattr(agent_decide_endpoint, "_fundamentals_cache"):
        agent_decide_endpoint._fundamentals_cache = {}

    def _fundamentals_quietly(symbol: str):
        if os.getenv("AGENT_FUNDAMENTALS", "1").strip().lower() in ("0", "off", "false"):
            return None
        now = time.time()
        cached = agent_decide_endpoint._fundamentals_cache.get(symbol)
        if cached and now - cached[0] < _FUNDAMENTALS_TTL_SECONDS:
            return cached[1]
        try:
            from akshare_client import get_financial_abstract
            data = get_financial_abstract(symbol, periods=2)
        except Exception as e:  # noqa: BLE001 —— 财务取不到不该挡住决策
            logger.info("委员会财务摘要取数失败 symbol=%s（不带 fundamentals）：%s",
                        symbol, type(e).__name__)
            data = None
        agent_decide_endpoint._fundamentals_cache[symbol] = (now, data)
        return data

    from agent import execution_contract as ec
    from agent import market_context as mc
    from agent import trading_committee
    from agent.strategy_schema import validate_strategy_config
    from akshare_client import ADJUST_MODE, get_history

    async def _market_context_args(symbol: str) -> dict:
        """市场语境三块的取数（fail-open：坏一块少一块，全坏则三块都不带）。"""
        if os.getenv("AGENT_MARKET_CONTEXT", "1").strip().lower() in ("0", "off", "false"):
            return {}
        market = await asyncio.to_thread(mc.get_market_context)
        sector = await asyncio.to_thread(mc.get_sector_context, symbol)
        valuation = await asyncio.to_thread(mc.get_valuation_context, symbol)
        return {"market_context": market, "sector_context": sector,
                "valuation_context": valuation}

    settlement_kind = None
    try:
        data = await req.json()
        settlement_kind = str(data.get("settlement_kind") or "").strip() or None
        strategy_json = data.get("strategy_json") or {}
        cfg, err = validate_strategy_config(strategy_json)
        symbol = str(data.get("symbol") or (cfg.symbol if cfg else "") or "").strip()
        if err and not symbol:
            return {"valid": False, "error": err}
        if not symbol:
            return {"valid": False, "error": "symbol 不能为空"}
        date = str(data.get("date") or "").strip()
        if not date:
            return {"valid": False, "error": "date 不能为空（agent 决策必须有决策日）"}

        # as-of 第一道防线：源头就不取决策日之后的数据
        records = await asyncio.to_thread(get_history, symbol, "", date, "day")
        position = data.get("position")
        if isinstance(position, dict) and "quantity" in position and "shares" not in position:
            position = dict(position)
            position["shares"] = position["quantity"]

        # 技能注入（④步）：按 (用户, committee 场景, 标的, 策略) 拉软参考技能
        skill_items = []
        user_id = data.get("user_id")
        if user_id:
            try:
                from agent import skill_store
                skill_items = skill_store.injectable(
                    str(user_id), "committee", symbol=symbol,
                    strategy_id=data.get("strategy_id"))
            except Exception:  # noqa: BLE001 —— 技能缺席不挡会议
                skill_items = []
        # 复盘教训注入（2026-10-18）：周反思产出的教训流回决策现场（软参考，
        # fail-open）。与 skills 的信任级别不同：教训未经用户逐条确认。
        lesson_items = []
        if user_id:
            try:
                from agent import trading_lessons
                lesson_items = trading_lessons.injectable(
                    str(user_id), "committee", symbol=symbol,
                    strategy_id=data.get("strategy_id"))
            except Exception:  # noqa: BLE001 —— 教训缺席不挡会议
                lesson_items = []
        result = await asyncio.to_thread(
            trading_committee.decide, symbol, date, records or [], position,
            settlement_kind=settlement_kind or ec.SETTLEMENT_DAILY, adjust_mode=ADJUST_MODE,
            # BYOK：委员会 8 棒串行是用户级任务里最贵的一档（~21k tokens/次），
            # 必须花用户自己的 Key——没配 Key 时 _is_available 判不可用，
            # decide 内部门控会退化成"不开会"，Java 侧按既有降级路径处理
            completion=_llm_callables(data, user_id)[0],
            # 策略 JSON 只给门控用（"规则信号"触发理由复用规则引擎）；
            # `last_decision_at` 决定"太久没看"这条 —— 由 Java 从痕迹里取上次**真的开了会**的日子，
            # 而不是"上次结算的日子"（后者每天都有，定时器永远不会到期）。
            config=cfg.model_dump() if cfg else None,
            last_decision_at=data.get("last_decision_at"),
            # 信息面证据（P3）：Java 从新闻库组装，非空才带（fail-open）
            news_briefing=data.get("news_briefing") if isinstance(data.get("news_briefing"), dict) else None,
            # 经验回流（P4）：这条策略自己的成交成绩单，非空才带（fail-open）
            experience=data.get("experience") if isinstance(data.get("experience"), dict) else None,
            # 组合上下文（P5）：本策略 + 该用户全部在跑策略的敞口，非空才带（fail-open）
            portfolio=data.get("portfolio") if isinstance(data.get("portfolio"), dict) else None,
            # 财务证据：本端点自取（TTL 缓存），失败/关闭为 None（fail-open）
            fundamentals=await asyncio.to_thread(_fundamentals_quietly, symbol),
            # 市场语境三块（大盘/板块/估值）：agent/market_context.py 自取，
            # AGENT_MARKET_CONTEXT=0 可整体关闭；任一块失败缺席不挡决策
            **(await _market_context_args(symbol)),
            skills=skill_items or None,
            # 复盘教训（软参考）：教训库检索非空才带（fail-open）
            lessons=lesson_items or None,
        )
        result["valid"] = True
        return result
    except Exception as e:
        logger.error(f"agent decide error: {e}", exc_info=True)
        from agent import execution_contract as ec
        from akshare_client import ADJUST_MODE
        # skip_reason 保持 data_unavailable：Java 补跑轮次只认 no_bar/data_unavailable，
        # 换成别的值瞬时故障（网络/LLM）就永远不被重试。真因走 error_detail 进
        # 痕迹快照（Java errorSnapshot 消费），代码错误不再伪装成纯数据问题。
        return {"valid": False, "error": "agent 决策失败，请稍后重试",
                "error_detail": {"type": type(e).__name__, "message": str(e)[:200]},
                "decision": ec.DECISION_SKIP, "skip_reason": ec.SKIP_DATA_UNAVAILABLE,
                "signal": None,
                **ec.no_bar_facts(settlement_kind, ADJUST_MODE, ec.DECISION_MODE_AGENT)}


@app.post("/api/v1/agent/intraday-decide")
async def intraday_decide_endpoint(req: Request):
    """**盘中盯盘交易员**：事件触发的一次性判断（1 次 LLM 调用，秒级）。

    与委员会的分工：委员会（/agent/decide）只在日线结算时开会、划授权边界；
    本端点在盘中的量化事件（急涨急跌/量能异常/穿越均线/逼近止损线/规则信号……）
    发生时被结算侧调用，在授权边界内给出 hold/buy/sell/partial_sell/flag。

    请求体：
        {"strategy_json": {...}, "symbol": "601988", "events": [{"type","detail"}],
         "position": {"shares","entry_price","high_watermark"},          # 可空
         "authorization": {"intraday_execution","allow_open","allow_close",
                            "max_position_pct","notes"}}                  # 可空

    面板（价格/量比/均线/持仓盈亏）由本端点自己取数计算 —— 调用方只透传事件，
    不需要在两次请求之间维护面板状态。
    """
    from agent import intraday_watch
    from akshare_client import get_history
    from akshare_client import get_minute_kline
    try:
        data = await req.json()
        symbol = str(data.get("symbol") or "").strip()
        if not symbol:
            return {"valid": False, "error": "symbol 不能为空"}
        events = data.get("events")
        if not isinstance(events, list) or not events:
            return {"valid": False, "error": "events 不能为空（无事件不该调盯盘）"}
        position = data.get("position")
        if isinstance(position, dict) and "quantity" in position and "shares" not in position:
            position = dict(position)
            position["shares"] = position["quantity"]

        config = data.get("strategy_json") or {}
        minute_bars = await asyncio.to_thread(get_minute_kline, symbol, 1)
        day_records = await asyncio.to_thread(get_history, symbol)
        # news_shock 详情（P3）：Java 检测到 high impact 新闻时随请求带来，None = 与新闻无关
        news_alert = data.get("news_alert") if isinstance(data.get("news_alert"), dict) else None
        # 经验回流（P4）：这条策略自己的成交成绩单（轻量版），None = 不带
        experience = data.get("experience") if isinstance(data.get("experience"), dict) else None
        # 盘中语境（2026-10-14）：当日已成交 + 上次全委会结论（Java 组装，fail-open）
        today_context = data.get("today_context") if isinstance(data.get("today_context"), dict) else None
        # 大盘语境（2026-10-14）：与委员会共用 market_context 的 TTL 缓存（全市场一份，
        # 近零成本）；AGENT_MARKET_CONTEXT=0 时与委员会一起关闭，fail-open 不挡盯盘
        market_context = None
        if os.getenv("AGENT_MARKET_CONTEXT", "1").strip().lower() not in ("0", "off", "false"):
            try:
                from agent import market_context as _mc
                market_context = await asyncio.to_thread(_mc.get_market_context)
            except Exception as e:  # noqa: BLE001 —— 大盘语境取不到不该挡盯盘
                logger.info("盯盘大盘语境取数失败（不带该块）：%s", type(e).__name__)
        # 复盘教训（2026-10-18）：请求带 user_id 才检索（Java 侧随请求透传，缺省不带）。
        # fail-open：检索失败/没有 user_id 都不影响盯盘。
        lesson_items = []
        if data.get("user_id"):
            try:
                from agent import trading_lessons
                lesson_items = await asyncio.to_thread(
                    trading_lessons.injectable, str(data.get("user_id")), "watch",
                    symbol=symbol)
            except Exception:  # noqa: BLE001 —— 教训缺席不挡盯盘
                lesson_items = []
        result = await asyncio.to_thread(
            intraday_watch.intraday_decide, symbol, events, position,
            data.get("authorization"), config, minute_bars or [], day_records or [],
            completion=_llm_callables(data, data.get("user_id"))[0],
            news_alert=news_alert, experience=experience,
            today_context=today_context, market_context=market_context,
            lessons=lesson_items or None)
        result["valid"] = True
        return result
    except Exception as e:
        logger.error(f"intraday decide error: {e}", exc_info=True)
        # 端点级异常按"没问成模型"处理：degraded=true，结算侧回落规则+授权路径
        return {"valid": False, "degraded": True, "action": "hold", "size_fraction": 0.0,
                "reason": f"盯盘端点异常：{type(e).__name__}", "watch_tokens": 0}


# ==================== 周反思（P2 经验回流闭环，2026-10-12） ====================
# 设计参考 TradingAgents（Apache-2.0）的 memory log：决策落日志 → 持有期到 → 结算
# 实际收益并生成一段反思 → 下次决策回读。我们的差异：定量半环（5 日对账的成绩单）
# 已经是确定性代码，LLM 只负责定性半环（"为什么错"），且产物是结构化教训、
# 由 Java 落进教训库（MemoryLesson），**人不审核也只影响提示词权重、不影响账**。

WEEKLY_REFLECTION_MAX = 3

_WEEKLY_REFLECTION_SYSTEM = (
    "你是这条策略的周复盘官。输入是本周的客观材料：定量战绩（每笔成交 N 个交易日后"
    "回看的方向对错，样本可能很少）、成交记录、委员会决策过程（角色发言摘录），"
    "以及 prior_lessons——这条策略现存的旧教训（带 id）。\n"
    f"请提炼**最多 {WEEKLY_REFLECTION_MAX} 条**本周教训。\n"
    "要求：symptom 一句话点出重复出现的模式（犯错或做对都算）；reusable_rule 是一句"
    "**可执行、可验证**的话（例如「首仓不超过现金 50%，等回踩确认再加仓」）——"
    "不是投资结论，不预测行情；context 用两三句给出依据（引用战绩/成交/辩论里的具体"
    "事实，不许编数字）；resolution 写这条教训打算怎么落实。没有足够事实支撑就少写"
    "或不写 —— 宁缺毋滥。\n"
    "质疑旧教训（可选，同样宁缺毋滥）：仅当本周材料与 prior_lessons 里的某条旧教训"
    "**明确矛盾**时，才在 revisions（改写该条，只给要改的字段）或 retirements"
    "（该条已被证伪，建议停用）里提出处置；lesson_id 只能填 prior_lessons 里出现过的"
    "id，编造的 id 会被直接丢弃；reason 引用具体事实说明为什么。证据不充分就保持"
    "沉默，不要为了输出而质疑。\n"
    "只输出一个 JSON 对象，不要任何其他文字：\n"
    '{"lessons": [{"symptom": "...", "context": "...", "resolution": "...", '
    '"reusable_rule": "...", "confidence": 0.0-1.0}], '
    '"revisions": [{"lesson_id": 0, "reason": "...", "symptom": "可选", '
    '"context": "可选", "resolution": "可选", "reusable_rule": "可选", '
    '"confidence": 0.0-1.0}], '
    '"retirements": [{"lesson_id": 0, "reason": "..."}]}'
)


def _extract_json_object(text: str):
    """从模型输出里提取第一个 JSON 对象（容忍代码围栏/前后散文）；读不懂返回 None。"""
    import json as _json
    import re as _re

    match = _re.search(r"\{.*\}", str(text or ""), _re.DOTALL)
    if not match:
        return None
    try:
        data = _json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _parse_weekly_lessons(text: str) -> list[dict]:
    """确定性解析（保守优先，与 trading_decision 同一条纪律）：读不懂就当没有教训。"""
    data = _extract_json_object(text)
    raw = data.get("lessons") if data else None
    if not isinstance(raw, list):
        return []
    lessons: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        symptom = str(item.get("symptom") or "").strip()
        rule = str(item.get("reusable_rule") or "").strip()
        if not symptom or not rule:
            continue  # 缺 symptom + reusable_rule 的是散文，不是教训
        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = None
        lessons.append({
            "symptom": symptom[:200],
            "context": str(item.get("context") or "").strip()[:600] or None,
            "resolution": str(item.get("resolution") or "").strip()[:600] or None,
            "reusable_rule": rule[:300],
            "confidence": max(0.0, min(1.0, confidence)) if confidence is not None else None,
        })
        if len(lessons) >= WEEKLY_REFLECTION_MAX:
            break
    return lessons


def _parse_weekly_dispositions(text: str) -> dict:
    """解析周反思对旧教训的处置指令（方案一质疑回路，2026-10-16）。

    保守优先，与 _parse_weekly_lessons 同一条纪律：
    - lesson_id 必须是正整数（bool 不算）——非整数/编造不出具体行的条目直接丢弃，
      Java 侧还有一层"只认本次喂入 prior_lessons 集合"的白名单，两层闸门各管一段；
    - revisions 里一个字段都没改的"空修订"是噪声，不进结果；
    - 读不懂就当没有任何处置。
    """
    data = _extract_json_object(text)
    revisions: list[dict] = []
    retirements: list[dict] = []
    if data is None:
        return {"revisions": revisions, "retirements": retirements}

    def _lesson_id(item) -> int | None:
        value = item.get("lesson_id")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            return None
        return value

    for item in data.get("revisions") or []:
        if not isinstance(item, dict):
            continue
        lesson_id = _lesson_id(item)
        if lesson_id is None:
            continue
        patch = {"lesson_id": lesson_id,
                 "reason": str(item.get("reason") or "").strip()[:300] or None}
        for key, cap in (("symptom", 200), ("context", 600),
                         ("resolution", 600), ("reusable_rule", 300)):
            value = str(item.get(key) or "").strip()
            if value:
                patch[key] = value[:cap]
        confidence = item.get("confidence")
        if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
            patch["confidence"] = max(0.0, min(1.0, float(confidence)))
        if any(key in patch for key in ("symptom", "context", "resolution",
                                        "reusable_rule", "confidence")):
            revisions.append(patch)
        if len(revisions) >= WEEKLY_REFLECTION_MAX:
            break
    for item in data.get("retirements") or []:
        if not isinstance(item, dict):
            continue
        lesson_id = _lesson_id(item)
        if lesson_id is None:
            continue
        retirements.append({"lesson_id": lesson_id,
                            "reason": str(item.get("reason") or "").strip()[:300] or None})
        if len(retirements) >= WEEKLY_REFLECTION_MAX:
            break
    return {"revisions": revisions, "retirements": retirements}


def weekly_reflection_run(payload: dict, completion=None) -> dict:
    """一次周反思（**1 次** LLM 调用）。永不抛异常：任何故障退化成"没有教训"。"""
    import json as _json

    import llm_service

    if not isinstance(payload, dict) or not isinstance(payload.get("strategy"), dict):
        return {"ok": False, "lessons": [], "warnings": ["payload 缺少 strategy 字段"]}
    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {"ok": False, "lessons": [], "warnings": ["模型服务不可用"]}
        except Exception:  # noqa: BLE001 —— 探测失败不应阻止尝试
            pass
    call = completion or llm_service.chat_completion
    try:
        choice = call(
            [{"role": "system", "content": _WEEKLY_REFLECTION_SYSTEM},
             {"role": "user", "content": _json.dumps(payload, ensure_ascii=False, default=str)}],
            temperature=0.2, max_tokens=1100)
        text = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "lessons": [], "warnings": [f"反思调用失败（{type(exc).__name__}）"]}
    if "AI 服务暂不可用" in text or "AI 服务未配置" in text:
        reason = "用户未绑定自己的 API Key（个人中心可配置）" if "AI 服务未配置" in text \
            else "模型服务占位响应"
        return {"ok": False, "lessons": [], "warnings": [reason]}
    warnings: list[str] = [] if text.strip() else ["模型返回空内容"]
    result = {"ok": True, "lessons": _parse_weekly_lessons(text), "warnings": warnings}
    # 质疑回路（方案一）：旧教训的处置指令一并带回，Java 侧过 id 白名单后落库
    result.update(_parse_weekly_dispositions(text))
    return result


@app.post("/api/v1/agent/weekly-reflection")
async def weekly_reflection_endpoint(req: Request):
    """**周反思**（P2，经验回流的定性半环）：本周战绩/成交/委员会过程 → ≤3 条结构化教训。

    请求体（Java PaperTradingService 在周五复盘时组装）：
        {"strategy": {...}, "experience": {...}?, "trades": [...]?,
         "committee": [...]?, "prior_lessons": [{id, symptom, ...}]?}

    - 委员会记录缺一周不开会就没有原料 —— Java 侧门控在先，本端点不再判断；
    - 产物是结构化教训（symptom/context/resolution/reusable_rule/confidence），
      Java 落 MemoryLesson（taskType=weekly_reflection），随经验包回读给委员会；
    - prior_lessons 是本策略现存旧教训（2026-10-16 质疑回路）：模型对照本周材料
      可输出 revisions/retirements 处置意见带回 Java，由 Java 过 id 白名单后落库
      （本服务仍然没有任何教训写接口——防投毒：处置只产出，不落地）；
    - fail-open：任何失败返回 ok=false + 人话 warning，绝不抛异常。
    """
    try:
        data = await req.json()
        payload = data if isinstance(data, dict) else {}
        # BYOK：周反思记的是这个用户的教训，Key 也用他自己的（没配就"没有教训"降级）
        completion, _ = _llm_callables(payload, payload.get("user_id"))
        result = await asyncio.to_thread(weekly_reflection_run, payload, completion)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"weekly reflection error: {e}", exc_info=True)
        return {"ok": False, "lessons": [], "warnings": ["周反思端点异常"]}


# ==================== 单笔大亏即时反思（2026-10-18 二轮，mini-reflection） ====================
# 与周反思的分工：周反思每周五一次、面向"模式"；本端点在一笔大亏了结的当天结算后
# 立即触发、面向"这一笔"——教训到位时间从"最迟下周五"压到"当天收盘后"，
# 下一交易日的委员会就能读到。触发阈值与大亏判定在 Java 侧（确定性代码），
# 本端点只负责定性半环；产物 ≤1 条（单笔材料只配得上一条，宁缺毋滥）。

_TRADE_REFLECTION_MAX = 1

_TRADE_REFLECTION_SYSTEM = (
    "你是这条策略的单笔复盘官。输入是一笔**已了结且亏损较深**的卖出：成交明细、"
    "往返路径口径（maePct/mfePct = 持仓期间相对 FIFO 成本的最大不利/有利偏移%，"
    "exitEarly = 曾有明显浮盈但落袋不足一半）、这条策略的定量战绩（experience）、"
    "以及当次/最近的委员会决策过程摘录（committee）。\n"
    f"请提炼**最多 {_TRADE_REFLECTION_MAX} 条**教训，聚焦这一笔暴露出的**可执行**问题"
    "（例如「浮盈 mfePct 远大于落袋 → 应设跟踪止盈」「入场即遭 deepAdverse → 首仓过重，"
    "应等回踩确认」）。symptom 一句话点名这笔的具体病理；reusable_rule 是一句可执行、"
    "可验证的话，不预测行情；context 引用给定的具体数字（不许编）；confidence 如实给。"
    "材料撑不起一条像样的教训就不输出——**宁缺毋滥，单笔材料只配一条**。\n"
    "只输出一个 JSON 对象，不要任何其他文字：\n"
    '{"lessons": [{"symptom": "...", "context": "...", "resolution": "...", '
    '"reusable_rule": "...", "confidence": 0.0-1.0}]}'
)


def trade_reflection_run(payload: dict, completion=None) -> dict:
    """一次单笔反思（**1 次** LLM 调用）。永不抛异常：任何故障退化成"没有教训"。

    与 weekly_reflection_run 同一条纪律（解析保守优先、结构化产物、无写接口）；
    差异只有两点：提示词面向单笔病理、产物封顶 1 条。
    """
    import json as _json

    import llm_service

    if not isinstance(payload, dict) or not isinstance(payload.get("strategy"), dict):
        return {"ok": False, "lessons": [], "warnings": ["payload 缺少 strategy 字段"]}
    if not isinstance(payload.get("trade"), dict):
        return {"ok": False, "lessons": [], "warnings": ["payload 缺少 trade 字段"]}
    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {"ok": False, "lessons": [], "warnings": ["模型服务不可用"]}
        except Exception:  # noqa: BLE001 —— 探测失败不应阻止尝试
            pass
    call = completion or llm_service.chat_completion
    try:
        choice = call(
            [{"role": "system", "content": _TRADE_REFLECTION_SYSTEM},
             {"role": "user", "content": _json.dumps(payload, ensure_ascii=False, default=str)}],
            temperature=0.2, max_tokens=700)
        text = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "lessons": [], "warnings": [f"反思调用失败（{type(exc).__name__}）"]}
    if "AI 服务暂不可用" in text or "AI 服务未配置" in text:
        reason = "用户未绑定自己的 API Key（个人中心可配置）" if "AI 服务未配置" in text \
            else "模型服务占位响应"
        return {"ok": False, "lessons": [], "warnings": [reason]}
    warnings: list[str] = [] if text.strip() else ["模型返回空内容"]
    lessons = _parse_weekly_lessons(text)[:_TRADE_REFLECTION_MAX]
    return {"ok": True, "lessons": lessons, "warnings": warnings}


@app.post("/api/v1/agent/trade-reflection")
async def trade_reflection_endpoint(req: Request):
    """**单笔大亏即时反思**：一笔深亏了结的当天结算后触发 → ≤1 条结构化教训。

    请求体（Java PaperTradingService 在日线结算落库后组装，fail-open 旁路）：
        {"strategy": {...}, "trade": {...含 roundTrip 的 maePct/mfePct/roundTripPct},
         "experience": {...}?, "committee": [...]?}

    触发阈值（亏损多深才反思、去重）全在 Java 侧的确定性代码里；本端点与
    weekly-reflection 同一条纪律：结构化产物、解析保守优先、无教训写接口。
    """
    try:
        data = await req.json()
        data = await req.json()
        payload = data if isinstance(data, dict) else {}
        # BYOK：单笔反思记的是这个用户的教训，Key 也用他自己的
        completion, _ = _llm_callables(payload, payload.get("user_id"))
        result = await asyncio.to_thread(trade_reflection_run, payload, completion)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"trade reflection error: {e}", exc_info=True)
        return {"ok": False, "lessons": [], "warnings": ["单笔反思端点异常"]}


# ==================== 季度策略信（2026-10-15）====================
# 与周反思的分工：周反思面向**委员会**（教训回流提示词，结构化、宁缺毋滥）；
# 季度信面向**用户**（一封可读的复盘信：逻辑还成立吗/参数还合适吗/和基准比怎样）。
# 数字底座全部由 Java 确定性算好（报告纪律：判断依据不能每次生成都不一样），
# LLM 只负责"评语"半环 —— 且评语里的参数建议是**提议**，永不自动改配置。

QUARTERLY_LETTER_MAX_CHARS = 900

_QUARTERLY_REVIEW_SYSTEM = (
    "你是这条模拟盘策略的季度复盘官，正在给策略的主人写一封复盘信的「评语」部分。"
    "输入是一个季度的客观材料：季度战绩（收益/回撤/胜率/空仓占比/对买入持有的超额）、"
    "季度成交明细、教训库现状（含本季自动退役的教训）、样本外验证结论。\n"
    "请回答三件事：① 策略的逻辑这个季度还成立吗（用事实说话，不许编数字）；"
    "② 参数/风控有没有值得调整的**提议**（明确说「提议、需主人确认」，你不是执行者）；"
    "③ 和买入持有比，这套操作值不值这个摩擦。\n"
    "要求：直接称呼「你」；引用材料里的具体数字做依据；样本不足就直说样本不足；"
    "不超过 300 字；不预测行情、不给投资建议、不用列表堆砌 —— 写成几段自然的话。"
    "只输出 JSON：{\"verdict\": \"一段 2-3 句的总评\", \"suggestions\": \"参数/风控的提议，"
    "没有就说暂无\"}"
)


def quarterly_review_run(payload: dict, completion=None) -> dict:
    """一封季度信的评语半环（**1 次** LLM 调用）。永不抛异常：失败退化成「未生成评语」。"""
    import json as _json

    import llm_service

    if not isinstance(payload, dict) or not isinstance(payload.get("strategy"), dict):
        return {"ok": False, "letter": None, "warnings": ["payload 缺少 strategy 字段"]}
    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {"ok": False, "letter": None, "warnings": ["模型服务不可用"]}
        except Exception:  # noqa: BLE001 —— 探测失败不应阻止尝试
            pass
    call = completion or llm_service.chat_completion
    try:
        choice = call(
            [{"role": "system", "content": _QUARTERLY_REVIEW_SYSTEM},
             {"role": "user", "content": _json.dumps(payload, ensure_ascii=False, default=str)}],
            temperature=0.3, max_tokens=600)
        text = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "letter": None, "warnings": [f"季度评语调用失败（{type(exc).__name__}）"]}
    if "AI 服务暂不可用" in text or "AI 服务未配置" in text:
        reason = "用户未绑定自己的 API Key（个人中心可配置）" if "AI 服务未配置" in text \
            else "模型服务占位响应"
        return {"ok": False, "letter": None, "warnings": [reason]}

    import re as _re
    verdict, suggestions = None, None
    match = _re.search(r"\{.*\}", text, _re.DOTALL)
    if match:
        try:
            data = _json.loads(match.group(0))
            verdict = str(data.get("verdict") or "").strip()[:QUARTERLY_LETTER_MAX_CHARS] or None
            suggestions = str(data.get("suggestions") or "").strip()[:400] or None
        except Exception:  # noqa: BLE001 —— 读不懂就当没有评语
            pass
    if verdict is None:
        return {"ok": False, "letter": None, "warnings": ["模型返回无法解析为评语"]}
    return {"ok": True, "letter": {"verdict": verdict, "suggestions": suggestions}, "warnings": []}


@app.post("/api/v1/agent/quarterly-review")
async def quarterly_review_endpoint(req: Request):
    """**季度策略信**的评语半环：季度材料 → 总评 + 参数提议（文本，不落库不执行）。

    请求体（Java PaperPeriodicReviewService 组装）：
        {"strategy": {...}, "period": {...}, "stats": {...}, "trades": [...],
         "lessons": {...}?, "verification": {...}?}

    - fail-open：任何失败 ok=false + warning，Java 侧季度信仍会发出（只带数字、写明未生成评语）；
    - 评语是「写给用户看的话」，不是教训（教训走周反思→MemoryLesson 那条线）。
    """
    try:
        data = await req.json()
        payload = data if isinstance(data, dict) else {}
        # BYOK：季度评语写给这个用户，Key 用他自己的（没配就"未生成评语"降级）
        completion, _ = _llm_callables(payload, payload.get("user_id"))
        result = await asyncio.to_thread(quarterly_review_run, payload, completion)
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"quarterly review error: {e}", exc_info=True)
        return {"ok": False, "letter": None, "warnings": ["季度评语端点异常"]}


# ==================== 策略修订提案（P2，2026-10-15）====================
# 与季度信的分工：季度信是「写给用户看的话」（提议永不落地）；本端点是**结构化修订闭环**
# 的提案半环 —— 健康材料 → 参数级提案 → 立刻用「截至今天、近期加权」的真实化回测开门禁，
# 产物由 Java 落 StrategyRevision 等人审批。与沙盒 evolve_replay 的 _gate 同一血统，
# 差别：① 数据窗口永远截到今天并按半衰期加权（不再固定重放 2025）；
#       ② 门禁过了也不自动应用 —— 应用动作在 Java 审批路径（人签字 + configUpdatedAt）。

def strategy_revision_run(payload: dict, completion=None) -> dict:
    from agent.revision import propose_revision
    return propose_revision(payload if isinstance(payload, dict) else {}, completion)


@app.post("/api/v1/agent/strategy-revision")
async def strategy_revision_endpoint(req: Request):
    """**策略修订提案**：健康材料 + 当前 config → 结构化提案 + 回测门禁证据。

    请求体（Java StrategyRevisionService 组装）：
        {"config": {...DSL...}, "health": {journal/expectation/equity/trades/lessons},
         "strategy": {"id":..,"name":..,"symbol":..}}

    - 数据窗口与加权口径见 agent/revision.py 头注释（截到今天 + 指数衰减）；
    - fail-closed 语义：提案不可解析/校验不过/门禁不过都会**如实返回**，
      Java 侧据此决定是否生成待审批记录（未过门禁的提案也留档，供人看否决理由）。
    """
    try:
        data = await req.json()
        payload = data if isinstance(data, dict) else {}
        # BYOK：提案花的是这个用户的 Key（没配就 ok=false，Java 不生成待审批记录）
        completion, _ = _llm_callables(payload, payload.get("user_id"))
        return await asyncio.to_thread(strategy_revision_run, payload, completion)
    except Exception as e:  # noqa: BLE001
        logger.error(f"strategy revision error: {e}", exc_info=True)
        return {"ok": False, "error": "修订提案端点异常"}


@app.post("/api/v1/llm/test")
async def llm_test(req: Request):
    """LLM 连通性探测（BYOK 配套，2026-10-25）：Java「保存前测试」按钮的后端。

    请求体：{"providers": [{name, base_url, api_key, model, chat_path?}, ...],
             "scope": "可选，冷却命名空间用（用户 id）"}
    逐条独立探测（不走 _post_chat 的冷却/切换/退避——探测要的是"每一家各自
    通不通"），单发 15s 超时、max_tokens=8。Key 只进请求头，错误信息截断
    200 字符且不会带出 Key。内部 token 保护（中间件对 /api/v1/* 统一 fail-closed）。
    """
    import llm_service

    try:
        data = await req.json()
    except Exception:  # noqa: BLE001
        return {"results": []}
    try:
        providers = llm_service.user_providers(
            data.get("providers"), data.get("scope") if isinstance(data, dict) else None)
        if not providers:
            return {"results": [], "error": "providers 不能为空"}
        results = await asyncio.to_thread(llm_service.test_providers, providers)
        return {"results": results}
    except ValueError as e:
        return {"results": [], "error": str(e)}
    except Exception as e:  # noqa: BLE001
        logger.error(f"llm test error: {e}", exc_info=True)
        return {"results": [], "error": "探测失败，请稍后重试"}


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

    # 扫到了 —— 会话用完即弃。
    # 账号掩码（2026-10-15 安全测试 L3）：Java 侧日志只记前 3 后 2，Python 侧对齐
    # —— 完整账号是凭证级敏感信息，不该全文进日志。
    _acc = str(session["account"])
    _masked = (_acc[:3] + "***" + _acc[-2:]) if len(_acc) > 5 else "***"
    logger.info(f"ths qr confirmed: account={_masked}")
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


# ==================== 新闻/资讯 ====================


def _news_error(message: str, status_code: int = 502):
    """新闻模块统一错误信封：`{"ok": false, "error": "中文可读原因"}`。

    与 THS 的 `_ths_error` 同形状（前端已熟悉），但**不复用那个函数名** ——
    新闻与同花顺是两个独立的数据域，共用一个以 THS 命名的 helper 会让
    "改 THS 的错误处理"意外改到新闻，反之亦然。
    """
    return JSONResponse(status_code=status_code, content={"ok": False, "error": message})


NEWS_TYPES = (news_client.LEVEL_NOTICE, news_client.LEVEL_MEDIA,
              news_client.LEVEL_REPORT, news_client.LEVEL_FORUM)


@app.get("/api/v1/news/hot-rank")
def news_hot_rank(limit: int = 20):
    """个股人气榜 TopN（资讯雷达「热榜」视图）。5 分钟 TTL 共享缓存，fail-open。

    中间件已按 `/api/v1/*` fail-closed 校验 X-Internal-Token（Java 代理调用）。
    """
    from akshare_client import get_hot_rank
    rows = get_hot_rank(limit=max(1, min(int(limit), 50)))
    return {"ok": bool(rows), "items": rows,
            "note": "东方财富个股人气榜，分钟级更新（5 分钟缓存）；排名是市场关注度，不是买卖建议"}


@app.post("/api/v1/news/fetch")
async def news_fetch(req: Request):
    """按标的/关键词聚合取数（N1 的唯一对外入口）。

    请求体：`{"symbol":"600519","keyword":"","types":[1,2,3],"days":7,"withBody":false,"onlyRelevant":false}`
    响应：`{"ok":true,"data":{"items":[…],"counts":{…},"errors":[…],"bodies_fetched":n}}`

    - `withBody=true`：给最近的公告补抓正文（每条约 1~2 次 HTTP 请求），
      只在"用户正在看某只股票"时打开 —— 没有正文时 N2 对公告只能回"信息不足，不判断方向"。
    - `onlyRelevant=true`：丢掉"只是提及该股"的媒体条目（大盘综述）。
      个股页打开它 —— 那页上每条都该是"关于这只票"的，否则用户看到的是"显示了一堆却不解释"。

    `counts` 与 `items` 是**去重后**的口径，前端会并排显示"公告 x / 媒体 y"，
    两者不一致会让用户以为丢了数据（这条约束由单测钉住）。

    ⚠️ 必须 `asyncio.to_thread`：这个调用链里有 4 个外部 HTTP 请求，
    其中全市场公告是上千行 —— 直接在事件循环里跑会把整个服务卡住。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)

    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)

    symbol = str(body.get("symbol") or "").strip()
    keyword = str(body.get("keyword") or "").strip()

    raw_types = body.get("types")
    types = None
    if raw_types is not None:
        if not isinstance(raw_types, list):
            return _news_error("types 必须是数组，如 [1,2,3]", status_code=400)
        try:
            chosen = {int(t) for t in raw_types}
        except (TypeError, ValueError):
            return _news_error("types 只能是数字（1公告 2媒体 3研报 4舆情）", status_code=400)
        unknown = chosen - set(NEWS_TYPES)
        if unknown:
            # 静默忽略未知类型会返回"空结果"，被误解成"这只票没有资讯"
            return _news_error(f"未知的资讯类型 {sorted(unknown)}（1公告 2媒体 3研报 4舆情）",
                               status_code=400)
        types = sorted(chosen)

    try:
        days = int(body.get("days", 7))
    except (TypeError, ValueError):
        return _news_error("days 必须是数字", status_code=400)
    days = max(1, min(days, 365))

    try:
        data = await asyncio.to_thread(
            news_client.search_news, symbol, keyword, types, days,
            with_body=bool(body.get("withBody", False)),
            only_relevant=bool(body.get("onlyRelevant", False)))
    except Exception as e:
        logger.error(f"news fetch error: {e}", exc_info=True)
        return _news_error("资讯服务暂时不可用，请稍后再试")

    return {"ok": True, "data": data}


# 单次请求规模上限：批量是 5 条/次 LLM 调用，深读是**一条一次**调用。
# 不设上限的话，一个 POST 就能触发上百次模型调用 —— 既慢又会烧掉一整天的额度。
NEWS_ANALYZE_MAX_BATCH = 100
NEWS_ANALYZE_MAX_DEEP = 20


@app.post("/api/v1/news/analyze")
async def news_analyze(req: Request):
    """把资讯抽成 spec §6 的结构化字段（N2）。

    请求体：`{"items":[{…N1 的事件结构…}],"mode":"batch"|"deep"}`
    响应：`{"ok":true,"data":{"items":[…原始字段 + 分析字段…],"notice":"…"}}`

    - `batch`：5 条一批，出方向/强度/一句话（量大、便宜）
    - `deep`：一条一次，额外出 `risks` / `opportunities`（用户点开某条才跑）

    两种模式**都不会因为模型不可用而失败**：降级为 `direction=null` +
    "信息不足，不判断方向"，原文照常返回。"信息不足 ≠ 丢数据"。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)
    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)

    items = body.get("items")
    if not isinstance(items, list) or not items:
        return _news_error("items 必须是非空数组", status_code=400)
    if not all(isinstance(item, dict) for item in items):
        return _news_error("items 里每一项都必须是对象", status_code=400)

    mode = str(body.get("mode") or "batch").strip()
    if mode not in ("batch", "deep"):
        return _news_error("mode 只能是 batch 或 deep", status_code=400)
    limit = NEWS_ANALYZE_MAX_DEEP if mode == "deep" else NEWS_ANALYZE_MAX_BATCH
    if len(items) > limit:
        return _news_error(f"{mode} 模式单次最多 {limit} 条（收到 {len(items)} 条）",
                           status_code=400)

    try:
        if mode == "deep":
            analyzed = await asyncio.to_thread(
                lambda: [news_understanding.analyze_one(item, mode="deep") for item in items])
            data = {
                "items": analyzed,
                "analyzed": sum(1 for entry in analyzed if entry.get("analyzed")),
                "skipped": 0,
                "errors": [],
            }
        else:
            data = await asyncio.to_thread(news_understanding.analyze_events, items)
    except Exception as e:
        logger.error(f"news analyze error: {e}", exc_info=True)
        return _news_error("AI 分析暂时不可用，请稍后再试")

    # 合规标识放在 data 里而不是信封顶层：信封形状 `{ok,data,error}` 是三个模块共用的，
    # 为一条免责声明去加第四个顶层键，会让所有解析信封的代码都要考虑特例。
    data["notice"] = news_understanding.AI_NOTICE
    return {"ok": True, "data": data}


NEWS_SYNTHESIS_MAX_ITEMS = 40


@app.post("/api/v1/news/synthesize")
async def news_synthesize(req: Request):
    """把一批事件**综合**成一段"这只票现在怎么看"（一次 LLM 调用）。

    与 `/news/analyze` 的分工：那个逐条分类（每条一个方向标签），这个做综合。
    用户的原始反馈是*"你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法"* ——
    逐条标签回答不了"那合起来呢"，所以必须有这一层。

    请求体：`{"symbol":"600519","name":"贵州茅台",
             "quote":{"price":"1257.12","changePercent":"-0.78"},"items":[…]}`

    响应：`{"ok":true,"data":{"read":"…","highlights":[…],"skepticism":"…",
            "notice":"…","ok":true}}`

    ⚠️ 失败时返回的 `data.ok=false` 且 `read` 为空串 —— 调用方据此**隐藏整块**。
    宁可不显示，也不要用一段空话占住页面最显眼的位置。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)
    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)

    items = body.get("items")
    if items is None:
        items = []
    if not isinstance(items, list):
        return _news_error("items 必须是数组", status_code=400)
    mode = str(body.get("mode") or "")
    # 市场综合的输入是"一屏快讯"，上限放宽到 60（个股综合仍是 40）
    max_items = 60 if mode == "market" else NEWS_SYNTHESIS_MAX_ITEMS
    if len(items) > max_items:
        return _news_error(f"单次最多综合 {max_items} 条（收到 {len(items)} 条）",
                           status_code=400)

    quote = body.get("quote") if isinstance(body.get("quote"), dict) else None
    # 散户情绪上下文（软失败）：综合解读时让模型知道"股吧在怎么议论"，
    # 但必须连同"未验证"的定性一起给——不带定性的情绪数据会被模型当成
    # 与公告同级的事实引用，那正是要防的。
    symbol_for_sentiment = str(body.get("symbol") or "")
    sentiment = None
    if symbol_for_sentiment:
        try:
            sentiment = await asyncio.to_thread(sentiment_client.get_sentiment,
                                                symbol_for_sentiment)
        except Exception as exc:  # noqa: BLE001 - 情绪是旁路上下文，失败不挡综合解读
            logger.warning("sentiment context unavailable for %s: %s",
                           symbol_for_sentiment, exc)
    # 市场级综合（资讯雷达的"今日信息面"）：同一端点的另一个模式。
    # 分流在取完 items 之后、取情绪之前——市场综合不需要个股情绪与行情上下文。
    if mode == "market":
        try:
            data = await asyncio.to_thread(news_understanding.synthesize_market, items)
        except Exception as e:
            logger.error(f"news market synthesize error: {e}", exc_info=True)
            return _news_error("AI 市场综合暂时不可用，请稍后再试")
        return {"ok": True, "data": data}

    try:
        data = await asyncio.to_thread(
            news_understanding.synthesize_read,
            items, str(body.get("symbol") or ""), str(body.get("name") or ""),
            quote, sentiment)
    except Exception as e:
        logger.error(f"news synthesize error: {e}", exc_info=True)
        return _news_error("AI 综合解读暂时不可用，请稍后再试")

    return {"ok": True, "data": data}


@app.post("/api/v1/news/body")
async def news_body(req: Request):
    """抓取资讯原文正文（"去链接化"的取数端点）。

    请求体：`{"url":"https://finance.eastmoney.com/a/xxx.html"}`
    响应：`{"ok":true,"data":{"body":"…","chars":1234}}`

    安全栏（白名单/私网拦截/限额/最短长度）都在 article_fetcher 里；
    这里失败返回 `ok=false` + 一句人话，Java 侧据此软降级为摘要+原文链接。
    ⚠️ 必须 `asyncio.to_thread`：抓取要等外部站点最长 8 秒，不能占事件循环。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)
    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)
    url = str(body.get("url") or "").strip()
    if not url:
        return _news_error("url 不能为空", status_code=400)

    from article_fetcher import FetchError, fetch_article_body
    try:
        text = await asyncio.to_thread(fetch_article_body, url)
    except FetchError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 - 取数边界必须兜住一切
        logger.error(f"news body fetch error: {exc}", exc_info=True)
        return {"ok": False, "error": "正文抓取失败，请稍后再试"}
    return {"ok": True, "data": {"body": text, "chars": len(text)}}


# ==================== 事件线（N4 聚类 + N5 总编/撰稿，2026-10-09 新闻雷达升级）====================

CLUSTER_MAX_ITEMS = news_clustering.MAX_INPUT_ITEMS


@app.post("/api/v1/news/cluster")
async def news_cluster(req: Request):
    """把一批资讯聚成事件线（N4，**纯代码，不调模型**）。

    请求体：`{"items":[{id,symbol,name,title,published_at,source_level,source_name,url,direction,plain_summary},…]}`
    （`id` 是 Java 侧 news_events 主键，Python 不解释、随成员原样透传回去。）

    响应：`{"ok":true,"data":{"clusters":[
        {"title","members":[…原样条目…],"member_count","first_at","last_at",
         "sources","source_count","symbols","directions"}],
        "unclustered_count":n}}`

    聚类是确定性纯函数：同一输入永远同一输出。LLM 综合是另一个端点
    （`/news/topic-synthesize`），失败绝不影响本端点 —— 事件线本身没有 LLM 也成立。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)
    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)
    items = body.get("items")
    if not isinstance(items, list) or not items:
        return _news_error("items 必须是非空数组", status_code=400)
    if not all(isinstance(item, dict) for item in items):
        return _news_error("items 里每一项都必须是对象", status_code=400)
    if len(items) > CLUSTER_MAX_ITEMS:
        return _news_error(f"单次最多聚类 {CLUSTER_MAX_ITEMS} 条（收到 {len(items)} 条）",
                           status_code=400)
    try:
        data = news_clustering.cluster_events(items)
    except Exception as e:
        logger.error(f"news cluster error: {e}", exc_info=True)
        return _news_error("聚类服务暂时不可用，请稍后再试")
    return {"ok": True, "data": data}


TOPIC_SYNTHESIS_MAX_CLUSTERS = 30


@app.post("/api/v1/news/topic-synthesize")
async def news_topic_synthesize(req: Request):
    """事件线综合（N5）：总编一次通览 / 单条线撰稿，一次 LLM 调用。

    请求体（mode=editor）：`{"mode":"editor","clusters":[…cluster 端点的 clusters…]}`
    响应：`{"ok":true,"data":{"ok":true,"topics":[{index,title,worth_deep,reason}]}}`

    请求体（mode=writer）：`{"mode":"writer","topic":{"title":"…","items":[…成员…]}}`
    响应：`{"ok":true,"data":{"ok":true,"title","story","outlook","direction","confidence","affected_symbols"}}`

    内层 `data.ok=false` 表示 LLM 不可用或输出无法解析（Java 侧按
    `editor_llm_unavailable` 留痕，事件线照常展示代码标题 —— fail-open）。
    """
    try:
        body = await req.json()
    except Exception:
        return _news_error("请求体不是合法 JSON", status_code=400)
    if not isinstance(body, dict):
        return _news_error("请求体必须是对象", status_code=400)
    mode = str(body.get("mode") or "").strip()
    if mode not in ("editor", "writer"):
        return _news_error("mode 只能是 editor 或 writer", status_code=400)

    if mode == "editor":
        clusters = body.get("clusters")
        if not isinstance(clusters, list) or not clusters:
            return _news_error("clusters 必须是非空数组", status_code=400)
        if len(clusters) > TOPIC_SYNTHESIS_MAX_CLUSTERS:
            return _news_error(
                f"单次最多总览 {TOPIC_SYNTHESIS_MAX_CLUSTERS} 条事件线（收到 {len(clusters)} 条）",
                status_code=400)
        try:
            data = await asyncio.to_thread(news_topics.editor_pass, clusters)
        except Exception as e:
            logger.error(f"news editor error: {e}", exc_info=True)
            return _news_error("AI 总览暂时不可用，请稍后再试")
        return {"ok": True, "data": data}

    topic = body.get("topic")
    if not isinstance(topic, dict):
        return _news_error("topic 必须是对象", status_code=400)
    try:
        data = await asyncio.to_thread(news_topics.write_topic, topic)
    except Exception as e:
        logger.error(f"news topic writer error: {e}", exc_info=True)
        return _news_error("AI 撰稿暂时不可用，请稍后再试")
    return {"ok": True, "data": data}


# ==================== 散户情绪（千股千评聚合指数）====================


@app.get("/api/v1/sentiment/{symbol}")
def get_sentiment_endpoint(symbol: str):
    """个股散户情绪：参与意愿/关注指数/千股千评评分 + 对照验证结论。

    对照验证 = 情绪分桶 × 次日收盘方向（与 Java 侧决策日志同一判定哲学）。
    未通过验证的情绪只展示不预测；验证结论随数据一起返回，前端必须展示。
    """
    data = sentiment_client.get_sentiment(symbol)
    if data is None:
        return {"ok": False, "error": "无法识别股票代码"}
    return {"ok": True, "data": data}


# ==================== 直接运行入口 ====================
#
# 为什么需要这一段：这个文件此前**没有 `__main__` 入口**，于是
# "在 PyCharm / IDEA 里右键 app.py → Run" 只会导入模块然后立刻退出，什么都不会起 ——
# 想用终端之外的方式启动服务就卡在这里，只能去敲 `python -m uvicorn app:app`。
#
# 参数与 `start.ps1` 对齐（127.0.0.1:8000）。刻意**不用 reload**：
# 热重载要求把应用写成 import 字符串（"app:app"），那会依赖"工作目录正好是这个目录"，
# 而 IDE 的运行配置里工作目录经常是仓库根 —— 与其留一个"在 IDE 里跑不起来"的坑，
# 不如让这个入口在任何工作目录下都能用。要热重载请用 start.ps1。
if __name__ == "__main__":
    import uvicorn

    logger.info("以脚本方式启动 uvicorn（127.0.0.1:8000，无热重载）")
    uvicorn.run(app, host="127.0.0.1", port=8000)



