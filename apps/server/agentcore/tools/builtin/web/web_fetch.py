"""Built-in tool: web_fetch (fetch a web page and extract its main text)."""

import contextlib
import json
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from agentcore.config import settings
from agentcore.core.citation_tier import stamp_citation_tier
from agentcore.core.error_codes import ErrorCode
from agentcore.core.logging import get_logger
from agentcore.core.net import (
    EgressError,
    PinnedAddressError,
    PinnedIPTransport,
    URLBlock,
    describe_net_error,
    outbound_async_client,
    site_of,
    web_timeout,
)
from agentcore.core.net import (
    classify_url as _classify_url,
)
from agentcore.core.task_cancel import raise_if_task_cancelled
from agentcore.core.types import ToolApproval, ToolCategory
from agentcore.tools.builtin.web._net import (
    circuit_remaining,
    note_failure,
    note_success,
    web_fetch_retire_message,
)
from agentcore.tools.builtin.web.github_page import try_fetch_github_page
from agentcore.tools.builtin.web.source_domains import default_source_domain_registry
from agentcore.tools.builtin.web.url_cache import (
    UrlCacheEntry,
    default_url_cache_registry,
)
from agentcore.tools.protocol import ToolContext, ToolResult, ToolSchema
from agentcore.tools.registration import (
    AUDIENCE_BOTH,
    ToolRegistration,
    ToolSurface,
)

logger = get_logger(__name__)

_DEFAULT_MAX_CHARS = 8000
_MAX_CHARS_CAP = 30000
_MAX_REDIRECTS = 5
_SNIPPET_MAX = 200  # citation preview length — a sentence or two, not the whole lead
# Slack over ``max_chars`` for the JSON envelope itself (url + title + keys/quotes).
# NOT slack for escape growth — that is budgeted by shaping ``content`` in
# :func:`_page_output`, since escaping is unbounded relative to the raw char count.
_OUTPUT_ENVELOPE_SLACK = 1024
# In-band truncation notice. A budget cut must be a stated fact, not a silent one:
# the model would otherwise read a short body as the whole page and claim full coverage.
_OUTPUT_TRUNCATED_NOTE = (
    "content 末尾已截断（正文经 JSON 转义后超出本次输出预算）：尾部内容缺失，"
    "勿据此断言已读全文；需要尾部信息时请换更聚焦的来源，或如实说明未覆盖。"
)
# Query-string length (chars) above which a read of a NOVEL domain is treated as a
# possible exfil beacon (PI-002): a fabricated ``?d=<secret>`` rides the query, while
# legitimate article URLs rarely carry a 64+ char opaque query. The query-length AND
# novel-domain conjunction keeps the common search→deep-read and plain-URL reads quiet.
_SUSPICIOUS_QUERY_LEN = 64
# Novel-domain exfil and loopback refusals are true policy blocks (tool is fine; call
# refused, and a reroute succeeds). SSRF / host-circuit / transport hard-fails are NOT
# policy_failure: continuous environmental failures must feed the run-scoped circuit
# breaker so research workers stop empty-spinning after stall→Wave retry (P1 2026-07-29).
_POLICY_FAILURE = "policy_failure"
# Shared stop-read trailer for hard-dead fetch classes (403/404/timeout/SSRF/egress).
# Points at existing materials — not «下一招再 web_search» (that fuels search thrash
# after deep-read death; retirement steer closes search separately).
_STOP_READ_HINT = (
    "——请停止对该来源换 URL / 同策略重试；基于已有材料收口写作，"
    "不要再空转外网深读。"
    "收口 ≠ 可伪精确逐步菜单：无现行可核证据时，后台点击路径须标「易变/待实测」+ 查找关键词"
)
# 401/403/429/451：同拒绝类失败的可执行约束（对齐 retire steer 语气；不拦换公开源）。
_ANTI_CRAWL_SAME_REJECT_HINT = (
    "【同拒绝类】勿对本 URL、同站点同策略再调 web_fetch；换公开可抓来源仍可用 web_fetch。"
)
# 回环地址不是「深读失败」，是「这个工具够不到，换个工具就能做」。贴 _STOP_READ_HINT
# （停止深读 / 基于已有材料收口）等于把模型推向放弃一次它换工具就能完成的验收——线上
# 就这么丢过一次。所以这里只给可执行改道，不给收口话术。
_LOOPBACK_REROUTE_HINT = (
    "。web_fetch 在服务端进程内发起请求，够不到用户本机上的服务："
    "换 URL、重试、改 UA 都读不到，这既不是出网受限，也不是放弃本次验收的理由。"
    "改道（二选一）：① 用 browser 工具打开该地址（浏览器跑在用户机器上）；"
    "② 用 terminal 在用户本机跑 `curl -sS <url>` 取内容。"
    "两者都不可用时，请用户把页面内容贴过来。"
)
# 工作区路径 / file:// / 盘符误喂 web_fetch：同构 loopback——工具没坏，换 file_read
# 就能做。贴收口话术会让模型放弃一次本可完成的读文件；补 https:// 会去抓公网
# （文件夹名长得像域名时更糟，例如工作区 `_scratch/zoogame.cc/`）。
_NOT_A_WEB_URL_REROUTE_HINT = (
    "。web_fetch 只接受 http/https 公网网页，不是读工作区文件的工具："
    "工作区相对路径、file://、盘符请改用 file_read(path=相对路径)。"
    "不要给本参数补 https:// 再调 web_fetch——那会去抓公网，读不到工作区文件。"
)

