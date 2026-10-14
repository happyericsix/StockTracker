# -*- coding: utf-8 -*-
"""市场语境证据：大盘（market_context）/ 板块（sector_context）/ 估值（valuation_context）。

<h3>它补的是什么</h3>
"一个合格炒股人开盘先看什么"的三级：大盘环境 → 板块强弱 → 个股。此前委员会的
证据包只有个股层（行情/新闻/财务/战绩/组合），评价一只票时不知道今天大盘是
暴涨还是崩盘、板块是领涨还是领跌、这票在自身历史上贵不贵。本模块补齐这三块。

<h3>数据源与已验证的出口（2026-10-14 实测）</h3>
- 指数点位/涨跌幅/两市成交额：**腾讯 qt.gtimg.cn**——与 akshare_client 的行情
  同源同款出口（生产验证过），指数响应与个股同为 ~ 分隔；
- 涨跌家数：乐咕 `stock_market_activity_legu`；
- 估值（PE-TTM/市净率的**日线历史序列** → 历史分位）：东财数据中心 `stock_value_em`；
- 个股行业 + 板块当日行情：东财 push2（`stock_individual_info_em` /
  `stock_board_industry_spot_em`）——该出口限流时这两块自动缺席（fail-open），
  恢复后自动回来，委员会照旧只看其余证据。

<h3>纪律</h3>
1. **全部 fail-open**：任何一块取不到就是不带（None），绝不让市场语境的故障
   挡住决策——与 news_briefing / fundamentals 同一条纪律；
2. **TTL 缓存全市场共用一份**：大盘快照对同一时刻所有策略的委员会是同一份，
   成本 ≈ 每 TTL 一次，不随策略数线性涨；
3. **整形器是纯函数**（可测），网络函数只做薄封装；
4. 北向资金刻意不做：实时流向 2024-08 起交易所已停止披露，做出来就是过时口径。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 腾讯指数代码（与 akshare_client.normalize_symbol 的前缀约定一致）。
#: 创业板指是 sz399006——399002 是"深成指R"（收益指数），上线实测踩过。
TENCENT_INDEX_CODES = {
    "上证指数": "sh000001",
    "深证综指": "sz399106",      # 深市全市场口径（深证成指只覆盖 500 只样本股）
    "创业板指": "sz399006",
}

#: 腾讯指数响应的字段位（实测钉死；与个股同构）：3=点位 4=昨收 31=涨跌额 32=涨跌幅% 37=成交额(万元)
_TX_POINT, _TX_PREV_CLOSE, _TX_CHG, _TX_PCT, _TX_TURNOVER_WAN = 3, 4, 31, 32, 37

#: TTL（秒）：大盘快照与板块行情分钟级（盘中开会也新鲜）；行业归属日级；估值序列日级。
MARKET_TTL = 5 * 60
SECTOR_TTL = 5 * 60
INDUSTRY_TTL = 24 * 3600
VALUATION_TTL = 12 * 3600

#: 估值分位的历史窗口（交易日 ≈ 5 年）。
VALUATION_WINDOW_ROWS = 1250

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()

_TX_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _cached(key: str, ttl: float, fetch):
    """带 TTL 的进程内缓存；fetch 抛异常不缓存、向上抛（调用方 fail-open）。"""
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    value = fetch()
    with _CACHE_LOCK:
        _CACHE[key] = (now, value)
    return value


def _clear_cache() -> None:
    """测试用：清空缓存。"""
    with _CACHE_LOCK:
        _CACHE.clear()


# ==================== ① 大盘：指数 + 两市成交额 + 涨跌家数 ====================

def _shape_index_fields(fields: list[str]) -> Optional[dict]:
    """腾讯指数响应的一行 → {name, code, level, prev_close, change, pct, turnover_yi}。"""
    try:
        return {
            "name": str(fields[1]),
            "code": str(fields[2]),
            "level": float(fields[_TX_POINT]),
            "prev_close": float(fields[_TX_PREV_CLOSE]),
            "change": float(fields[_TX_CHG]),
            "pct": float(fields[_TX_PCT]),
            "turnover_yi": round(float(fields[_TX_TURNOVER_WAN]) / 10000.0, 1),  # 万 → 亿
        }
    except (IndexError, TypeError, ValueError):
        return None


def _fetch_indices() -> Optional[list[dict]]:
    """一次腾讯批量请求取三只指数（与 akshare_client 同款出口）。"""
    import requests

    codes = ",".join(TENCENT_INDEX_CODES.values())
    resp = requests.get(f"https://qt.gtimg.cn/q={codes}", headers=_TX_HEADERS, timeout=10)
    resp.encoding = "gbk"
    out: list[dict] = []
    for segment in resp.text.strip().split(";"):
        segment = segment.strip()
        if "=" not in segment:
            continue
        fields = segment.split("=", 1)[1].strip('"').split("~")
        shaped = _shape_index_fields(fields)
        if shaped:
            out.append(shaped)
    return out or None


def _shape_breadth(df) -> Optional[dict]:
    """乐咕涨跌家数（item/value 长表）→ {上涨/下跌/涨停/跌停/平盘, as_of}。"""
    try:
        pairs = {str(r["item"]): r["value"] for _, r in df.iterrows()}
        wanted = {}
        for key in ("上涨", "下跌", "涨停", "跌停", "平盘"):
            if key in pairs and pairs[key] is not None:
                wanted[key] = int(float(pairs[key]))
        if not wanted:
            return None
        out = dict(wanted)
        if "统计日期" in pairs:
            out["as_of"] = str(pairs["统计日期"])
        return out
    except Exception:  # noqa: BLE001 —— 列名/类型对不上就整块缺席
        return None


def _fetch_breadth() -> Optional[dict]:
    import akshare as ak

    return _shape_breadth(ak.stock_market_activity_legu())


def get_market_context() -> Optional[dict]:
    """大盘语境块（指数 + 两市成交额 + 涨跌家数）。任一子块缺失就少带，全缺返回 None。"""
    indices: list[dict] = []
    try:
        indices = _cached("indices", MARKET_TTL, _fetch_indices) or []
    except Exception as exc:  # noqa: BLE001
        logger.info("大盘指数取数失败（不带该块）：%s", type(exc).__name__)
    breadth: Optional[dict] = None
    try:
        breadth = _cached("breadth", MARKET_TTL, _fetch_breadth)
    except Exception as exc:  # noqa: BLE001
        logger.info("涨跌家数取数失败（不带该块）：%s", type(exc).__name__)
    if not indices and not breadth:
        return None

    out: dict[str, Any] = {
        "note": ("大盘环境（先看这里再谈个股）：逆大盘的个股方向需要更硬的证据；"
                 "两市成交额与涨跌家数量化市场情绪冷热。数据为快照口径。"),
    }
    if indices:
        out["indices"] = {item["name"]: {k: item[k] for k in ("level", "pct", "turnover_yi")}
                          for item in indices}
        turnover = sum(item["turnover_yi"] for item in indices
                       if item["name"] in ("上证指数", "深证综指"))
        if turnover > 0:
            out["total_turnover_yi"] = round(turnover, 0)
    if breadth:
        out["breadth"] = breadth
    return out


# ==================== ② 板块：个股所属行业的当日表现 ====================

def _fetch_industry(symbol: str) -> Optional[str]:
    """个股所属东财行业（日级缓存；push2 限流时返回 None → 板块块整体缺席）。"""
    import akshare as ak

    info = ak.stock_individual_info_em(symbol=str(symbol).strip())
    mapping = {str(item): value for item, value in zip(info["item"], info["value"])}
    industry = mapping.get("行业")
    return str(industry).strip() if industry else None


def _shape_sector(industry: str, board_df) -> Optional[dict]:
    """行业行情快照里找出本行业那一行 → {industry, today_pct, rank, total, up/down}。"""
    try:
        df = board_df.copy()
        df["板块名称"] = df["板块名称"].astype(str)
        row = df[df["板块名称"] == industry]
        if row.empty:
            return None
        ranked = df.sort_values("涨跌幅", ascending=False).reset_index(drop=True)
        rank = int(ranked.index[ranked["板块名称"] == industry][0]) + 1
        r = row.iloc[0]
        return {
            "industry": industry,
            "today_pct": round(float(r["涨跌幅"]), 2),
            "rank": rank,
            "total_boards": int(len(df)),
            "up_count": int(float(r.get("上涨家数") or 0)),
            "down_count": int(float(r.get("下跌家数") or 0)),
            "top_stock": str(r.get("领涨股票") or ""),
        }
    except Exception:  # noqa: BLE001
        return None


def _fetch_sector(industry: str) -> Optional[dict]:
    import akshare as ak

    return _shape_sector(industry, ak.stock_board_industry_spot_em())


def get_sector_context(symbol: str) -> Optional[dict]:
    """板块语境块：行业名 + 当日涨跌幅 + 全行业排名 + 板块内涨跌家数。fail-open。"""
    try:
        industry = _cached(f"industry:{symbol}", INDUSTRY_TTL,
                           lambda: _fetch_industry(symbol))
        if not industry:
            return None
        shaped = _cached("board_spot", SECTOR_TTL,
                         lambda: _fetch_sector(industry))
        if not shaped:
            return None
        return {
            "note": ("板块语境：个股所属行业的当日表现与全行业排名。"
                     "强板块里的弱股与弱板块里的强股，含义不同。"),
            **shaped,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("板块语境取数失败 symbol=%s（不带该块）：%s", symbol, type(exc).__name__)
        return None


# ==================== ③ 估值：PE/PB 与自身历史分位 ====================

def _shape_valuation(df) -> Optional[dict]:
    """value_em 的日线序列 → 当前 PE-TTM/市净率/总市值 + 近 5 年分位（纯函数）。"""
    try:
        window = df.iloc[-VALUATION_WINDOW_ROWS:]
        last = window.iloc[-1]

        def _pct_rank(series, current) -> Optional[float]:
            values = [v for v in series if v is not None and v == v]  # 去 NaN
            if not values or current is None or current != current:
                return None
            return round(sum(1 for v in values if v <= current) / len(values) * 100.0, 1)

        pe = float(last["PE(TTM)"])
        pb = float(last["市净率"])
        return {
            "date": str(last["数据日期"]),
            "pe_ttm": round(pe, 2),
            "pe_percentile_5y": _pct_rank(window["PE(TTM)"], pe),
            "pb": round(pb, 2),
            "pb_percentile_5y": _pct_rank(window["市净率"], pb),
            "market_cap_yi": round(float(last["总市值"]) / 1e8, 0),
        }
    except Exception:  # noqa: BLE001
        return None


def _fetch_valuation(symbol: str) -> Optional[dict]:
    import akshare as ak

    return _shape_valuation(ak.stock_value_em(symbol=str(symbol).strip()))


def get_valuation_context(symbol: str) -> Optional[dict]:
    """估值语境块：PE/PB 当前值与近 5 年历史分位（贵不贵，和自己的历史比）。fail-open。"""
    try:
        shaped = _cached(f"valuation:{symbol}", VALUATION_TTL,
                         lambda: _fetch_valuation(symbol))
        if not shaped:
            return None
        return {
            "note": ("估值语境：PE/PB 与自身近 5 年历史分位（0=历史最便宜，100=历史最贵）。"
                     "高分位不是卖出指令，但加仓的证据门槛应当更高。"),
            **shaped,
        }
    except Exception as exc:  # noqa: BLE001
        logger.info("估值语境取数失败 symbol=%s（不带该块）：%s", symbol, type(exc).__name__)
        return None


def describe() -> dict:
    """给 /health 与调试用。"""
    return {
        "blocks": ["market_context", "sector_context", "valuation_context"],
        "sources": {
            "indices": "腾讯 qt.gtimg.cn（与行情主源同出口）",
            "breadth": "乐咕 stock_market_activity_legu",
            "sector": "东财 push2（限流时该块自动缺席，恢复即回）",
            "valuation": "东财数据中心 stock_value_em（日线序列算 5 年分位）",
        },
        "ttl_seconds": {"market": MARKET_TTL, "sector": SECTOR_TTL,
                        "industry": INDUSTRY_TTL, "valuation": VALUATION_TTL},
        "deliberately_absent": "北向资金（实时流向 2024-08 起停止披露）",
    }
