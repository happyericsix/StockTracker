"""临时探针：核实 akshare 新闻/公告接口的真实可用性与返回字段。

用途仅为施工前的事实核对，不属于产品代码；核对完成后可直接删除。
运行（必须带 UTF-8，否则 Windows 控制台 GBK 会把中文列名显示成乱码）：

    $env:PYTHONIOENCODING="utf-8"; .\\.venv\\Scripts\\python.exe _probe_news.py
"""

import datetime
import json
import sys

import akshare as ak


def collect() -> dict:
    schema: dict = {}

    def grab(name: str, fn) -> None:
        try:
            df = fn()
        except Exception as exc:  # noqa: BLE001 - 探针脚本，任何异常都要打印出来
            schema[name] = {"error": f"{type(exc).__name__}: {exc}"}
            return
        if df is None or len(df) == 0:
            schema[name] = {"error": "empty result"}
            return
        schema[name] = {
            "rows": int(len(df)),
            "columns": [str(c) for c in df.columns],
            "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
            "sample": df.head(2).to_dict("records"),
        }

    today = datetime.date.today().strftime("%Y%m%d")
    grab("stock_notice_report(全部, today)", lambda: ak.stock_notice_report(symbol="全部", date=today))
    grab("stock_news_em(600519)", lambda: ak.stock_news_em(symbol="600519"))
    grab("stock_research_report_em(600519)", lambda: ak.stock_research_report_em(symbol="600519"))
    grab("stock_news_main_cx", lambda: ak.stock_news_main_cx())
    return schema


def report(schema: dict) -> None:
    print(f"akshare {ak.__version__} / today {datetime.date.today().isoformat()}")
    for name, info in schema.items():
        if "error" in info:
            print(f"[{name}] ERROR {info['error']}")
            continue
        print(f"[{name}] rows={info['rows']}")
        print(f"  columns={json.dumps(info['columns'], ensure_ascii=False)}")
        print(f"  dtypes={json.dumps(info['dtypes'], ensure_ascii=False)}")
        print(f"  sample={json.dumps(info['sample'], ensure_ascii=False, default=str)[:800]}")


if __name__ == "__main__":
    report(collect())
    sys.exit(0)
