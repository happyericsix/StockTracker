"""sentiment_client.py —— 散户情绪聚合信号（东财股吧数据的聚合出口）。

<h2>这是什么、不是什么</h2>
"评论走向"的可量化近似：东财千股千评体系把**股吧评论行为**聚合成三个指数——
  - **参与意愿**（PARTICIPATION_WISH）：当天评论/互动的活跃倾向，0-100；
  - **用户关注指数**：关注热度，0-100；
  - **千股千评评分**：系统对个股的综合评价分。
我们不抓单条评论（反爬/合规/噪声三座大山，见 finetune/README 与调研），
拿的是平台自己聚合后的指数——**便宜、稳定、可回测**。

<h2>铁律：先当被验证的假设，不当预测信号</h2>
情绪指标在本项目里的唯一合法用法是"展示 + 对照验证"：
  1. `validate_against_price` 用**次日收盘方向**给情绪分桶对答案
     （与 Java 侧 TradeJournalService 同一套判定哲学）；
  2. 验证结论（"未见显著关系"/"样本不足"）**必须随数据一起展示**——
     用户看到的永远是"情绪 + 这个信号在本票上灵不灵"，而不是光秃秃的看多看空；
  3. 未通过对照验证的情绪**绝不进决策链**（与"模型未过前向验证不外放"同一条纪律）。

<h2>已知数据边界（实地探针 2026-09-30，600519）</h2>
- 参与意愿接口**只回最近 5 天**——它的长序列必须靠 Java 侧每日快照累积
  （`MarketSentimentSnapshot` + 定时任务），当天单次调用凑不满验证样本；
- 关注指数/评分各回 30 天，对照验证当下就能算（约 29 个样本，够粗筛）。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime

import akshare

from akshare_client import get_history
from trading_calendar import CN_TZ

logger = logging.getLogger(__name__)

# 情绪是日频数据：盘中刷新没有意义，缓存 30 分钟防个股页反复打开打上游
SENTIMENT_TTL = 30 * 60
_sentiment_cache: dict[str, tuple[float, dict]] = {}
_sentiment_lock = threading.Lock()

# 验证门槛（粗筛，不是严格检验——措辞在 verdict 里如实说）
MIN_VALIDATION_SAMPLES = 20
MIN_BUCKET_SAMPLES = 8
# 报"有信息量"的差值门槛 = max(固定下限, 两比例差的 95% 近似筛查值)：
# 每桶 n 个样本时，上涨比例差的噪声标准差约 sqrt(0.5*(1/n₁+1/n₂))，
# 小样本下 10 个百分点的差异完全可能是运气（40 样本时 2σ≈22pt）。
# 固定下限防大样本时的过度敏感。
MIN_UP_RATIO_DIFF = 0.10
Z_SCREEN = 1.96


def _bare(symbol: str) -> str:
    """三个情绪接口都要 6 位裸代码（600519），不吃前缀。"""
    text = str(symbol or "").strip().upper()
    for prefix in ("SH", "SZ", "BJ"):
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            return text[len(prefix):]
    return text


def _to_number(value):
    try:
        number = float(value)
        return number if number == number else None  # NaN 防御
    except (TypeError, ValueError):
        return None


def _series(rows, date_key, value_key) -> list[dict]:
    """[(日期str, 数值)] 升序、去 None——三条接口共用的整形器。"""
    out = []
    for row in rows or []:
        date = str(row.get(date_key) or "")
        value = _to_number(row.get(value_key))
        if date and value is not None:
            out.append({"date": date, "value": value})
    out.sort(key=lambda item: item["date"])
    return out


def _fetch_desire(symbol: str) -> dict:
    df = akshare.stock_comment_detail_scrd_desire_em(symbol=symbol)
    rows = df.to_dict("records")
    series = _series(rows, "交易日期", "参与意愿")
    if not series:
        return {"latest": None, "avg5": None, "change": None, "series": []}
    latest = series[-1]
    tail = rows[-1] if rows else {}
    return {
        "latest": latest["value"],
        "avg5": _to_number(tail.get("5日平均参与意愿")),
        "change": _to_number(tail.get("参与意愿变化")),
        "series": series,
        "note": "参与意愿接口仅返回最近 5 天；长序列由 Java 每日快照累积",
    }


def _fetch_index_series(fetch, symbol: str, date_key: str, value_key: str) -> list[dict]:
    df = fetch(symbol=symbol)
    return _series(df.to_dict("records"), date_key, value_key)


def validate_against_price(series: list[dict], closes: list[dict]) -> dict:
    """情绪分桶 × 次日收盘方向：这个信号在这只票上有没有信息量。

    判定与 TradeJournalService 同一哲学：次日收盘涨 = 方向对。把样本按情绪值
    的**中位数**分成高/低两桶，比较两桶的次日上涨比例。返回结构里的 verdict
    只有三档：样本不足 / 未见显著关系 / 初步显示有信息量——最后一档的措辞
    刻意克制：这是粗筛不是统计检验，没有做显著性检验，也不该被引用成
    "情绪预测股价"。

    series/closes 都是升序 [{date, value}] / [{date, value(收盘价)}]。
    """
    close_by_date = {row["date"]: row["value"] for row in closes if row.get("date")}
    ordered_dates = sorted(close_by_date)
    pairs = []  # (情绪值, 当日收盘, 次日收盘)
    for item in series:
        idx = ordered_dates.index(item["date"]) if item["date"] in close_by_date else -1
        if idx < 0 or idx + 1 >= len(ordered_dates):
            continue
        close = close_by_date[ordered_dates[idx]]
        nxt = close_by_date[ordered_dates[idx + 1]]
        if close and close > 0:
            pairs.append((item["value"], nxt > close))
    if len(pairs) < MIN_VALIDATION_SAMPLES:
        return {"samples": len(pairs), "verdict": "样本不足",
                "note": f"对照验证至少需要 {MIN_VALIDATION_SAMPLES} 个可配对样本"}

    # 秩分割（按值排序后切两半），不用 v>median 的阈值法：情绪值大量并列时
    # （比如恰好一半 70 一半 30，median=70），阈值法会把整组挤进低桶，
    # 高桶为空——那是分桶方法的失败，不是"没有信息量"
    ordered = sorted(pairs, key=lambda pair: pair[0])
    mid = len(ordered) // 2
    low = [up for _, up in ordered[:mid]]
    high = [up for _, up in ordered[mid:]]
    if len(high) < MIN_BUCKET_SAMPLES or len(low) < MIN_BUCKET_SAMPLES:
        return {"samples": len(pairs), "verdict": "样本不足",
                "note": "分桶后样本过少"}

    high_up = sum(high) / len(high)
    low_up = sum(low) / len(low)
    diff = high_up - low_up
    result = {
        "samples": len(pairs),
        "high": {"n": len(high), "up_ratio": round(high_up, 3)},
        "low": {"n": len(low), "up_ratio": round(low_up, 3)},
        "up_ratio_diff": round(diff, 3),
    }
    import math
    noise = math.sqrt(0.5 * (1 / len(high) + 1 / len(low)))
    threshold = max(MIN_UP_RATIO_DIFF, Z_SCREEN * noise)
    if abs(diff) >= threshold:
        side = "高" if diff > 0 else "低"
        result["verdict"] = (
            f"初步显示有信息量：情绪{side}组的次日上涨比例高出 {abs(diff) * 100:.0f} 个百分点"
            f"（超过小样本噪声门槛 {threshold * 100:.0f}pt；近似筛查，非严格检验，"
            "不构成买卖依据）")
    else:
        result["verdict"] = "未见显著关系（该信号在本票上暂时没有预测力）"
    return result


def get_sentiment(symbol: str) -> dict | None:
    """聚合三条情绪指数 + 对照验证。整体失败返回 None；子项失败只缺对应键。"""
    symbol = str(symbol or "").strip()
    if not symbol:
        return None
    now = time.time()
    with _sentiment_lock:
        cached = _sentiment_cache.get(symbol)
        if cached and now - cached[0] < SENTIMENT_TTL:
            return cached[1]

    bare = _bare(symbol)
    result: dict = {
        "symbol": symbol,
        "updated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "东方财富-千股千评（股吧行为聚合指数）",
        # 展示层必须带的定性声明：未验证的信号不许裸奔
        "disclaimer": "情绪为股吧行为的聚合统计，未通过对照验证前不构成任何买卖依据",
    }

    desire: dict = {}
    try:
        desire = _fetch_desire(bare)
    except Exception as exc:  # noqa: BLE001 - 子项失败不拖垮整包
        logger.warning("参与意愿取数失败 %s: %s", bare, exc)
        desire = {"latest": None, "avg5": None, "change": None, "series": [],
                  "error": "参与意愿暂时不可用"}
    result["desire"] = desire

    focus_series = []
    try:
        focus_series = _fetch_index_series(
            akshare.stock_comment_detail_scrd_focus_em, bare, "交易日", "用户关注指数")
    except Exception as exc:  # noqa: BLE001
        logger.warning("关注指数取数失败 %s: %s", bare, exc)
    result["focus"] = {
        "latest": focus_series[-1]["value"] if focus_series else None,
        "series": focus_series,
    }

    score_series = []
    try:
        score_series = _fetch_index_series(
            akshare.stock_comment_detail_zhpj_lspf_em, bare, "交易日", "评分")
    except Exception as exc:  # noqa: BLE001
        logger.warning("千股千评评分取数失败 %s: %s", bare, exc)
    result["score"] = {
        "latest": score_series[-1]["value"] if score_series else None,
        "series": score_series,
    }

    # 对照验证：关注指数/评分自带 30 天序列，当下就能对答案；
    # 参与意愿只有 5 天，验证留给 Java 快照累积后补（desire.validation = 样本不足）
    try:
        history = get_history(symbol) or []
        closes = [{"date": str(row.get("date") or ""), "value": _to_number(row.get("close"))}
                  for row in history]
        closes = [row for row in closes if row["date"] and row["value"]]
        result["validation"] = {
            "focus": validate_against_price(focus_series, closes),
            "score": validate_against_price(score_series, closes),
            "desire": {"samples": len(desire.get("series") or []) - 1,
                       "verdict": "样本不足",
                       "note": "参与意愿历史由每日快照累积，累积满 20 个交易日后自动验证"},
        }
    except Exception as exc:  # noqa: BLE001 - 验证是旁路，失败不拖垮展示
        logger.warning("情绪对照验证失败 %s: %s", symbol, exc)
        result["validation"] = None

    with _sentiment_lock:
        _sentiment_cache[symbol] = (now, result)
    return result
