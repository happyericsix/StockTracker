"""article_fetcher.py —— 把资讯原文抓回本站展示（"去链接化"的取数管线）。

<h2>安全栏（这道管线是对外发 HTTP 的，四道栏缺一不可）</h2>
1. **域名白名单**：只抓已知信源（东财系/巨潮/新浪/同花顺）。新闻 URL 来自
   第三方数据源，等于"别人给的地址就去访问"——不加白名单就是一个
   送给内网的探测器（SSRF）。
2. **私网拦截**：白名单通过后仍要解析 IP——DNS 重绑定可以把白名单域名
   指到 127.0.0.1/10.x/169.254.x（云元数据端点）。域名骗得过白名单，
   骗不过解析结果。
3. **限额**：单篇 8 秒超时、响应体上限 2MB。否则一个慢站/巨页就能占死
   工作线程。
4. **长度与失败语义**：抽不出正文返回明确错误（调用方软降级为摘要+原文
   链接），绝不返回半截 HTML 让前端渲染。

<h2>抽取策略（bs4 + lxml，无新依赖）</h2>
先试已知信源的容器选择器（东财 .txtinfos / #ContentBody 等），失败再退
"最长连续段落块"启发式。抽不出 200 字以上视为失败——比这短的"全文"
多半是 JS 渲染壳，拿摘要反而更诚实。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# 白名单按**后缀**匹配（覆盖子域）：finance.eastmoney.com / pdf.dfcfw.com 等
ALLOWED_DOMAIN_SUFFIXES = (
    "eastmoney.com",       # 东方财富（媒体正文的主要源）
    "dfcfw.com",           # 东财系（数据/研报落地页）
    "cninfo.com.cn",       # 巨潮资讯（公告）
    "sina.com.cn",         # 新浪财经
    "10jqka.com.cn",       # 同花顺
)

FETCH_TIMEOUT_S = 8
MAX_BYTES = 2 * 1024 * 1024
MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 20000

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
}

# 已知信源的正文容器（先试最快的路，失败再走启发式）
_KNOWN_CONTAINERS = (
    ".txtinfos",        # 东财文章页
    "#ContentBody",     # 东财另一种布局
    "#main-content",    # 巨潮
    ".art_content",     # 新浪财经
    "#ztxt",            # 同花顺
)


class FetchError(Exception):
    """带一句人话原因的取数失败（调用方拿它做软降级文案）。"""


def _check_url(url: str) -> str:
    parsed = urlparse(str(url or ""))
    if parsed.scheme not in ("http", "https"):
        raise FetchError("只支持 http/https 链接")
    host = (parsed.hostname or "").lower()
    if not host:
        raise FetchError("链接没有主机名")
    if not any(host == suffix or host.endswith("." + suffix)
               for suffix in ALLOWED_DOMAIN_SUFFIXES):
        raise FetchError(f"不在信源白名单内：{host}")
    return parsed.geturl()


def _reject_private_ip(url: str) -> None:
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise FetchError(f"域名解析失败：{host}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast):
            raise FetchError(f"信源域名解析到内网/保留地址，已拦截：{host} -> {ip}")


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    for selector in _KNOWN_CONTAINERS:
        node = soup.select_one(selector)
        if node:
            text = node.get_text("\n", strip=True)
            if len(text) >= MIN_BODY_CHARS:
                return text

    # 启发式：取"连续 <p> 文本总量最大"的容器（正文页的 p 都长在同一个父节点下）
    best, best_len = None, 0
    for p in soup.find_all("p"):
        parent = p.parent
        text = parent.get_text("\n", strip=True) if parent else ""
        if len(text) > best_len:
            best, best_len = parent, len(text)
    if best is not None and best_len >= MIN_BODY_CHARS:
        return best.get_text("\n", strip=True)
    return ""


def fetch_article_body(url: str) -> str:
    """抓取资讯原文正文。任何失败都抛 FetchError，绝不返回半截内容。"""
    safe_url = _check_url(url)
    _reject_private_ip(safe_url)
    try:
        response = requests.get(safe_url, headers=_HEADERS, timeout=FETCH_TIMEOUT_S,
                                allow_redirects=True, stream=True)
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_BYTES:
                raise FetchError("页面超过大小上限，放弃抓取")
        html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
    except FetchError:
        raise
    except requests.RequestException as exc:
        raise FetchError(f"请求失败：{exc}") from exc

    body = _extract_text(html)
    if len(body) < MIN_BODY_CHARS:
        raise FetchError("抽取不到有效正文（可能是 JS 渲染页）")
    return body[:MAX_BODY_CHARS]
