"""行情源降级（腾讯 → 新浪）的用例。

验收的不是"新浪接口长什么样"（那是外部事实），而是**降级语义**：
主源失败才启用、只兜 A 股、兜底结果的键与主源完全一致、批量路径合并成
一次兜底请求、兜底结果同样进缓存。每一条都对应一类真实事故。
"""
import akshare_client as ac


def _tencent_ok(code="sh600519"):
    class _Resp:
        encoding = "utf-8"
        text = f'v_{code}="1~贵州茅台~600519~1259.59~1272.7~' + "~" * 36 + '";'
    return _Resp()


def _sina_ok(code="sh600519", close="1700.00", prev="1690.00"):
    fields = ["贵州茅台", "1695.00", prev, close, "1710.00", "1688.00"]
    fields += ["0.00"] * 2 + ["1234500", "2098650000.00"] + ["0.00"] * 20
    fields += ["2026-09-30", "15:00:03"]
    class _Resp:
        encoding = "utf-8"
        text = f'var hq_str_{code}="' + ",".join(fields) + '";'
    return _Resp()


class _Boom(Exception):
    pass


def _failing_get(url, **kwargs):
    if "qt.gtimg.cn" in url:
        raise _Boom("tencent down")
    raise AssertionError(f"不应该打到这个源: {url}")


def test_single_quote_falls_back_to_sina_when_tencent_fails(monkeypatch):
    ac._quote_cache.clear()
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        if "qt.gtimg.cn" in url:
            raise _Boom("tencent down")
        assert "hq.sinajs.cn" in url
        assert kwargs["headers"].get("Referer") == "https://finance.sina.com.cn", "新浪 2021 起强制 Referer"
        return _sina_ok()

    monkeypatch.setattr(ac.requests, "get", fake_get)
    quote = ac.get_quote("600519")

    assert quote is not None
    # 键必须与腾讯解析器完全一致：下游契约（GlobalQuote 等）按键取值
    assert set(quote) == {"代码", "名称", "最新价", "昨收", "今开", "最高", "最低",
                          "成交量", "成交额", "涨跌幅", "涨跌额",
                          "总市值", "流通市值", "市盈率-动态"}
    assert quote["最新价"] == "1700.00"
    assert quote["涨跌额"] == "10.00"
    assert quote["涨跌幅"] == "0.59"
    # 兜底结果同样进缓存：下一次 15s 内的请求不该再打任何源
    assert ("600519", quote) in [(k, v[1]) for k, v in ac._quote_cache.items()]


def test_no_fallback_for_non_a_share(monkeypatch):
    ac._quote_cache.clear()

    def fake_get(url, **kwargs):
        assert "qt.gtimg.cn" in url
        raise _Boom("tencent down")

    monkeypatch.setattr(ac.requests, "get", fake_get)
    assert ac.get_quote("AAPL") is None, "美股新浪是另一套字段布局，兜底只会静默给错数"


def test_primary_source_still_used_when_healthy(monkeypatch):
    ac._quote_cache.clear()
    sources = []

    def fake_get(url, **kwargs):
        sources.append("sina" if "sinajs" in url else "tencent")
        return _sina_ok() if "sinajs" in url else _tencent_ok()

    monkeypatch.setattr(ac.requests, "get", fake_get)
    quote = ac.get_quote("600519")
    assert quote["最新价"] == "1259.59"  # 腾讯主源的价
    assert sources == ["tencent"], "主源健康时不该打新浪"


def test_batch_fallback_fills_only_the_missing_and_uses_one_request(monkeypatch):
    ac._quote_cache.clear()
    sina_calls = []

    def fake_get(url, **kwargs):
        if "sinajs" in url:
            sina_calls.append(url)
            # 按请求的代码生成响应：响应代码与请求对不上时本就不该回填
            wanted = url.split("list=")[-1].split(",")
            return _Resp_lines([_sina_ok(code).text for code in wanted])
        # 腾讯只回 600519，另一只"缺失"
        return _tencent_ok()

    monkeypatch.setattr(ac.requests, "get", fake_get)
    out = ac.get_quotes(["600519", "000001"])

    assert out["600519"]["最新价"] == "1259.59"     # 腾讯给的
    assert out["000001"]["最新价"] == "1700.00"     # 新浪兜的
    assert len(sina_calls) == 1, "兜底必须合并成一次请求"


class _Resp_lines:
    """多行新浪响应（一行一只票）。"""
    def __init__(self, lines):
        self.encoding = "utf-8"
        self.text = "\n".join(lines)


def test_batch_total_failure_still_recovers_a_shares(monkeypatch):
    """整批腾讯请求挂掉（超时/断网）：A 股部分从新浪救回，而不是整批 None。"""
    ac._quote_cache.clear()

    def fake_get(url, **kwargs):
        if "qt.gtimg.cn" in url:
            raise _Boom("tencent timeout")
        return _sina_ok()

    monkeypatch.setattr(ac.requests, "get", fake_get)
    out = ac.get_quotes(["600519"])

    assert out["600519"] is not None
    assert out["600519"]["昨收"] == "1690.00"


def test_https_is_used_for_all_quote_and_kline_endpoints():
    """明文 http 会被中间人篡改价格喂给回测——三个腾讯端点全部 https。"""
    import inspect
    source = inspect.getsource(ac)
    assert "http://qt.gtimg.cn" not in source
    assert "http://web.ifzq.gtimg.cn" not in source


def test_sina_parser_rejects_malformed_rows():
    assert ac._sina_quote_fields("sh600519", ["x"] * 5) is None
    assert ac._sina_quote_fields("sh600519", ["名称"] + ["0.00"] * 40) is None, "最新价为 0 的停牌行不兜"
