# -*- coding: utf-8 -*-
"""一次性脚本（沙箱，不进项目）：把 A 股真实日线喂进 TradingAgents 的 yfinance 缓存。

为什么需要它：
  TradingAgents 的数据层是 yfinance，而这台机器上 Yahoo 直接限流
  （YFRateLimitError，连 AAPL 都取不到）。但它的历史回测**永远复用磁盘缓存**
  （dataflows/yfinance.py 的 _is_cache_fresh 对回测日期直接返回 True），
  缓存文件是 <results_dir>/data_cache/<TICKER>-YFin-data.csv。
  所以只要用我们自己的行情源（腾讯 ifzq，与产品同一个源）把
  真实日线写进去，它就能在完全离线的情况下跑起来。

两个刻意的做法：
  1. 用**真实数据**，不用合成/补齐的假 bar：它要求缓存覆盖 [curr_date-15y, curr_date]，
     那就真的去取 15 年，而不是伪造一根 15 年前的 bar 骗过检查。
  2. 复用产品里已被测试覆盖的分页取数（akshare_client.get_history），
     只把它的 MAX_PAGES 上限临时抬高（那个上限是给日常取数设的成本边界，
     这里要的是 15 年，属于明确的例外）。
"""
import csv
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT = Path(r"E:\GitHubjob\Stock Tracker\stock-tracker\python-data-service")
sys.path.insert(0, str(PROJECT))

import akshare_client  # noqa: E402

# 15 年需要 6 页以上（每页 900 自然日），把成本上限临时抬高
akshare_client.MAX_PAGES = 14

CACHE_DIR = Path(r"E:\GitHubjob\ta-sandbox\ta\results\data_cache")
START = "2011-06-01"          # 早于 任何回测日期的 curr_date-15y，保证覆盖
END = date.today().isoformat()

# 它按 Yahoo 代码命名缓存；A 股在 Yahoo 上是 .SS（沪）/ .SZ（深）
TARGETS = {
    "600519.SS": "600519",
    "000001.SZ": "000001",
}


def main() -> int:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for yahoo_symbol, code in TARGETS.items():
        records = akshare_client.get_history(code, START, END, "day")
        if not records:
            print(f"[FAIL] {yahoo_symbol}: 取不到数据")
            return 1
        out = CACHE_DIR / f"{yahoo_symbol}-YFin-data.csv"
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["Date", "Open", "High", "Low", "Close", "Volume"])
            for record in records:
                writer.writerow([
                    record["date"], record["open"], record["high"],
                    record["low"], record["close"], record["volume"],
                ])
        first, last = records[0]["date"], records[-1]["date"]
        needed_from = (date.today() - timedelta(days=365 * 15 + 30)).isoformat()
        covered = first <= needed_from
        print(f"{yahoo_symbol}: {len(records)} 根  {first} ~ {last}  "
              f"覆盖 curr_date-15y（需 <= {needed_from}）: {covered}")
        print(f"    -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
