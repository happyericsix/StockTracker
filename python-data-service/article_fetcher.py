"""article_fetcher.py —— 把资讯原文抓回本站展示（"去链接化"的取数管线）。

<h2>安全栏（这道管线是对外发 HTTP 的，四道栏缺一不可）</h2>
1. **域名白名单**：只抓已知信源（东财系/巨潮/新浪/同花顺）。新闻 URL 来自
   第三方数据源，等于"别人给的地址就去访问"——不加白名单就是一个
   送给内网的探测器（SSRF）。
2. **私网拦截**：白名单通过后仍要解析 IP——DNS 重绑定可以把白名单域名
   指到 127.0.0.1/10.x/169.254.x（云元数据端点）。域名骗得过白名单，
   骗不过解析结果。（残余风险：解析与连接之间理论上有 DNS 重绑定的
   TOCTOU 窗口，完整消除需 IP 级绑定连接，对 CDN 信源不现实，记录在案。）
3. **重定向逐跳复检**（2026-10-15 SSRF 修复）：requests 的
   allow_redirects=True 只校验第一跳，302 目标不再过任何栏——白名单域名
   上的开放重定向可以跳到内网/云元数据。现在禁自动重定向，手动跟随
   且**每一跳**重新过 1+2 两道栏，上限 3 跳。
4. **限额**：单篇 8 秒超时、响应体上限 2MB。否则一个慢站/巨页就能占死
   工作线程。
5. **长度与失败语义**：抽不出正文返回明确错误（调用方软降级为摘要+原文
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
from urllib.parse import urljoin, urlparse

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
MAX_REDIRECTS = 3          # 正常信源 1~2 跳足够；更多多半是重定向环/开放重定向
MAX_BYTES = 2 * 1024 * 1024
MAX_PDF_BYTES = 5 * 1024 * 1024   # 研报 PDF 普遍 0.5~3MB
MIN_BODY_CHARS = 200
MAX_BODY_CHARS = 20000
# 已知信源容器的门槛：命中 .txtinfos 这类**专用正文容器**的本来就是正文页，
# 而快讯类文章常常只有几十字 —— 200 字会把真正文丢掉再误报"JS 渲染页"。
# 2026-10-12 实测：东财全球快讯 318 条失败的主因就是 83 字真正文被 200 门槛击杀。
MIN_BODY_CHARS_KNOWN = 50

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


def _request_checked(url: str):
    """发请求并处理重定向：**每一跳**重新过白名单 + 私网栏（2026-10-15 SSRF 修复）。

    requests 的 allow_redirects=True 只校验传进去的第一个 URL，302 的目标
    不会再过任何栏——白名单域名上的开放重定向（或被篡改的信源页）可以
    一路跳到 169.254.169.254 / localhost。这里禁自动重定向、手动跟随，
    每跳 _check_url + _reject_private_ip，跳数上限 MAX_REDIRECTS。

    测试桩兼容：最小假响应可能没有 status_code/headers/close（生产
    Response 恒有），缺 status_code 按普通 200 响应对待。
    """
    for _ in range(MAX_REDIRECTS + 1):
        response = requests.get(url, headers=_HEADERS, timeout=FETCH_TIMEOUT_S,
                                allow_redirects=False, stream=True)
        status = getattr(response, "status_code", 200)
        headers = getattr(response, "headers", None) or {}
        if status not in (301, 302, 303, 307, 308):
            return response
        location = headers.get("Location")
        if not location:
            # 声称重定向却没给 Location：当普通响应对待，让调用方自己消化
            return response
        next_url = urljoin(url, str(location))
        closer = getattr(response, "close", None)
        if closer:
            closer()
        url = _check_url(next_url)
        _reject_private_ip(next_url)
    raise FetchError("重定向次数超过上限，放弃抓取")


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
        tag.decompose()

    for selector in _KNOWN_CONTAINERS:
        node = soup.select_one(selector)
        if node:
            text = node.get_text("\n", strip=True)
            # 已知正文容器用低门槛（快讯几十字也是完整原文），启发式才用高门槛
            if len(text) >= MIN_BODY_CHARS_KNOWN:
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
    """抓取资讯原文正文。任何失败都抛 FetchError，绝不返回半截内容。

    三条路（按 URL 形态自动选）：
    1. 东财公告详情页（data.eastmoney.com/notices/detail/...）→ 页面本身 JS 渲染，
       静态 HTML 只有页脚免责声明；正文走东财公开 JSON API（np-cnotice-stock）。
    2. PDF（pdf.dfcfw.com 等）→ pypdf 抽文本。
    3. 普通 HTML → 选择器 + 启发式。
    2026-10-05 实测：527 条事件正文抽取率 0.76%；公告 API 与 PDF 分支就是为
    补齐 158 条公告详情页和 45 条研报 PDF 来的。
    """
    safe_url = _check_url(url)
    _reject_private_ip(safe_url)

    if "/notices/detail/" in safe_url:
        body = _fetch_notice_via_api(safe_url)
        if len(body) >= MIN_BODY_CHARS:
            return body[:MAX_BODY_CHARS]
        if body:
            # API 给了但太短：可能是超短公告，直接返回（比丢掉诚实）
            return body[:MAX_BODY_CHARS]
        raise FetchError("公告正文 API 未返回内容")

    try:
        response = _request_checked(safe_url)
        response.raise_for_status()
        # getattr 兼容测试桩（最小响应对象没有 headers）；生产路径恒有
        resp_headers = getattr(response, "headers", None) or {}
        content_type = str(resp_headers.get("Content-Type") or "").lower()
        looks_pdf = ("application/pdf" in content_type
                     or safe_url.lower().split("?")[0].endswith(".pdf"))
        cap = MAX_PDF_BYTES if looks_pdf else MAX_BYTES
        chunks, total = [], 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            chunks.append(chunk)
            total += len(chunk)
            if total > cap:
                raise FetchError("页面超过大小上限，放弃抓取")
        payload = b"".join(chunks)
    except FetchError:
        raise
    except requests.RequestException as exc:
        raise FetchError(f"请求失败：{exc}") from exc

    if looks_pdf or payload[:5] == b"%PDF-":
        return _extract_pdf_text(payload)

    html = payload.decode(response.encoding or "utf-8", errors="replace")
    body = _extract_text(html)
    if _looks_like_boilerplate(body):
        raise FetchError("页面只有站点模板文字（JS 渲染页），没有正文")
    # 门槛判定已在 _extract_text 内按路径分档（已知容器 50 / 启发式 200）：
    # 这里只挡"一个字都没抽到"，不再重复高门槛 —— 否则短快讯又被杀一次
    if not body:
        raise FetchError("抽取不到有效正文（可能是 JS 渲染页）")
    return body[:MAX_BODY_CHARS]


def _fetch_notice_via_api(url: str) -> str:
    """东财公告详情页 → 正文 JSON API。

    页面本身是 JS 渲染（静态 HTML 只有"数据来源：Choice 郑重声明"页脚，
    直接抽会把免责声明当正文喂给模型），所以走 np-cnotice-stock 的公开 API。
    长公告分页，取前 3 页（对阅读理解足够；公告主体信息都在前几页）。
    """
    import re
    # [0-9A-Za-z]+ 而不是 [^/?#]+：后者会把 ".html" 一起吞进 art_code
    m = re.search(r"/notices/detail/[^/]+/(AN[0-9A-Za-z]+)", url)
    if not m:
        return ""
    art_code = m.group(1)
    parts: list[str] = []
    for page in (1, 2, 3):
        try:
            resp = requests.get(
                f"https://np-cnotice-stock.eastmoney.com/api/content/ann"
                f"?art_code={art_code}&client_source=web&page_index={page}",
                headers={**_HEADERS, "Referer": "https://data.eastmoney.com/"},
                timeout=FETCH_TIMEOUT_S)
            resp.raise_for_status()
            data = (resp.json() or {}).get("data") or {}
        except Exception:  # noqa: BLE001 —— API 抖动按"无内容"处理
            break
        parts.append(str(data.get("notice_content") or ""))
        try:
            total_pages = int(data.get("page_size") or 1)
        except (TypeError, ValueError):
            total_pages = 1
        if page >= total_pages:
            break
    return "\n".join(p for p in parts if p.strip())


def _looks_like_boilerplate(text: str) -> bool:
    """识别"抽到的其实是站点模板"：JS 渲染页的静态壳里只有页脚/免责声明。

    判据：包含东财页脚签名串且长度撑不过几行正文的量。宁可误杀一次重试，
    也不能把"郑重声明"当资讯正文存进库（2026-10-05 实测 158 条公告详情页全中）。
    """
    if not text:
        return True
    signature = "郑重声明"
    if signature not in text:
        return False
    return ("数据来源" in text or "东方财富网发布此信息" in text) and len(text) < 900


def _extract_pdf_text(payload: bytes) -> str:
    """PDF → 纯文本（研报正文）。pypdf 纯 Python 无系统依赖，作为可选依赖延迟导入。"""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # noqa: PERF203
        raise FetchError("服务器未安装 pypdf，无法解析 PDF 研报") from exc
    try:
        import io
        reader = PdfReader(io.BytesIO(payload))
        # 研报动辄几十页，先读前 12 页（覆盖摘要/核心观点），够做阅读理解
        parts = []
        for page in reader.pages[:12]:
            parts.append(page.extract_text() or "")
        text = "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001 —— 各种损坏 PDF 都见过
        raise FetchError(f"PDF 解析失败：{exc}") from exc
    if len(text) < MIN_BODY_CHARS:
        raise FetchError("PDF 里抽不出足够文本（可能是扫描件/图片版）")
    return text[:MAX_BODY_CHARS]