# --- 用户可见失败 code ---------------------------------------------------------
# ``metadata["code"]`` keys the curated user sentence (runtime/engine/tool_failure_face);
# ``error`` above stays the model's imperative steer. Two channels, one classification —
# an uncoded path collapses into a single info-free sentence for the user, which is what
# every web_fetch failure used to do regardless of cause.
_CODE_LOOPBACK_HOST = "loopback_host"
_CODE_NOT_A_WEB_URL = "not_a_web_url"
_CODE_PRIVATE_IP = "private_address_blocked"

_BLOCK_CODES: dict[URLBlock, str] = {
    URLBlock.BAD_SCHEME: _CODE_NOT_A_WEB_URL,
    URLBlock.BLOCKED_HOST: "blocked_host",
    URLBlock.LOOPBACK_HOST: _CODE_LOOPBACK_HOST,
    URLBlock.DNS_FAIL: "dns_resolve_failed",
    URLBlock.PRIVATE_IP: _CODE_PRIVATE_IP,
    URLBlock.PRIVATE_IP_FAKE_PROXY: "fake_ip_proxy_blocked",
}


def _query_len(url: str) -> int:
    """Length of the URL's query component (exfil-bandwidth proxy); 0 if unparseable."""
    try:
        return len(urlparse(url).query or "")
    except ValueError:
        return 0


class BlockedRedirectError(ValueError):
    """A hop failed the per-hop SSRF re-check in :func:`_safe_request`.

    Stays a ``ValueError`` with the original message so ``download_url``'s prefix branch
    keeps working; ``block`` carries the reason so the failure code is read off the
    classification instead of re-parsed from the text.
    """

    def __init__(self, block: URLBlock) -> None:
        super().__init__("URL blocked: private/internal network")
        self.block = block


class TooManyRedirectsError(ValueError):
    """The redirect chain outran ``max_redirects`` (message unchanged for callers)."""

    def __init__(self) -> None:
        super().__init__("Too many redirects")


