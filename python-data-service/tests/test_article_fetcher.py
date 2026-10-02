# -*- coding: utf-8 -*-
"""article_fetcher：安全栏（白名单/SSRF/限额）与抽取策略。

外部 HTTP 全部用假 requests（不摸网络）；安全栏的判定是纯逻辑，
必须钉住 —— 这条管线是对外发请求的，栏子松一格就是 SSRF 探测器。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import article_fetcher as af
from article_fetcher import FetchError, fetch_article_body


# ==================== 安全栏 ====================

def test_rejects_non_http_scheme():
    with pytest.raises(FetchError):
        fetch_article_body("javascript:alert(1)")
    with pytest.raises(FetchError):
        fetch_article_body("file:///etc/passwd")


def test_rejects_domains_outside_whitelist():
    # 内网地址即使长得像信源也必须拦：SSRF 的标准打法
    with pytest.raises(FetchError, match="白名单"):
        fetch_article_body("http://127.0.0.1:8080/admin")
    with pytest.raises(FetchError, match="白名单"):
        fetch_article_body("https://evil-example.com/a.html")


def test_whitelist_matches_by_suffix_not_substring():
    # "not-eastmoney.com" 不该因包含 "eastmoney" 而放行
    with pytest.raises(FetchError, match="白名单"):
        af._check_url("https://fake-eastmoney.com.evil.io/a.html")


def test_private_ip_resolution_is_blocked(monkeypatch):
    """白名单域名被 DNS 重绑定到内网 → 解析结果这一关必须拦下。"""
    monkeypatch.setattr(af.socket, "getaddrinfo",
                        lambda host, *a: [(2, 1, 6, "", ("127.0.0.1", 0))])
    with pytest.raises(FetchError, match="内网"):
        fetch_article_body("https://finance.eastmoney.com/a.html")


def test_size_cap_aborts_before_parsing(monkeypatch):
    class _BigResp:
        encoding = "utf-8"
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield b"<html>" + b"x" * 1024
            yield b"x" * (af.MAX_BYTES)
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: _BigResp())
    with pytest.raises(FetchError, match="上限"):
        fetch_article_body("https://finance.eastmoney.com/a.html")


# ==================== 抽取 ====================

_ARTICLE_HTML = """
<html><body>
<nav>导航 导航 导航 导航 导航 导航</nav>
<div class="txtinfos">""" + "正文段落。" * 120 + """</div>
<footer>页脚 页脚</footer>
</body></html>
"""


def test_known_container_wins(monkeypatch):
    class _Resp:
        encoding = "utf-8"
        text = ""
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield _ARTICLE_HTML.encode("utf-8")
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: _Resp())
    body = fetch_article_body("https://finance.eastmoney.com/a/1.html")
    assert "正文段落" in body
    assert "导航" not in body and "页脚" not in body


def test_too_short_page_is_failure_not_half_content(monkeypatch):
    class _Resp:
        encoding = "utf-8"
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield "<html><body><div class='txtinfos'>太短</div></body></html>".encode("utf-8")
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: _Resp())
    with pytest.raises(FetchError, match="正文"):
        fetch_article_body("https://finance.eastmoney.com/a/1.html")


def test_heuristic_fallback_when_no_known_container(monkeypatch):
    html = "<html><body><div><p>" + "启发式段落。" * 100 + "</p></div></body></html>"
    class _Resp:
        encoding = "utf-8"
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield html.encode("utf-8")
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: _Resp())
    body = fetch_article_body("https://finance.eastmoney.com/a/1.html")
    assert "启发式段落" in body


def test_body_is_capped_at_storage_limit(monkeypatch):
    html = "<html><body><div class='txtinfos'>" + "长" * (af.MAX_BODY_CHARS + 5000) + "</div></body></html>"
    class _Resp:
        encoding = "utf-8"
        def raise_for_status(self): pass
        def iter_content(self, chunk_size):
            yield html.encode("utf-8")
    monkeypatch.setattr(af.requests, "get", lambda *a, **k: _Resp())
    assert len(fetch_article_body("https://finance.eastmoney.com/a/1.html")) == af.MAX_BODY_CHARS