def _failure_face(e: BaseException) -> tuple[str, str]:
    """Model-facing steer + stable user-facing code for one failed fetch.

    Two channels off a single classification: ``hint`` is the imperative appended to
    ``error`` (model only), ``code`` keys the user's curated sentence. Hard-dead classes
    (anti-crawl / 404 / timeout / connect / host-circuit / pinned SSRF) get the stop-read
    steer — retries and URL thrashing do not help — and COUNT toward the run circuit
    breaker (no ``policy_failure``), so Wave/contract restarts still trip disable.
    """
    if isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status in (401, 403, 429, 451):
            # Anti-crawl / auth wall: keep stop-read + no URL thrash, then name the
            # next workable moves (public source / summary close, or hand-brain paste
            # from the user). Never imply a logged-in scrape. Same-reject-class:
            # forbid same URL / same strategy; allow new public hosts.
            return (
                "。该站点反爬 / 拒绝访问，换 URL 或同策略重试都读不到"
                f"{_STOP_READ_HINT}"
                f"{_ANTI_CRAWL_SAME_REJECT_HINT}"
                "下一招：改查公开可抓来源，或直接基于已有摘要/材料收口；"
                "若必须站内数据，请用户自行打开页面后贴关键数字/截图（手脑），"
                "勿假装已登录抓取。",
                "site_access_denied",
            )
        if status == 404:
            return f"。页面不存在（404）{_STOP_READ_HINT}", ErrorCode.NOT_FOUND
        return _STOP_READ_HINT, "http_status_error"
    if isinstance(e, EgressError):
        return f"。出网受限或地址不可达{_STOP_READ_HINT}", "egress_circuit_open"
    if isinstance(e, PinnedAddressError):
        # Connect-time SSRF block (DNS rebinding): same reason ⇒ same code as pre-flight.
        return (
            f"。出网受限或地址不可达{_STOP_READ_HINT}",
            _BLOCK_CODES.get(e.block, _CODE_PRIVATE_IP) if e.block else _CODE_PRIVATE_IP,
        )
    if isinstance(e, BlockedRedirectError):
        return (
            f"。重定向目标被 SSRF 防护拦截{_STOP_READ_HINT}",
            _BLOCK_CODES.get(e.block, _CODE_PRIVATE_IP),
        )
    if isinstance(e, TooManyRedirectsError):
        return f"。重定向次数过多{_STOP_READ_HINT}", "too_many_redirects"
    if isinstance(e, (httpx.ConnectTimeout, httpx.ConnectError, httpx.NetworkError)):
        return f"。连接/读取失败{_STOP_READ_HINT}", "site_unreachable"
    if isinstance(e, httpx.TimeoutException):
        return f"。连接/读取失败{_STOP_READ_HINT}", "read_timeout"
    return _STOP_READ_HINT, ErrorCode.TOOL_ERROR


def _is_loopback_refusal(e: BaseException) -> bool:
    """True when the fetch died because the target is the user's own machine."""
    return getattr(e, "block", None) is URLBlock.LOOPBACK_HOST


def _is_not_a_web_url_refusal(e: BaseException) -> bool:
    """True when the target is not an http(s) URL (path / file:// / other scheme)."""
    return getattr(e, "block", None) is URLBlock.BAD_SCHEME


def _failed(error: str, start: float, *, code: str, policy: bool = False) -> ToolResult:
    """Failed ``ToolResult`` carrying the stable code (and optional policy marker)."""
    metadata: dict[str, Any] = {"code": code}
    if policy:
        metadata[_POLICY_FAILURE] = True
    return ToolResult(
        tool_call_id="",
        success=False,
        output="",
        error=error,
        duration_ms=int((time.monotonic() - start) * 1000),
        metadata=metadata,
    )


def _loopback_refusal(reason: str, start: float) -> ToolResult:
    """Refuse a local-machine target with a reroute instead of a stop-read.

    Deterministic policy refusal: no retry from this process can ever reach the address,
    but the *task* is still doable through a tool that runs on the user's machine. So it
    must not spend run-breaker budget, and must not carry ``_STOP_READ_HINT`` — that
    trailer is for public-web thrashing, and here it just tells the model to give up.
    """
    return _failed(
        f"{reason}{_LOOPBACK_REROUTE_HINT}",
        start,
        code=_CODE_LOOPBACK_HOST,
        policy=True,
    )


def _not_a_web_url_refusal(reason: str, start: float) -> ToolResult:
    """Refuse a non-http(s) target with a file_read reroute instead of a stop-read.

    Same posture as :func:`_loopback_refusal`: the tool is fine; this call used the
    wrong one. Must not spend run-breaker budget, and must not carry
    ``_STOP_READ_HINT``. Do not suggest prefixing ``https://`` — a workspace
    folder named like a domain is a file path, not a missing scheme.
    """
    return _failed(
        f"{reason}{_NOT_A_WEB_URL_REROUTE_HINT}",
        start,
        code=_CODE_NOT_A_WEB_URL,
        policy=True,
    )


async def _safe_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    max_redirects: int = _MAX_REDIRECTS,
    **kwargs: Any,
) -> httpx.Response:
    """逐跳重校验的请求：每个重定向目标都重新过 SSRF 检查。

    client 必须以 follow_redirects=False 创建，否则 httpx 会自动跟随、
    使得「公网 URL 302 到内网 IP」绕过检查。命中拦截或超跳数则抛 ValueError。

    出网韧性（受限环境）：按原始请求主机做熔断——近期连续传输失败的主机会被
    临时短路（抛 EgressError 快速失败，不再空耗整个超时窗口）；传输失败计入熔断、
    成功则清零。仅传输层错误（连接/超时/网络）计数，HTTP 4xx/5xx 由调用方处理、
    不视为出网故障。

    逐跳走模块级 ``_classify_url``（``core.net`` 导入的别名），既保住拒绝原因、
    也让 ``test_web_tools`` 对 ``web_fetch._classify_url`` 的 monkeypatch 仍生效。
    """
    request = client.build_request(method, url, **kwargs)
    host = (request.url.host or "").lower()
    remaining = circuit_remaining(host)
    if remaining > 0:
        raise EgressError(
            f"站点 {host} 近期连续访问失败，已临时熔断约 {int(remaining)}s"
            "（出网受限或站点不可达），暂不重试"
        )
    for _ in range(max_redirects + 1):
        hop_block = await _classify_url(str(request.url))
        if hop_block is not None:
            raise BlockedRedirectError(hop_block)
        try:
            resp = await client.send(request)
        except httpx.TimeoutException:
            note_failure(host)
            raise
        except httpx.NetworkError as e:
            # SSRF pin blocks are policy, not transport — must not open the per-host breaker.
            if not isinstance(e, PinnedAddressError):
                note_failure(host)
            raise
        nxt = resp.next_request
        if resp.is_redirect and nxt is not None:
            await resp.aclose()
            request = nxt
            continue
        note_success(host)
        return resp
    raise TooManyRedirectsError


class _TextExtractor(HTMLParser):
    """Minimal stdlib HTML→text extractor: drops scripts/styles, keeps the title,
    and inserts newlines at block boundaries (no third-party dependency)."""

    # Drop scripts/styles plus page chrome (nav/header/footer/aside) so both the
    # extracted body text and the fallback citation snippet skip boilerplate menus
    # and footers instead of leading with a navigation bar.
    SKIP_TAGS = frozenset(
        {
            "script",
            "style",
            "noscript",
            "svg",
            "head",
            "nav",
            "header",
            "footer",
            "aside",
        }
    )

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0
        self.title = ""
        self._in_title = False
        # First page-level description meta — a ready-made one-line summary, better
        # for a citation preview than the (often boilerplate-led) body text.
        self.description = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta" and not self.description:
            a = {k.lower(): (v or "") for k, v in attrs}
            key = a.get("name", "").lower() or a.get("property", "").lower()
            if key in ("description", "og:description", "twitter:description"):
                self.description = a.get("content", "").strip()

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in ("p", "div", "br", "li", "h1", "h2", "h3", "h4", "tr"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.title:
            self.title = data.strip()
        if self._skip_depth == 0:
            self.parts.append(data)


def _extract_page(html: str, max_chars: int) -> tuple[str, str, str]:
    """Return ``(title, text, description)`` from raw HTML; text capped to max_chars.

    ``description`` is the page's ``<meta>`` description (empty when absent) — used to
    seed a citation snippet so a read source still previews on hover.
    """
    extractor = _TextExtractor()
    with contextlib.suppress(Exception):
        extractor.feed(html)
    raw = "".join(extractor.parts)
    text = re.sub(r"\n{3,}", "\n\n", raw).strip()[:max_chars]
    return extractor.title, text, extractor.description


def _extract_text(html: str, max_chars: int) -> tuple[str, str]:
    """Back-compat ``(title, text)`` wrapper over :func:`_extract_page`."""
    title, text, _ = _extract_page(html, max_chars)
    return title, text


def _make_snippet(description: str, text: str) -> str:
    """A short citation preview: prefer the meta description, else the text lead.

    Whitespace is collapsed and the result capped to :data:`_SNIPPET_MAX` so the
    hover card shows a clean sentence-or-two, not a wall of body text.
    """
    source = description.strip() or text.strip()
    return re.sub(r"\s+", " ", source)[:_SNIPPET_MAX].strip()


def _page_output(*, url: str, title: str, text: str, limit: int) -> tuple[str, bool]:
    """Model-facing JSON whose **serialized** length fits ``limit``; ``(json, truncated)``.

    ``json.dumps`` escaping inflates the payload past the raw character count (每个换行
    ``\\n`` 变两字符；换行密集的列表/表格页轻松上千个), so budgeting on ``len(text)`` alone
    lets the dump blow past ``output_limit`` — and ``ToolResult`` then trims it with a
    character-level head+tail cut, handing the model a JSON object sliced through the
    middle while the result still reads as a success. So the budget is spent on the dump
    itself: shrink ``content`` until the serialized form fits, and declare the cut in-band
    (``truncated`` + ``note``) instead of letting the envelope be chopped.
    """

    def _dump(body: str, truncated: bool) -> str:
        payload: dict[str, Any] = {"url": url, "title": title, "content": body}
        if truncated:
            payload["truncated"] = True
            payload["note"] = _OUTPUT_TRUNCATED_NOTE
        return json.dumps(payload, ensure_ascii=False)

    output = _dump(text, False)
    if len(output) <= limit:
        return output, False
    # Largest prefix whose dump still fits (dump length is monotonic in prefix length).
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if len(_dump(text[:mid], True)) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return _dump(text[:lo], True), True


def _make_display(
    *,
    url: str,
    title: str,
    site: str,
    snippet: str,
    content: str,
) -> dict[str, Any]:
    """Render-oriented twin of the model-facing JSON output (工具结果富渲染).

    The desktop shows a source-style card header (favicon · title · site) plus a
    body preview from ``content`` — same display channel as ``web_search``, so the
    client never parses the JSON ``output`` string.
    """
    return {
        "url": url,
        "title": title,
        "site": site,
        "snippet": snippet,
        "content": content,
    }


class WebFetchTool:
    """Fetch a web page and return its extracted main text."""

    registration = ToolRegistration(
        surface=ToolSurface.BUILTIN,
        audience=AUDIENCE_BOTH,
    )

    @staticmethod
    def _guard_novel_domain_exfil(url: str, conversation_id: str) -> str | None:
        """Observe (and, under the opt-in flag, refuse) the novel-domain exfil pattern.

        Indirect prompt injection can drive web_fetch to ``https://attacker/?d=<secret>``
        — the SSRF guard blocks only INTERNAL targets, so a public exfil URL passes. The
        deterministic tell: a legitimate deep-read targets a domain ``web_search``
        surfaced this conversation, while an exfil URL is a model-fabricated NOVEL domain
        carrying a long opaque query (the secret). When both hold, log it (always, for
        observability) and refuse it when ``web_fetch_block_novel_query`` is on.

        Returns an honest model-facing error string to BLOCK, else ``None`` (allow).
        Skipped for unscoped calls (no per-conversation source set to compare against)
        and for short / absent query strings (no meaningful exfil bandwidth) — so the
        common search→deep-read and plain-URL reads are untouched. Path-based exfil to a
        novel domain with no query is a known residual gap (closing it needs novel-domain
        approval, a heavier UX trade-off — 项目审计-提示注入专项 §五 PI-002).
        """
        if not conversation_id:
            return None
        query_len = _query_len(url)
        if query_len < _SUSPICIOUS_QUERY_LEN:
            return None
        domain = site_of(url)
        if not domain:
            return None
        if default_source_domain_registry().has_domain(conversation_id, domain):
            return None
        logger.warning(
            "tool.web_fetch_novel_domain",
            url=url[:200],
            site=domain,
            query_len=query_len,
            conversation_id=conversation_id,
            blocked=settings.web_fetch_block_novel_query,
        )
        if settings.web_fetch_block_novel_query:
            return (
                "[ERROR] 该链接指向本会话检索结果之外的新域名且携带较长查询参数，"
                "已按出网外泄防护拦截。如确需读取该来源，请先用 web_search 找到它，"
                "或请用户直接提供链接。"
            )
        return None

    @property
    def schema(self) -> ToolSchema:
        return ToolSchema(
            name="web_fetch",
            description=(
                "仅 http/https 公网网页正文。工作区相对路径、file://、盘符路径用 file_read，"
                "不要把路径传给本工具，也不要补 https:// 冒充网页。"
                "获取指定网页的正文文本（比 web_search 摘要更完整，但长页面会按 "
                "max_chars 截断），用于在 web_search 摘要不足、确需深读某条结果时。"
                "默认摘要优先：多数问题先用 web_search 摘要作答；"
                "任务要求核对原文或需要正文细节时再调用本工具深读。"
                "要把 URL 的原始文件/二进制写入工作区时用 download_url，不要用本工具。"
                "注意：部分大型站点（如百度百科、知乎等）有反爬保护，可能返回 403/失败——"
                "此时改用 web_search 摘要或换其他来源，不要对同一被拒站点反复重试。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": (
                            "必须是 http:// 或 https:// 开头的公网网页地址。"
                            "工作区文件用 file_read。"
                        ),
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": "返回的最大字符数，默认 8000",
                    },
                },
                "required": ["url"],
            },
            category=ToolCategory.RESEARCH,
            approval=ToolApproval.NEVER,
        )

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        start = time.monotonic()
        url = (arguments.get("url") or "").strip()
        if not url:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error="缺少必填参数：url",
                duration_ms=0,
                metadata={"code": ErrorCode.VALIDATION_ERROR},
            )

        # Run-scoped retirement (survives react_loop restart after stream-stall /
        # Wave retry): refuse without fetching and re-trip the loop breaker.
        retire_msg = web_fetch_retire_message(context.run_id)
        if retire_msg:
            return ToolResult(
                tool_call_id="",
                success=False,
                output="",
                error=f"网页读取失败：{retire_msg}",
                duration_ms=int((time.monotonic() - start) * 1000),
                metadata={
                    "code": "web_fetch_retired",
                    "retire_tools": ["web_fetch"],
                    "error_class": "permanent",
                    # CircuitBreak.message() prefixes ``[系统提示]`` once.
                    "retire_message": retire_msg,
                },
            )

        block = await _classify_url(url)
        if block is not None:
            if block is URLBlock.LOOPBACK_HOST:
                return _loopback_refusal(block.value, start)
            if block is URLBlock.BAD_SCHEME:
                return _not_a_web_url_refusal(block.value, start)
            # SSRF / DNS / blocked-host: count toward the run breaker (not policy_failure)
            # so consecutive environmental refusals hard-stop empty URL thrashing.
            return _failed(
                f"{block.value}{_STOP_READ_HINT}",
                start,
                code=_BLOCK_CODES.get(block, ErrorCode.TOOL_ERROR),
            )

        try:
            raw_max = int(arguments.get("max_chars", _DEFAULT_MAX_CHARS))
            max_chars = max(1, min(raw_max, _MAX_CHARS_CAP))
        except (TypeError, ValueError):
            max_chars = _DEFAULT_MAX_CHARS

        # Conversation-scoped fetch cache: a repeat read of the same page within the
        # conversation is served from memory (within a freshness TTL) instead of
        # re-fetching. Only successful fetches are cached, so a hit's URL already
        # passed the SSRF gate above; unscoped call sites (conversation_id == "")
        # skip the cache entirely.
        cache = (
            default_url_cache_registry().get_or_create(context.conversation_id)
            if context.conversation_id
            else None
        )
        if cache is not None:
            cached = cache.get(url, min_chars=max_chars)
            if cached is not None:
                text = cached.content[:max_chars]
                limit = max_chars + _OUTPUT_ENVELOPE_SLACK
                output, output_truncated = _page_output(
                    url=url, title=cached.title, text=text, limit=limit
                )
                logger.info("tool.web_fetch_cache_hit", url=url, content_chars=len(text))
                return ToolResult(
                    tool_call_id="",
                    success=True,
                    output=output,
                    duration_ms=int((time.monotonic() - start) * 1000),
                    # ``_page_output`` already fit the dump to ``limit``; the max() only
                    # covers the degenerate envelope-over-budget case (very long url /
                    # title) so a chopped, unparseable JSON can never reach the model.
                    output_limit=max(limit, len(output)),
                    metadata={
                        "title": cached.title,
                        "content_chars": len(text),
                        "cached": True,
                        "output_truncated": output_truncated,
                    },
                    citations=[
                        stamp_citation_tier(
                            {
                                "url": url,
                                "title": cached.title,
                                "snippet": cached.snippet,
                                "site": cached.site,
                                "deep_read": True,
                            }
                        )
                    ],
                    display=_make_display(
                        url=url,
                        title=cached.title,
                        site=cached.site,
                        snippet=cached.snippet,
                        content=text,
                    ),
                )

        # PI-002 出网外泄观测：only reached on a cache MISS (a real outbound fetch is about
        # to happen). A model-fabricated novel domain carrying a long opaque query is the
        # indirect-injection exfil tell — always logged, refused only under the opt-in flag.
        exfil_block = self._guard_novel_domain_exfil(url, context.conversation_id)
        if exfil_block is not None:
            return _failed(exfil_block, start, code="novel_domain_blocked", policy=True)

        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; AgentCore/1.0; +https://agentcore.dev)",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        # 工具执行阶段进度 (联网前端展示优化): signal the slow blocking leg so the waiting row
        # is live, not a dead spinner — and stay honest about egress control. If THIS host's
        # circuit is OPEN, ``_safe_request`` is about to fast-fail (EgressError), NOT queue —
        # so report「出网受限·快速失败」(blocked) rather than a fake「正在抓取」/「排队」; web_fetch
        # has no token-bucket/semaphore wait, so it never has a real「排队」state. Otherwise the
        # fetch proceeds → 「正在抓取网页」. Best-effort; ``on_phase`` is None on unscoped call
        # sites (tests / evals). Enforcement stays in ``_safe_request`` (single source of truth);
        # this is an observe-only mirror of the same breaker state.
        if context.on_phase:
            host = (urlparse(url).hostname or "").lower()
            context.on_phase("blocked" if circuit_remaining(host) > 0 else "fetching")
        used_github_api = False
        try:
            # PinnedIPTransport: connect to the IP we validated, closing the DNS-rebinding
            # TOCTOU between the per-hop classify_url check and httpx's own resolution.
            # verify=False: tolerate broken cert chains on gov/court/academic mirrors
            # (same posture as the favicon proxy). SSRF pinning still bounds which hosts
            # we reach; only TLS trust is relaxed.
            async with outbound_async_client(
                timeout=web_timeout(),
                follow_redirects=False,
                transport=PinnedIPTransport(verify=False),
            ) as client:
                # github.com/{owner} (profile / ?tab=repositories) and
                # github.com/{owner}/{repo} (root/tree/blob): prefer api.github.com so a
                # HTML JS shell / connect timeout is not misread as "private repo".
                # Match miss or API failure → HTML path below (same SSRF/breaker).
                github_page = await try_fetch_github_page(
                    client,
                    url,
                    max_chars,
                    safe_request=_safe_request,
                    user_id=context.user_id,
                )
                if github_page is not None:
                    title, text, description = github_page
                    used_github_api = True
                else:
                    resp = await _safe_request(client, "GET", url, headers=headers)
                    resp.raise_for_status()
                    html = resp.text
                    # 工具执行阶段进度: fetched — now parse/extract the main text (可感知的第二段，
                    # 长页面抽取有耗时). Signals「正在提取正文」.
                    if context.on_phase:
                        context.on_phase("reading")
                    title, text, description = _extract_page(html, max_chars)
        except Exception as e:
            raise_if_task_cancelled(e)
            reason = describe_net_error(e)
            err_fields: dict[str, Any] = {
                "url": url,
                "error": reason,
                "error_repr": repr(e),
            }
            host = site_of(url)
            if host:
                err_fields["host"] = host
            logger.warning("tool.web_fetch_error", **err_fields)
            # A rebind / redirect that lands on the user's own machine is the same
            # situation as the pre-flight loopback refusal — reroute, do not close.
            if _is_loopback_refusal(e):
                return _loopback_refusal(f"网页读取失败：{reason}", start)
            if _is_not_a_web_url_refusal(e):
                return _not_a_web_url_refusal(f"网页读取失败：{reason}", start)
            hint, code = _failure_face(e)
            return _failed(f"网页读取失败：{reason}{hint}", start, code=code)

        # GitHub API path has no HTML extract leg; still advance the phase row once.
        if context.on_phase and used_github_api:
            context.on_phase("reading")
        snippet = _make_snippet(description, text)
        site = site_of(url)
        if cache is not None:
            cache.put(
                UrlCacheEntry(
                    url=url,
                    title=title,
                    content=text,
                    snippet=snippet,
                    site=site,
                    max_chars=max_chars,
                    truncated=len(text) >= max_chars,
                    stored_at=time.time(),
                )
            )
        limit = max_chars + _OUTPUT_ENVELOPE_SLACK
        output, output_truncated = _page_output(url=url, title=title, text=text, limit=limit)
        return ToolResult(
            tool_call_id="",
            success=True,
            output=output,
            duration_ms=int((time.monotonic() - start) * 1000),
            # See the cache-hit branch: the dump is already budget-shaped, the max() only
            # guards the degenerate envelope-over-budget case.
            output_limit=max(limit, len(output)),
            metadata={
                "title": title,
                "content_chars": len(text),
                "output_truncated": output_truncated,
            },
            citations=[
                stamp_citation_tier(
                    {
                        "url": url,
                        "title": title,
                        "snippet": snippet,
                        "site": site,
                        "deep_read": True,
                    }
                )
            ],
            display=_make_display(
                url=url,
                title=title,
                site=site,
                snippet=snippet,
                content=text,
            ),
        )
