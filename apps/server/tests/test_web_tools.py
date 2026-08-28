"""Tests for the web tools (search backend, SSRF guard, extraction, breaker).

Pure logic + offline paths only — no real network. SSRF rejection is verified by
calling ``read_url`` against private/blocked hosts (classification short-circuits
before any request), and search parsing is tested via the pure ``_parse_results``.
"""

import asyncio
import json
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

from agentcore.core.net import (
    EgressError,
    describe_net_error,
    site_of,
)
from agentcore.core.net import (
    URLBlock as _URLBlock,
)
from agentcore.core.net import (
    classify_url as _classify_url,
)
from agentcore.core.net import (
    ip_is_safe as _ip_is_safe,
)
from agentcore.core.net import (
    is_fake_ip_proxy_signature as _is_fake_ip_proxy_signature,
)
from agentcore.core.net import (
    private_ip_block as _private_ip_block,
)
from agentcore.runtime.citations import annotate_tool_citations, merge_citations
from agentcore.tools.builtin.web import _net
from agentcore.tools.builtin.web import read_url as read_url_mod
from agentcore.tools.builtin.web import search as search_mod
from agentcore.tools.builtin.web import search_backend as search_backend_mod
from agentcore.tools.builtin.web import search_cache as search_cache_mod
from agentcore.tools.builtin.web import url_cache as url_cache_mod
from agentcore.tools.builtin.web._net import (
    circuit_remaining,
    note_failure,
    note_success,
)
from agentcore.tools.builtin.web.read_url import (
    ReadUrlTool,
    _extract_page,
    _extract_text,
    _make_snippet,
)
from agentcore.tools.builtin.web.search import (
    _QUERY_ABSOLUTE_CHAR_LIMIT,
    _QUERY_CJK_CHAR_LIMIT,
    _QUERY_LATIN_WORD_LIMIT,
    _QUERY_LATIN_WORD_WEIGHT,
    WebSearchTool,
    prepare_search_query,
    validate_search_query,
)
from agentcore.tools.builtin.web.search_backend import (
    FallbackSearchBackend,
    SearchResult,
    SearXNGBackend,
    TavilyBackend,
    _parse_results,
    infer_search_language,
    track_phase_durations,
)
from agentcore.tools.builtin.web.search_cache import (
    ConversationSearchCache,
    SearchCacheEntry,
    SearchCacheRegistry,
    _query_key,
)
from agentcore.tools.builtin.web.url_cache import (
    ConversationUrlCache,
    UrlCacheEntry,
    UrlCacheRegistry,
)
from agentcore.tools.protocol import ToolContext, ToolResult
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(
    conversation_id: str = "", on_phase=None, *, run_id: str = "s"
) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id=run_id,
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id=conversation_id,
        on_phase=on_phase,
    )


# --- search_backend._parse_results ---


def test_parse_results_maps_fields_and_caps():
    data = {
        "results": [
            {"title": "A", "url": "https://a.com", "content": "snip a"},
            {"title": "B", "url": "https://b.com", "content": "snip b"},
            {"title": "C", "url": "https://c.com", "content": "snip c"},
        ]
    }
    out = _parse_results(data, max_results=2)
    assert len(out) == 2
    assert out[0].title == "A"
    assert out[0].url == "https://a.com"
    assert out[0].snippet == "snip a"


def test_parse_results_dedups_normalized_url():
    data = {
        "results": [
            {"title": "A", "url": "https://a.com/page", "content": "1"},
            {"title": "A dup", "url": "https://a.com/page/#section", "content": "2"},
            {"title": "B", "url": "https://b.com", "content": "3"},
        ]
    }
    out = _parse_results(data, max_results=10)
    assert [r.url for r in out] == ["https://a.com/page", "https://b.com"]


def test_parse_results_skips_incomplete_entries():
    data = {
        "results": [
            {"title": "", "url": "https://a.com", "content": "x"},
            {"title": "B", "url": "", "content": "y"},
            {"title": "C", "url": "https://c.com", "content": None},
        ]
    }
    out = _parse_results(data, max_results=10)
    assert len(out) == 1
    assert out[0].url == "https://c.com"
    assert out[0].snippet == ""


def test_parse_results_accepts_cloud_snippet_field():
    """Cloud ``/v1/inference/web_search`` returns ``snippet``, not ``content``."""
    data = {
        "results": [
            {"title": "A", "url": "https://a.com", "snippet": "cloud snip a"},
            {"title": "B", "url": "https://b.com", "snippet": "cloud snip b"},
        ]
    }
    out = _parse_results(data, max_results=10)
    assert [r.snippet for r in out] == ["cloud snip a", "cloud snip b"]


def test_parse_results_prefers_content_over_snippet():
    data = {
        "results": [
            {
                "title": "A",
                "url": "https://a.com",
                "content": "from content",
                "snippet": "from snippet",
            },
            {"title": "B", "url": "https://b.com", "content": "", "snippet": "only snippet"},
        ]
    }
    out = _parse_results(data, max_results=10)
    assert [r.snippet for r in out] == ["from content", "only snippet"]


# --- _net: circuit breaker + error description ---


def test_circuit_breaker_trips_after_threshold():
    host = "breaker-test.example"
    _net._states.pop(host, None)
    for _ in range(_net.WEB_HOST_FAIL_THRESHOLD - 1):
        note_failure(host)
    assert circuit_remaining(host) == 0.0
    note_failure(host)  # threshold hit
    assert circuit_remaining(host) > 0.0
    note_success(host)
    assert circuit_remaining(host) == 0.0


def test_describe_net_error_is_honest():
    assert "连接超时" in describe_net_error(httpx.ConnectTimeout(""))
    assert "读取超时" in describe_net_error(httpx.ReadTimeout(""))
    assert describe_net_error(EgressError("已熔断")) == "已熔断"

    req = httpx.Request("GET", "https://x.com")
    err = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(403, request=req))
    assert describe_net_error(err) == "HTTP 403"


def test_describe_net_error_loopback_searxng_vs_public():
    # Loopback / configured SearXNG connect failure → 本地搜索不可用 (not 出网受限;
    # no docker compose teaching toward the model).
    local = describe_net_error(
        httpx.ConnectError("connection refused"),
        url="http://127.0.0.1:18888/search",
    )
    assert "本地搜索" in local and "不可用" in local
    assert "出网受限" not in local
    assert "docker" not in local.lower()
    assert "compose" not in local.lower()

    localhost = describe_net_error(
        httpx.ConnectTimeout(""),
        url="http://localhost:18888",
    )
    assert "本地搜索" in localhost and "不可用" in localhost
    assert "出网受限" not in localhost
    assert "docker" not in localhost.lower()

    # Public target keeps the egress-restricted copy (read_url / favicon path).
    public = describe_net_error(
        httpx.ConnectError("connection refused"),
        url="https://example.com/page",
    )
    assert "出网受限" in public
    assert "本地搜索" not in public

    # Bare ConnectError with no url/request stays public (callers must pass url /
    # local_service for SearXNG — see describe_searxng_error).
    bare = describe_net_error(httpx.ConnectError("connection refused"))
    assert "出网受限" in bare
    assert "本地搜索" not in bare

    # Real httpx attaches request.url — loopback is detected without an explicit url=.
    req = httpx.Request("GET", "http://127.0.0.1:18888/search")
    from_req = describe_net_error(httpx.ConnectError("refused", request=req))
    assert "本地搜索" in from_req and "出网受限" not in from_req


def test_describe_searxng_error_marks_configured_host():
    # Non-loopback compose service name still gets local-search copy via local_service.
    msg = search_backend_mod.describe_searxng_error(
        httpx.ConnectError("connection refused"),
        base_url="http://searxng:8080",
    )
    assert "本地搜索" in msg and "不可用" in msg
    assert "出网受限" not in msg
    assert "docker" not in msg.lower()

    via_backend = search_backend_mod.describe_search_error(
        httpx.ConnectError("down"),
        SearXNGBackend("http://127.0.0.1:18888"),
    )
    assert "本地搜索" in via_backend


# --- read_url: SSRF classification ---


def test_ip_is_safe_blocks_private_and_metadata():
    assert _ip_is_safe("1.1.1.1") is True
    assert _ip_is_safe("8.8.8.8") is True
    assert _ip_is_safe("127.0.0.1") is False
    assert _ip_is_safe("10.0.0.1") is False
    assert _ip_is_safe("192.168.1.1") is False
    assert _ip_is_safe("169.254.169.254") is False  # cloud metadata
    assert _ip_is_safe("not-an-ip") is False


async def test_classify_url_bad_scheme():
    assert await _classify_url("ftp://example.com") is _URLBlock.BAD_SCHEME
    assert await _classify_url("file:///etc/passwd") is _URLBlock.BAD_SCHEME


async def test_classify_url_blocked_hosts():
    assert await _classify_url("http://foo.internal/") is _URLBlock.BLOCKED_HOST
    assert await _classify_url("http://db.local/") is _URLBlock.BLOCKED_HOST
    assert (
        await _classify_url("http://metadata.google.internal/") is _URLBlock.BLOCKED_HOST
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://localhost:5173/",
        "http://127.0.0.1:8000/",
        "http://127.1.2.3/",
        "http://[::1]:8080/",
        "http://0.0.0.0:3000/",
    ],
)
async def test_classify_url_loopback_family_is_one_reason(url: str):
    """本机地址是一个意图，不该分裂成 BLOCKED_HOST / PRIVATE_IP 两条路两句话。"""
    assert await _classify_url(url) is _URLBlock.LOOPBACK_HOST


async def test_classify_url_literal_private_ip():
    assert await _classify_url("http://169.254.169.254/latest/") is _URLBlock.PRIVATE_IP
    assert await _classify_url("http://10.1.2.3/") is _URLBlock.PRIVATE_IP


async def test_loopback_stays_in_the_clone_guard_reject_set():
    """拆出独立拒绝类型 ≠ 放宽边界：git clone 守卫按 PRIVATE_IP_BLOCKS 判，必须仍覆盖回环。"""
    from agentcore.core.net import PRIVATE_IP_BLOCKS

    assert _URLBlock.LOOPBACK_HOST in PRIVATE_IP_BLOCKS
    for url in ("http://localhost/o/r.git", "http://127.0.0.1/o/r.git"):
        assert await _classify_url(url) in PRIVATE_IP_BLOCKS


async def test_classify_url_public_literal_ip_ok():
    assert await _classify_url("https://1.1.1.1/") is None


def test_is_fake_ip_proxy_signature_detects_clash_placeholder():
    assert _is_fake_ip_proxy_signature("198.18.0.21") is True
    assert _is_fake_ip_proxy_signature("198.19.255.255") is True
    assert _is_fake_ip_proxy_signature("127.0.0.1") is False
    assert _is_fake_ip_proxy_signature("not-an-ip") is False


def test_private_ip_block_appends_fake_proxy_hint():
    assert _private_ip_block("10.0.0.1") is _URLBlock.PRIVATE_IP
    assert _private_ip_block("198.18.0.21") is _URLBlock.PRIVATE_IP_FAKE_PROXY
    assert _private_ip_block("10.0.0.1", "198.18.0.5") is _URLBlock.PRIVATE_IP_FAKE_PROXY


def test_private_ip_block_names_loopback_only_when_every_answer_is_local():
    assert _private_ip_block("127.0.0.1") is _URLBlock.LOOPBACK_HOST
    assert _private_ip_block("::1") is _URLBlock.LOOPBACK_HOST
    # Public + loopback in one answer is a rebinding attempt — keep it reading as SSRF.
    assert _private_ip_block("93.184.216.34", "127.0.0.1") is _URLBlock.PRIVATE_IP


async def test_classify_url_hostname_resolving_to_loopback(monkeypatch):
    """*.localhost / hosts 文件里的本地开发域名：走 DNS 也归到同一个回环理由。"""

    async def _fake_dns(_host, _port=None):
        return ["127.0.0.1"]

    import agentcore.core.net as net

    monkeypatch.setattr(net, "_getaddrinfo", _fake_dns)
    assert await _classify_url("http://app.test:5173/") is _URLBlock.LOOPBACK_HOST


async def test_classify_url_fake_ip_proxy_signature(monkeypatch):
    async def _fake_dns(_host, _port=None):
        return ["198.18.0.21"]

    import agentcore.core.net as net
    from agentcore.config import settings

    monkeypatch.setattr(settings, "read_url_allow_fake_ip_proxy", False)
    monkeypatch.setattr(net, "_getaddrinfo", _fake_dns)
    block = await _classify_url("https://www.example.com/")
    assert block is _URLBlock.PRIVATE_IP_FAKE_PROXY
    assert "fake-IP" in block.value
    assert "probe_egress.py" in block.value


async def test_classify_url_allows_fake_ip_when_configured(monkeypatch):
    async def _fake_dns(_host, _port=None):
        return ["198.18.0.21"]

    import agentcore.core.net as net
    from agentcore.config import settings

    monkeypatch.setattr(settings, "read_url_allow_fake_ip_proxy", True)
    monkeypatch.setattr(net, "_getaddrinfo", _fake_dns)
    assert await _classify_url("https://arxiv.org/abs/1") is None


async def test_ip_is_safe_allows_fake_ip_when_configured(monkeypatch):
    from agentcore.config import settings

    monkeypatch.setattr(settings, "read_url_allow_fake_ip_proxy", True)
    assert _ip_is_safe("198.18.0.21") is True
    assert _ip_is_safe("127.0.0.1") is False


async def test_read_url_fake_ip_proxy_shows_environment_hint(monkeypatch):
    async def _fake_classify(_url):
        return _URLBlock.PRIVATE_IP_FAKE_PROXY

    from agentcore.config import settings

    monkeypatch.setattr(settings, "read_url_allow_fake_ip_proxy", False)
    monkeypatch.setattr(read_url_mod, "_classify_url", _fake_classify)
    result = await ReadUrlTool().execute({"url": "https://www.example.com/"}, _ctx())
    assert result.success is False
    assert "fake-IP" in result.error
    assert "probe_egress.py" in result.error
    # SSRF / fake-IP counts toward the run circuit breaker (not policy_failure) so
    # consecutive environmental refusals hard-stop research empty-spins.
    assert result.metadata.get("policy_failure") is not True
    assert "停止" in result.error or "收口" in result.error


# --- read_url: HTML extraction ---


def test_extract_text_strips_scripts_and_keeps_title():
    html = (
        "<html><head><title>Hello</title>"
        "<style>.x{color:red}</style></head>"
        "<body><script>var a=1;</script>"
        "<p>First para</p><p>Second para</p></body></html>"
    )
    title, text = _extract_text(html, max_chars=1000)
    assert title == "Hello"
    assert "var a=1" not in text
    assert "color:red" not in text
    assert "First para" in text
    assert "Second para" in text


def test_extract_text_truncates():
    html = "<body><p>" + ("x" * 500) + "</p></body>"
    _title, text = _extract_text(html, max_chars=100)
    assert len(text) == 100


def test_extract_text_drops_nav_header_footer_chrome():
    html = (
        "<body><nav>首页 登录 注册</nav><header>站点横幅</header>"
        "<p>真正的正文段落</p>"
        "<footer>版权所有 联系我们</footer><aside>相关推荐</aside></body>"
    )
    _title, text = _extract_text(html, max_chars=1000)
    assert "真正的正文段落" in text
    assert "登录" not in text
    assert "站点横幅" not in text
    assert "版权所有" not in text
    assert "相关推荐" not in text


# --- read_url: meta description seeds citation snippet ---


def test_extract_page_reads_meta_description():
    html = (
        "<html><head><title>T</title>"
        '<meta name="description" content="  Page summary here.  ">'
        "</head><body><p>Body lead</p></body></html>"
    )
    title, text, description = _extract_page(html, max_chars=1000)
    assert title == "T"
    assert "Body lead" in text
    assert description == "Page summary here."


def test_extract_page_reads_og_and_twitter_description():
    og = '<meta property="og:description" content="OG summary">'
    assert _extract_page(f"<head>{og}</head>", 1000)[2] == "OG summary"
    tw = '<meta name="twitter:description" content="TW summary">'
    assert _extract_page(f"<head>{tw}</head>", 1000)[2] == "TW summary"


def test_extract_page_no_description_is_empty():
    html = "<html><head><title>T</title></head><body><p>x</p></body></html>"
    assert _extract_page(html, 1000)[2] == ""


def test_make_snippet_prefers_description_over_text():
    assert _make_snippet("  the meta desc ", "body text lead") == "the meta desc"


def test_make_snippet_falls_back_to_text_lead():
    assert _make_snippet("", "  body  lead   text ") == "body lead text"


def test_make_snippet_collapses_whitespace_and_caps_length():
    out = _make_snippet("word " * 100, "")  # 500 chars before collapse
    assert len(out) <= 200
    assert "  " not in out  # whitespace collapsed to single spaces


async def test_read_url_emits_citation_snippet_from_description(monkeypatch):
    html = (
        "<html><head><title>深圳天气</title>"
        '<meta name="description" content="今天多云转晴，气温 20-28 度。">'
        "</head><body><nav>导航</nav><p>正文内容</p></body></html>"
    )

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://weather.example.com/sz"}, _ctx())

    assert result.success is True
    assert result.citations is not None
    cite = result.citations[0]
    assert cite["url"] == "https://weather.example.com/sz"
    assert cite["title"] == "深圳天气"
    assert cite["site"] == "weather.example.com"
    assert cite["deep_read"] is True
    assert cite["snippet"] == "今天多云转晴，气温 20-28 度。"
    # 工具结果富渲染: display carries the same source fields + body so the client
    # never parses the model-facing JSON output.
    assert result.display == {
        "url": "https://weather.example.com/sz",
        "title": "深圳天气",
        "site": "weather.example.com",
        "snippet": "今天多云转晴，气温 20-28 度。",
        "content": "正文内容",
    }


async def test_read_url_emits_fetching_then_reading_phases(monkeypatch):
    # 工具执行阶段进度 (联网前端展示优化): read_url signals 抓取→提取 while its blocking fetch
    # + parse legs run, so the waiting row is live (正在抓取网页 → 正在提取正文) not a dead spinner.
    from agentcore.tools.builtin.web import _net as net_mod

    monkeypatch.setattr(net_mod, "_states", {})  # closed circuit → the 抓取 branch, not blocked
    html = "<html><head><title>T</title></head><body><p>正文</p></body></html>"

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    phases: list[str] = []
    result = await ReadUrlTool().execute(
        {"url": "https://x.example.com/a"}, _ctx(on_phase=phases.append)
    )
    assert result.success is True
    assert phases == ["fetching", "reading"]


async def test_read_url_circuit_open_emits_blocked_phase(monkeypatch):
    # 出网熔断是真实的「快速失败」瞬时态（read_url 无令牌桶/信号量，没有真实「排队」）：诚实报
    # blocked（出网受限·快速失败），随即照常由 ``_safe_request`` 的熔断器快速失败——不虚构 queued。
    from agentcore.tools.builtin.web import _net as net_mod

    monkeypatch.setattr(net_mod, "_states", {})
    host = "blocked.example.com"
    for _ in range(net_mod.WEB_HOST_FAIL_THRESHOLD):
        net_mod.note_failure(host)

    async def _allow(_url: str):
        return None

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)

    phases: list[str] = []
    result = await ReadUrlTool().execute(
        {"url": "https://blocked.example.com/a"}, _ctx(on_phase=phases.append)
    )
    assert result.success is False  # fast-failed via the per-host egress circuit breaker
    assert phases == ["blocked"]  # honest block, never a fake fetching/reading/queued


async def test_read_url_cache_hit_emits_no_phase(monkeypatch):
    # A served-from-cache read does no outbound fetch, so it emits no phase (nothing to wait on).
    html = "<html><head><title>T</title></head><body><p>正文</p></body></html>"

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    tool = ReadUrlTool()
    # Prime the conversation cache with a first (real) read, then the second is served hot.
    await tool.execute(
        {"url": "https://x.example.com/a"}, _ctx(conversation_id="conv-phase-cache")
    )
    phases: list[str] = []
    r2 = await tool.execute(
        {"url": "https://x.example.com/a"},
        _ctx(conversation_id="conv-phase-cache", on_phase=phases.append),
    )
    assert r2.success is True
    assert r2.metadata.get("cached") is True
    assert phases == []


async def test_read_url_snippet_falls_back_to_body_when_no_meta(monkeypatch):
    html = "<html><head><title>无摘要页</title></head><body><p>正文第一段</p></body></html>"

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://x.example.com/a"}, _ctx())

    assert result.success is True
    assert result.citations is not None
    assert result.citations[0]["snippet"] == "正文第一段"


# --- tool execute: offline rejection paths ---


async def test_read_url_rejects_private_without_network():
    result = await ReadUrlTool().execute({"url": "http://10.1.2.3:9999/"}, _ctx())
    assert result.success is False
    assert _URLBlock.PRIVATE_IP.value in (result.error or "")
    assert result.metadata.get("code") == "private_address_blocked"
    # Environmental SSRF refusals count toward the run breaker (not policy_failure).
    assert result.metadata.get("policy_failure") is not True
    assert "收口" in (result.error or "") or "停止" in (result.error or "")


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1:9999/", "http://localhost:5173/health", "http://0.0.0.0:8080/"]
)
async def test_read_url_loopback_reroutes_instead_of_closing(url: str):
    """本机地址：给可执行改道（browser / terminal curl），不给「停止深读、收口写作」。"""
    result = await ReadUrlTool().execute({"url": url}, _ctx())
    err = result.error or ""

    assert result.success is False  # 边界不变：仍然拒绝
    assert result.metadata.get("code") == "loopback_host"
    # 确定性策略拒绝，改道即可成功 —— 不烧 run 熔断额度。
    assert result.metadata.get("policy_failure") is True
    # 那条为公网空转设计的收口话术不能出现在本机地址上。
    assert "不要再空转外网深读" not in err
    assert "基于已有材料收口写作" not in err
    # 改道指引必须点名两个真跑在用户机器上的工具。
    assert "browser" in err
    assert "terminal" in err and "curl" in err


async def test_read_url_loopback_refusal_survives_dns_rebinding(monkeypatch):
    """连接时才暴露的回环（DNS 重绑 / 本地开发域名）走同一条改道，不是「出网受限」。"""
    from agentcore.core.net import PinnedAddressError

    async def _allow(_url: str):
        return None

    async def _rebound(_client, _method, url, **_kwargs):
        raise PinnedAddressError(
            _URLBlock.LOOPBACK_HOST.value,
            request=httpx.Request("GET", url),
            block=_URLBlock.LOOPBACK_HOST,
        )

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _rebound)
    result = await ReadUrlTool().execute({"url": "https://app.example/"}, _ctx())
    err = result.error or ""
    assert result.success is False
    assert result.metadata.get("code") == "loopback_host"
    assert result.metadata.get("policy_failure") is True
    assert "不要再空转外网深读" not in err
    assert "browser" in err and "terminal" in err


@pytest.mark.parametrize(
    "url",
    [
        "src/foo.ts",
        "file:///C:/scratch/page.html",
        r"C:\scratch\page.html",
        "zoogame.cc",
        "ftp://example.com/a",
        "_scratch/zoogame.cc/index.html",
    ],
)
async def test_read_url_not_a_web_url_reroutes_to_file_read(url: str):
    """非 http(s)：改道 file_read，不贴收口话术，也不教补 https://。"""
    result = await ReadUrlTool().execute({"url": url}, _ctx())
    err = result.error or ""

    assert result.success is False
    assert result.metadata.get("code") == "not_a_web_url"
    assert result.metadata.get("policy_failure") is True
    assert "不要再空转外网深读" not in err
    assert "基于已有材料收口写作" not in err
    assert "file_read" in err
    assert "不要给本参数补 https://" in err
    assert "请补 https" not in err
    assert "加上 https://" not in err


async def test_read_url_not_a_web_url_survives_file_redirect(monkeypatch):
    """公网跳到 file:// 走同一条改道，不是「停止深读」。"""
    from agentcore.tools.builtin.web.read_url import BlockedRedirectError

    async def _allow(_url: str):
        return None

    async def _rebound(_client, _method, url, **_kwargs):
        raise BlockedRedirectError(_URLBlock.BAD_SCHEME)

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _rebound)
    result = await ReadUrlTool().execute({"url": "https://example.com/x"}, _ctx())
    err = result.error or ""
    assert result.success is False
    assert result.metadata.get("code") == "not_a_web_url"
    assert result.metadata.get("policy_failure") is True
    assert "不要再空转外网深读" not in err
    assert "file_read" in err


def test_read_url_schema_routes_workspace_paths_to_file_read():
    schema = ReadUrlTool().schema
    assert "file_read" in schema.description
    assert "不要补 https://" in schema.description
    url_desc = schema.parameters["properties"]["url"]["description"]
    assert "http://" in url_desc and "https://" in url_desc
    assert "file_read" in url_desc


@pytest.mark.parametrize(
    ("block", "code"),
    [
        (_URLBlock.BLOCKED_HOST, "blocked_host"),
        (_URLBlock.DNS_FAIL, "dns_resolve_failed"),
        (_URLBlock.PRIVATE_IP, "private_address_blocked"),
        (_URLBlock.PRIVATE_IP_FAKE_PROXY, "fake_ip_proxy_blocked"),
    ],
)
async def test_read_url_pre_flight_blocks_carry_distinct_codes(monkeypatch, block, code):
    """DNS 失败 / 私有 IP / 保留域名各有语义，用户面不该塌成同一句。"""

    async def _blocked(_url: str):
        return block

    monkeypatch.setattr(read_url_mod, "_classify_url", _blocked)
    result = await ReadUrlTool().execute({"url": "https://example.com/x"}, _ctx())
    assert result.success is False
    assert result.metadata.get("code") == code
    assert result.metadata.get("policy_failure") is not True


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.com/x")
    return httpx.HTTPStatusError(
        "boom", request=req, response=httpx.Response(code, request=req)
    )


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (_status_error(401), "site_access_denied"),
        (_status_error(403), "site_access_denied"),
        (_status_error(429), "site_access_denied"),
        (_status_error(451), "site_access_denied"),
        (_status_error(404), "NOT_FOUND"),
        (_status_error(500), "http_status_error"),
        (EgressError("站点近期连续访问失败，已临时熔断"), "egress_circuit_open"),
        (httpx.ConnectTimeout("connect timed out"), "site_unreachable"),
        (httpx.ConnectError("refused"), "site_unreachable"),
        (httpx.ReadTimeout("slow"), "read_timeout"),
    ],
)
async def test_read_url_fetch_failures_carry_distinct_codes(monkeypatch, exc, code):
    """抓取失败各有语义（反爬 / 404 / 熔断 / 连不上 / 读超时），别都塞同一个 code。"""

    async def _allow(_url: str):
        return None

    async def _raise(_client, _method, _url, **_kwargs):
        raise exc

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _raise)
    result = await ReadUrlTool().execute({"url": "https://example.com/x"}, _ctx())
    assert result.success is False
    assert result.metadata.get("code") == code
    # 这些仍是环境类硬失败：继续计入 run 熔断（不是 policy_failure）。
    assert result.metadata.get("policy_failure") is not True


async def test_read_url_connect_timeout_internal_cancel_is_tool_failure(monkeypatch):
    """httpx connect timeout wraps CancelledError in context; must not abort the turn."""

    inner = TimeoutError()
    inner.__context__ = asyncio.CancelledError()
    exc = httpx.ConnectTimeout("connect timed out")
    exc.__context__ = inner

    async def _allow(_url: str):
        return None

    async def _raise(_client, _method, _url, **_kwargs):
        raise exc

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _raise)
    result = await ReadUrlTool().execute({"url": "https://example.com/x"}, _ctx())
    assert result.success is False
    assert result.metadata.get("code") == "site_unreachable"


async def test_safe_request_redirect_block_keeps_value_error_contract(monkeypatch):
    """逐跳拦截仍是 ValueError（download_url 按前缀分支），但带上了拒绝原因。"""
    from agentcore.tools.builtin.web.read_url import BlockedRedirectError, _safe_request

    async def _blocked(_url: str):
        return _URLBlock.PRIVATE_IP

    monkeypatch.setattr(read_url_mod, "_classify_url", _blocked)
    async with httpx.AsyncClient() as client:
        with pytest.raises(BlockedRedirectError) as ei:
            await _safe_request(client, "GET", "https://example.com/x")
    assert isinstance(ei.value, ValueError)
    assert str(ei.value).startswith("URL blocked")
    assert ei.value.block is _URLBlock.PRIVATE_IP


async def test_read_url_retire_latch_blocks_fetch_after_disable(monkeypatch):
    """Run-scoped retirement survives a fresh tool call (simulates Wave retry)."""
    from agentcore.tools.builtin.web._net import (
        READ_URL_RETIRE_STEER,
        clear_read_url_retired,
        mark_read_url_retired,
    )

    run_id = "retire-latch-run"
    clear_read_url_retired(run_id)
    mark_read_url_retired(run_id, message=READ_URL_RETIRE_STEER)

    fetched = {"n": 0}

    async def _should_not_fetch(*_a, **_k):
        fetched["n"] += 1
        raise AssertionError("retired read_url must not fetch")

    monkeypatch.setattr(read_url_mod, "_safe_request", _should_not_fetch)
    result = await ReadUrlTool().execute(
        {"url": "https://example.com/x"}, _ctx(run_id=run_id)
    )
    assert result.success is False
    assert fetched["n"] == 0
    assert "停用" in (result.error or "")
    assert result.metadata.get("retire_tools") == ["read_url"]
    clear_read_url_retired(run_id)


async def test_read_url_403_steers_stop_read(monkeypatch):
    async def _allow(_url: str):
        return None

    async def _forbidden(_client, _method, url, **_kwargs):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "denied", request=req, response=httpx.Response(403, request=req)
        )

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _forbidden)
    result = await ReadUrlTool().execute({"url": "https://example.com/pay"}, _ctx())
    assert result.success is False
    err = result.error or ""
    assert "403" in err
    assert "收口" in err or "停止" in err
    # Actionable next moves (not just stop-URL): public/summary or hand-brain paste.
    assert "下一招" in err
    assert "公开" in err or "摘要" in err
    assert "手脑" in err or "截图" in err
    assert "勿假装已登录" in err or "已登录抓取" in err
    # Same-reject-class: actionable — no same URL / same strategy; public sources OK.
    assert "同拒绝类" in err or "本 URL" in err
    assert "再调 read_url" in err or "勿对" in err
    assert result.metadata.get("policy_failure") is not True


@pytest.mark.parametrize("code", [401, 429, 451])
async def test_read_url_anti_crawl_steers_next_move(monkeypatch, code):
    """401/429/451 share the 403 anti-crawl receipt: stop-read + workable next move."""

    async def _allow(_url: str):
        return None

    async def _blocked(_client, _method, url, **_kwargs):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "blocked", request=req, response=httpx.Response(code, request=req)
        )

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _blocked)
    result = await ReadUrlTool().execute({"url": "https://example.com/pay"}, _ctx())
    assert result.success is False
    err = result.error or ""
    assert str(code) in err
    assert "停止" in err or "换 URL" in err
    assert "下一招" in err
    assert "手脑" in err or "截图" in err
    assert "同拒绝类" in err or "本 URL" in err
    assert result.metadata.get("policy_failure") is not True


async def test_read_url_requires_url():
    result = await ReadUrlTool().execute({}, _ctx())
    assert result.success is False
    assert "url" in result.error


# --- read_url: GitHub api.github.com fast path ---


def test_parse_github_page_url_repo_tree_blob():
    from agentcore.tools.builtin.web.github_page import (
        _GithubBlobPage,
        _GithubOwnerPage,
        _GithubRepoPage,
        parse_github_page_url,
    )

    user = parse_github_page_url("https://github.com/octo")
    assert isinstance(user, _GithubOwnerPage)
    assert user.owner == "octo"

    org_tab = parse_github_page_url("https://www.github.com/acme?tab=repositories")
    assert isinstance(org_tab, _GithubOwnerPage)
    assert org_tab.owner == "acme"

    root = parse_github_page_url("https://github.com/octo/demo")
    assert isinstance(root, _GithubRepoPage)
    assert root.owner == "octo" and root.repo == "demo" and root.ref is None

    tree = parse_github_page_url("https://www.github.com/octo/demo/tree/main/src")
    assert isinstance(tree, _GithubRepoPage)
    assert tree.ref == "main"

    blob = parse_github_page_url("https://github.com/octo/demo/blob/main/src/a.py")
    assert isinstance(blob, _GithubBlobPage)
    assert blob.ref == "main" and blob.path == "src/a.py"

    assert parse_github_page_url("https://github.com/octo?tab=stars") is None
    assert parse_github_page_url("https://github.com/settings") is None
    assert parse_github_page_url("https://github.com/octo/demo/issues/1") is None
    assert parse_github_page_url("https://gist.github.com/octo/abc") is None
    assert parse_github_page_url("https://example.com/octo/demo") is None


def _no_git_pat(monkeypatch):
    async def _load(_user_id: str):
        return None

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )


def _assert_secret_absent(*blobs: object, secret: str) -> None:
    for blob in blobs:
        if secret and isinstance(blob, str) and secret in blob:
            raise AssertionError("secret token leaked into test-visible output")


async def test_read_url_github_repo_root_via_api(monkeypatch):
    import base64

    _no_git_pat(monkeypatch)
    readme_b64 = base64.b64encode(b"# Demo\nHello from README.\n").decode()
    api_calls: list[str] = []

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **kwargs):
        api_calls.append(url)
        headers = kwargs.get("headers") or {}
        assert "Authorization" not in headers
        if url == "https://api.github.com/repos/octo/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "octo/demo",
                    "private": False,
                    "visibility": "public",
                    "default_branch": "main",
                    "description": "A demo repo",
                    "html_url": "https://github.com/octo/demo",
                },
                request=httpx.Request("GET", url),
            )
        if url == "https://api.github.com/repos/octo/demo/readme":
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "encoding": "base64",
                    "content": readme_b64,
                },
                request=httpx.Request("GET", url),
            )
        raise AssertionError(f"unexpected URL (HTML must not be hit): {url}")

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://github.com/octo/demo"}, _ctx()
    )
    assert result.success is True
    body = json.loads(result.output)
    assert body["title"] == "octo/demo"
    assert "private: false" in body["content"]
    assert "visibility: public" in body["content"]
    assert "Hello from README." in body["content"]
    assert all(u.startswith("https://api.github.com/") for u in api_calls)
    assert result.citations[0]["snippet"] == "A demo repo"


async def test_read_url_github_blob_via_api(monkeypatch):
    import base64

    _no_git_pat(monkeypatch)
    file_b64 = base64.b64encode(b"print('hi')\n").decode()

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        assert "api.github.com" in url
        assert "/contents/src/app.py" in url
        assert "ref=main" in url
        return httpx.Response(
            200,
            json={
                "type": "file",
                "encoding": "base64",
                "content": file_b64,
                "path": "src/app.py",
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://github.com/octo/demo/blob/main/src/app.py"}, _ctx()
    )
    assert result.success is True
    body = json.loads(result.output)
    assert "print('hi')" in body["content"]
    assert "path: src/app.py" in body["content"]
    assert body["title"] == "octo/demo/src/app.py"


async def test_read_url_github_api_failure_falls_back_to_html(monkeypatch):
    _no_git_pat(monkeypatch)
    html = (
        "<html><head><title>HTML fallback</title>"
        '<meta name="description" content="from html">'
        "</head><body><p>HTML body</p></body></html>"
    )

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        if "api.github.com" in url:
            return httpx.Response(
                404, json={"message": "Not Found"}, request=httpx.Request("GET", url)
            )
        assert url == "https://github.com/octo/demo"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://github.com/octo/demo"}, _ctx()
    )
    assert result.success is True
    body = json.loads(result.output)
    assert body["title"] == "HTML fallback"
    assert body["content"] == "HTML body"


async def test_read_url_github_user_page_via_api(monkeypatch):
    _no_git_pat(monkeypatch)
    api_paths: list[str] = []

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **kwargs):
        parsed = urlparse(url)
        api_paths.append(parsed.path)
        headers = kwargs.get("headers") or {}
        assert "Authorization" not in headers
        if parsed.path == "/users/octo":
            return httpx.Response(
                200,
                json={"login": "octo", "type": "User", "bio": "builds demos"},
                request=httpx.Request("GET", url),
            )
        if parsed.path == "/users/octo/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "octo/demo",
                        "description": "A demo repo",
                        "default_branch": "main",
                        "html_url": "https://github.com/octo/demo",
                        "private": False,
                    }
                ],
                request=httpx.Request("GET", url),
            )
        raise AssertionError(f"unexpected URL (HTML must not be hit): {url}")

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://github.com/octo"}, _ctx())
    assert result.success is True
    body = json.loads(result.output)
    assert body["title"] == "octo"
    assert "octo/demo" in body["content"]
    assert "description: A demo repo" in body["content"]
    assert "default_branch: main" in body["content"]
    assert "html_url: https://github.com/octo/demo" in body["content"]
    assert any(p == "/users/octo/repos" or p == "/orgs/octo/repos" for p in api_paths)


async def test_read_url_github_org_page_via_api(monkeypatch):
    _no_git_pat(monkeypatch)
    api_paths: list[str] = []

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        parsed = urlparse(url)
        api_paths.append(parsed.path)
        if parsed.path == "/users/acme":
            return httpx.Response(
                200,
                json={"login": "acme", "type": "Organization", "description": "org"},
                request=httpx.Request("GET", url),
            )
        if parsed.path == "/orgs/acme/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "acme/sdk",
                        "description": "SDK",
                        "default_branch": "main",
                        "html_url": "https://github.com/acme/sdk",
                    }
                ],
                request=httpx.Request("GET", url),
            )
        if parsed.path == "/users/acme/repos":
            raise AssertionError("org listing must use /orgs/{org}/repos")
        raise AssertionError(f"unexpected URL (HTML must not be hit): {url}")

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://github.com/acme?tab=repositories"}, _ctx()
    )
    assert result.success is True
    body = json.loads(result.output)
    assert "acme/sdk" in body["content"]
    assert "/orgs/acme/repos" in api_paths


async def test_read_url_github_user_page_sends_pat_authorization(monkeypatch):
    from agentcore.workspace.git_credentials import GitAuthMaterial

    secret = "ghs_unit_test_pat_value"
    seen_auth: list[bool] = []

    async def _load(user_id: str):
        assert user_id == "u"
        return GitAuthMaterial(username="x-access-token", token=secret)

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **kwargs):
        headers = kwargs.get("headers") or {}
        auth = headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or auth[7:] != secret:
            raise AssertionError("expected account PAT in Authorization header")
        seen_auth.append(True)
        parsed = urlparse(url)
        if parsed.path == "/users/octo":
            return httpx.Response(
                200,
                json={"login": "octo", "type": "User"},
                request=httpx.Request("GET", url),
            )
        if parsed.path == "/users/octo/repos":
            return httpx.Response(
                200,
                json=[
                    {
                        "full_name": "octo/private-demo",
                        "description": "secret sauce",
                        "default_branch": "main",
                        "html_url": "https://github.com/octo/private-demo",
                        "private": True,
                    }
                ],
                request=httpx.Request("GET", url),
            )
        raise AssertionError(f"unexpected URL (HTML must not be hit): {url}")

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://github.com/octo"}, _ctx())
    assert result.success is True
    assert seen_auth
    _assert_secret_absent(result.output, result.error or "", secret=secret)
    body = json.loads(result.output)
    assert "octo/private-demo" in body["content"]
    assert "private: true" in body["content"]


async def test_read_url_github_user_page_api_failure_falls_back_to_html(monkeypatch):
    _no_git_pat(monkeypatch)
    html = (
        "<html><head><title>User HTML</title>"
        "</head><body><p>JS shell</p></body></html>"
    )

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        if "api.github.com" in url:
            return httpx.Response(
                403, json={"message": "rate limit"}, request=httpx.Request("GET", url)
            )
        assert url == "https://github.com/octo"
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://github.com/octo"}, _ctx())
    assert result.success is True
    body = json.loads(result.output)
    assert body["title"] == "User HTML"
    assert "JS shell" in body["content"]


async def test_read_url_github_repo_sends_pat_authorization(monkeypatch):
    import base64

    from agentcore.workspace.git_credentials import GitAuthMaterial

    secret = "ghs_unit_test_pat_value"
    seen_auth: list[bool] = []
    readme_b64 = base64.b64encode(b"# Private\n").decode()

    async def _load(_user_id: str):
        return GitAuthMaterial(username="x-access-token", token=secret)

    monkeypatch.setattr(
        "agentcore.workspace.git_credentials.load_git_auth_for_user",
        _load,
    )

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **kwargs):
        headers = kwargs.get("headers") or {}
        auth = headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or auth[7:] != secret:
            raise AssertionError("expected account PAT in Authorization header")
        seen_auth.append(True)
        if url == "https://api.github.com/repos/octo/demo":
            return httpx.Response(
                200,
                json={
                    "full_name": "octo/demo",
                    "private": True,
                    "visibility": "private",
                    "default_branch": "main",
                    "description": "private demo",
                    "html_url": "https://github.com/octo/demo",
                },
                request=httpx.Request("GET", url),
            )
        if url == "https://api.github.com/repos/octo/demo/readme":
            return httpx.Response(
                200,
                json={
                    "path": "README.md",
                    "encoding": "base64",
                    "content": readme_b64,
                },
                request=httpx.Request("GET", url),
            )
        raise AssertionError(f"unexpected URL (HTML must not be hit): {url}")

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://github.com/octo/demo"}, _ctx()
    )
    assert result.success is True
    assert seen_auth
    _assert_secret_absent(result.output, result.error or "", secret=secret)
    body = json.loads(result.output)
    assert "private: true" in body["content"]


async def test_web_search_requires_query():
    result = await WebSearchTool().execute({"query": "  "}, _ctx())
    assert result.success is False
    assert "query" in result.error


async def test_searxng_backend_trips_circuit_after_transport_failures(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)

    class _FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FailClient())

    backend = SearXNGBackend("http://localhost:18888")
    for _ in range(_net.WEB_HOST_FAIL_THRESHOLD):
        with pytest.raises(httpx.ConnectError):
            await backend.search("q")

    with pytest.raises(EgressError, match="熔断"):
        await backend.search("q")


async def test_searxng_breaker_message_is_honest():
    # An open breaker must NOT claim "未就绪/出网受限" (it opens on repeated request
    # failures, usually overload under a parallel burst — SearXNG is typically up).
    host = "localhost"
    _net._states.pop(host, None)
    for _ in range(_net.WEB_HOST_FAIL_THRESHOLD):
        note_failure(host)

    backend = SearXNGBackend("http://localhost:18888")
    with pytest.raises(EgressError) as ei:
        await backend.search("q")
    msg = str(ei.value)
    assert "熔断" in msg
    assert host in msg
    assert "未就绪" not in msg and "出网受限" not in msg
    _net._states.pop(host, None)


async def test_searxng_backend_caps_concurrent_requests(monkeypatch):
    # A parallel team can fire dozens of searches at once; the backend semaphore must
    # cap simultaneous hits on the single SearXNG instance so the burst queues into
    # waves instead of saturating it (which is what trips the breaker for everyone).
    host = "localhost"
    _net._states.pop(host, None)
    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    state = {"inflight": 0, "peak": 0}
    gate = asyncio.Event()

    class _GatedClient:
        async def get(self, *args, **kwargs):
            state["inflight"] += 1
            state["peak"] = max(state["peak"], state["inflight"])
            try:
                await gate.wait()  # hold the slot open until released
            finally:
                state["inflight"] -= 1
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _GatedClient())

    backend = SearXNGBackend("http://localhost:18888")
    cap = search_backend_mod._SEARCH_CONCURRENCY
    tasks = [asyncio.create_task(backend.search("q")) for _ in range(cap + 4)]
    for _ in range(100):  # pump the loop until the gate fills to the ceiling
        await asyncio.sleep(0)
        if state["inflight"] >= cap:
            break
    assert state["inflight"] == cap  # extra tasks are parked on the semaphore, not in flight

    gate.set()  # release; the parked tasks drain in a second wave
    results = await asyncio.gather(*tasks)
    assert state["peak"] == cap  # never exceeded the cap
    assert all(len(r) == 1 for r in results)
    _net._states.pop(host, None)


# --- search_backend: token-bucket rate limiter (B3 CAPTCHA defence) ---


async def test_token_bucket_allows_burst_then_paces():
    # Starts full (capacity tokens) so a burst passes instantly; once drained the next
    # token can't arrive faster than the refill rate — that paces the sustained rate that
    # CAPTCHA keys on. Lower-bound timing only (robust on slow CI).
    bucket = search_backend_mod._TokenBucket(rate_per_sec=20.0, capacity=2.0)
    await bucket.acquire()
    await bucket.acquire()  # burst of 2 drained
    start = time.monotonic()
    await bucket.acquire()  # must wait ~1/20s for the next token
    assert (time.monotonic() - start) >= 0.03


async def test_token_bucket_refills_over_elapsed_time():
    # Refill is proportional to elapsed time: simulate time passing by backdating the
    # update clock, then a drained bucket serves again without a real wait.
    bucket = search_backend_mod._TokenBucket(rate_per_sec=10.0, capacity=1.0)
    await bucket.acquire()  # drained
    bucket._updated -= 1.0  # pretend 1s elapsed → ~10 tokens refilled (capped at capacity)
    await asyncio.wait_for(bucket.acquire(), timeout=0.5)  # served from refill, no long wait


async def test_searxng_backend_acquires_rate_token_per_search(monkeypatch):
    # Regression guard: every outbound search must pass the rate-limit bucket (so the
    # CAPTCHA-defence pacing can't be silently dropped from the request path).
    host = "localhost"
    _net._states.pop(host, None)
    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    class _OkClient:
        async def get(self, *args, **kwargs):
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _OkClient())

    backend = SearXNGBackend("http://localhost:18888")
    acquired = {"n": 0}
    bucket = backend._get_bucket()
    real_acquire = bucket.acquire

    async def _counting_acquire():
        acquired["n"] += 1
        await real_acquire()

    monkeypatch.setattr(bucket, "acquire", _counting_acquire)

    await backend.search("q")
    await backend.search("q")
    assert acquired["n"] == 2
    _net._states.pop(host, None)


async def test_searxng_backend_retries_transient_5xx_then_succeeds(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)
    monkeypatch.setattr(search_backend_mod, "_SEARCH_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(search_backend_mod, "_SEARCH_RETRY_JITTER_S", 0.0)

    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    class _FlakyClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            if self.calls < 2:  # first attempt 502, retry succeeds
                return httpx.Response(502, request=req)
            return httpx.Response(200, json=payload, request=req)

    client = _FlakyClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)

    backend = SearXNGBackend("http://localhost:18888")
    results = await backend.search("q")

    assert client.calls == 2  # retried once past the 502
    assert [r.url for r in results] == ["https://e.com/a"]
    # success on the retry clears the breaker — no failure recorded
    assert host not in _net._states


async def test_searxng_backend_gives_up_after_persistent_5xx(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)
    monkeypatch.setattr(search_backend_mod, "_SEARCH_RETRY_BASE_S", 0.0)
    monkeypatch.setattr(search_backend_mod, "_SEARCH_RETRY_JITTER_S", 0.0)

    req = httpx.Request("GET", "http://localhost:18888/search")

    class _AllFailClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return httpx.Response(502, request=req)

    client = _AllFailClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)

    backend = SearXNGBackend("http://localhost:18888")
    with pytest.raises(httpx.HTTPStatusError):
        await backend.search("q")

    assert client.calls == search_backend_mod._SEARCH_ATTEMPTS  # exhausted the retries
    # one breaker failure per CALL (not one per internal attempt)
    assert _net._states[host].fails == 1


async def test_searxng_backend_does_not_retry_4xx(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)
    req = httpx.Request("GET", "http://localhost:18888/search")

    class _ClientErrClient:
        def __init__(self):
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return httpx.Response(400, request=req)

    client = _ClientErrClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client)

    backend = SearXNGBackend("http://localhost:18888")
    with pytest.raises(httpx.HTTPStatusError):
        await backend.search("q")

    assert client.calls == 1  # client errors are not retried
    assert host not in _net._states  # nor counted against the breaker


async def test_probe_search_backend_reports_reachable(monkeypatch):
    monkeypatch.setattr(search_backend_mod, "_backend", None)
    req = httpx.Request("GET", "http://localhost:18888/healthz")

    class _OkClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return httpx.Response(200, text="OK", request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _OkClient())
    assert await search_backend_mod.probe_search_backend() == (True, "http://localhost:18888")


async def test_probe_search_backend_reports_unreachable(monkeypatch):
    # A down dependency must be reported, never raised — startup can't be broken by it.
    monkeypatch.setattr(search_backend_mod, "_backend", None)

    class _DownClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _DownClient())
    result = await search_backend_mod.probe_search_backend()
    assert result is not None
    ok, detail = result
    assert ok is False
    assert "ConnectError" in detail


async def test_probe_search_results_reports_ok(monkeypatch):
    # The real-search canary (D5): a query that returns ≥1 result confirms the engine
    # pool actually works, not just that /healthz is 200.
    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [SearchResult("t", "https://a.com", "s")]

    monkeypatch.setattr(search_backend_mod, "_backend", _Backend())
    assert await search_backend_mod.probe_search_results() == (True, 1)


async def test_probe_search_results_flags_empty(monkeypatch):
    # The production failure mode: SearXNG healthz-200 but every engine CAPTCHA-suspended
    # → real search returns empty. The canary must surface this (ok=False), unlike the
    # reachability probe which would still report healthy.
    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return []

    monkeypatch.setattr(search_backend_mod, "_backend", _Backend())
    assert await search_backend_mod.probe_search_results() == (False, 0)


async def test_probe_search_results_never_raises(monkeypatch):
    # Best-effort like the reachability probe: a failing search must never break startup.
    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            raise httpx.ConnectError("down")

    monkeypatch.setattr(search_backend_mod, "_backend", _Backend())
    assert await search_backend_mod.probe_search_results() is None


async def test_web_search_fast_fails_when_circuit_open(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)

    class _FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FailClient())
    monkeypatch.setattr(search_backend_mod, "_backend", None)

    tool = WebSearchTool()
    for _ in range(_net.WEB_HOST_FAIL_THRESHOLD):
        result = await tool.execute({"query": "test"}, _ctx())
        assert result.success is False

    result = await tool.execute({"query": "test"}, _ctx())
    assert result.success is False
    assert "熔断" in result.error
    assert result.duration_ms < 500


# --- search_backend: Tavily fallback leg ---


async def test_tavily_backend_parses_results_and_sends_bearer(monkeypatch):
    # Tavily's result objects share SearXNG's title/url/content shape, so the same
    # _parse_results handles both. Verify the request carries the Bearer key + query.
    captured: dict = {}
    req = httpx.Request("POST", "https://api.tavily.com/search")
    payload = {
        "results": [
            {"title": "T1", "url": "https://a.com", "content": "snip a", "score": 0.9},
            {"title": "T2", "url": "https://b.com", "content": "snip b", "score": 0.8},
        ]
    }

    class _Client:
        async def post(self, url, json=None, headers=None):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())

    backend = TavilyBackend(api_key="tvly-test", base_url="https://api.tavily.com")
    results = await backend.search("深圳天气", max_results=2)

    assert [(r.title, r.url, r.snippet) for r in results] == [
        ("T1", "https://a.com", "snip a"),
        ("T2", "https://b.com", "snip b"),
    ]
    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["headers"]["Authorization"] == "Bearer tvly-test"
    assert captured["json"]["query"] == "深圳天气"
    assert captured["json"]["max_results"] == 2
    # Chinese query → country boost (Tavily has no language param).
    assert captured["json"]["country"] == "china"


async def test_searxng_backend_sends_explicit_language(monkeypatch):
    """Pin language on the wire so default_lang=auto / IP locale cannot hijack locale."""
    from agentcore.tools.builtin.web.search_backend import infer_search_language

    assert infer_search_language("OpenAI 股权 融资") == "zh"
    assert infer_search_language("Anthropic funding round") == "en"
    assert infer_search_language("東京の天気を調べる") == "ja"

    host = "localhost"
    _net._states.pop(host, None)
    captured: dict = {}
    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    class _Client:
        async def get(self, url, params=None):
            captured["params"] = dict(params or {})
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = SearXNGBackend("http://localhost:18888")
    await backend.search("OpenAI 股权分析", max_results=3, language="zh")
    assert captured["params"]["language"] == "zh"
    assert captured["params"]["q"] == "OpenAI 股权分析"


async def test_tavily_backend_requires_api_key():
    # Defensive: an unconfigured Tavily leg fails honestly rather than calling the API.
    backend = TavilyBackend(api_key="", base_url="https://api.tavily.com")
    with pytest.raises(EgressError, match="API key"):
        await backend.search("q")


async def test_tavily_backend_raises_on_http_error(monkeypatch):
    req = httpx.Request("POST", "https://api.tavily.com/search")

    class _Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(401, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = TavilyBackend(api_key="tvly-bad", base_url="https://api.tavily.com")
    with pytest.raises(httpx.HTTPStatusError):
        await backend.search("q")


async def test_tavily_backend_emits_querying_phase(monkeypatch):
    # 工具执行阶段进度 (联网搜索前端展示优化): the leg signals「正在检索」right before its
    # request flies, so the waiting UI is live instead of a dead spinner.
    req = httpx.Request("POST", "https://api.tavily.com/search")

    class _Client:
        async def post(self, *args, **kwargs):
            return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = TavilyBackend(api_key="tvly-test", base_url="https://api.tavily.com")
    phases: list[str] = []
    await backend.search("q", on_phase=phases.append)
    assert "querying" in phases


class _StubBackend:
    """Minimal SearchBackend: returns canned results or raises a canned error."""

    def __init__(self, results=None, exc=None):
        self._results = results or []
        self._exc = exc
        self.calls = 0

    async def search(self, query, max_results=5, on_phase=None, *, language=None):
        self.calls += 1
        if on_phase:
            on_phase("querying")
        if self._exc is not None:
            raise self._exc
        return self._results


async def test_fallback_uses_primary_on_success():
    # A successful primary never touches the fallback (no per-query Tavily cost).
    primary = _StubBackend(results=[SearchResult("P", "https://p.com", "ps")])
    fallback = _StubBackend(results=[SearchResult("F", "https://f.com", "fs")])
    results = await FallbackSearchBackend(primary, fallback).search("q")

    assert [r.url for r in results] == ["https://p.com"]
    assert primary.calls == 1
    assert fallback.calls == 0


async def test_fallback_switches_on_primary_failure():
    # The 案例1 mode: SearXNG breaker open → retry once via Tavily.
    primary = _StubBackend(exc=EgressError("熔断"))
    fallback = _StubBackend(results=[SearchResult("F", "https://f.com", "fs")])
    results = await FallbackSearchBackend(primary, fallback).search("q")

    assert [r.url for r in results] == ["https://f.com"]
    assert primary.calls == 1 and fallback.calls == 1


async def test_fallback_surfaces_primary_error_when_both_fail():
    # Both down → the PRIMARY's (already-tuned, honest) reason is what the model sees.
    primary = _StubBackend(exc=EgressError("主熔断信息"))
    fallback = _StubBackend(exc=httpx.ConnectError("tavily down"))

    with pytest.raises(EgressError, match="主熔断信息"):
        await FallbackSearchBackend(primary, fallback).search("q")


async def test_fallback_does_not_run_on_wrapped_cancel():
    req = httpx.Request("GET", "http://example.invalid/search")
    wrapped = httpx.ConnectError("wrapped-cancel", request=req)
    wrapped.__cause__ = asyncio.CancelledError()
    primary = _StubBackend(exc=wrapped)
    fallback = _StubBackend(results=[SearchResult("F", "https://f.com", "fs")])

    with pytest.raises(asyncio.CancelledError):
        await FallbackSearchBackend(primary, fallback).search("q")
    assert fallback.calls == 0


async def test_fallback_emits_fallback_phase():
    # 工具执行阶段进度: when the primary goes search-blind, the wrapper signals「改用备用引擎」
    # so the waiting UI explains the Tavily leg's extra latency (not a stalled spinner).
    primary = _StubBackend(exc=EgressError("熔断"))
    fallback = _StubBackend(results=[SearchResult("F", "https://f.com", "fs")])
    phases: list[str] = []
    results = await FallbackSearchBackend(primary, fallback).search(
        "q", on_phase=phases.append
    )
    assert [r.url for r in results] == ["https://f.com"]
    assert "fallback" in phases


async def test_success_path_emits_no_fallback_phase():
    # A healthy primary never signals fallback — the fallback leg (and its phase) stays untouched.
    primary = _StubBackend(results=[SearchResult("P", "https://p.com", "ps")])
    fallback = _StubBackend(results=[])
    phases: list[str] = []
    await FallbackSearchBackend(primary, fallback).search("q", on_phase=phases.append)
    assert "fallback" not in phases


async def test_fallback_aclose_closes_both_legs():
    closed: list[str] = []

    class _Closeable(_StubBackend):
        def __init__(self, name):
            super().__init__()
            self._name = name

        async def aclose(self):
            closed.append(self._name)

    await FallbackSearchBackend(_Closeable("p"), _Closeable("f")).aclose()
    assert sorted(closed) == ["f", "p"]


# --- Sidecar cloud inference web_search fallback (local SearXNG unreachable) ---


def test_inference_web_search_url_strips_openai_v1_suffix():
    from agentcore.tools.builtin.web.cloud_fallback import inference_web_search_url

    assert (
        inference_web_search_url("https://api.example.com/v1/inference/v1")
        == "https://api.example.com/v1/inference/web_search"
    )
    assert (
        inference_web_search_url("http://localhost:8000/v1/inference/v1/")
        == "http://localhost:8000/v1/inference/web_search"
    )


def test_is_local_search_unreachable_selectivity():
    from agentcore.tools.builtin.web.cloud_fallback import is_local_search_unreachable

    assert is_local_search_unreachable(httpx.ConnectError("refused"))
    assert is_local_search_unreachable(httpx.ConnectTimeout("slow"))
    assert is_local_search_unreachable(EgressError("搜索服务 localhost 已临时熔断约 30s"))
    # HTTP 403 / read timeout / empty success path must NOT trigger cloud fallback.
    req = httpx.Request("GET", "http://localhost/search")
    resp = httpx.Response(403, request=req)
    assert not is_local_search_unreachable(httpx.HTTPStatusError("forbidden", request=req, response=resp))
    assert not is_local_search_unreachable(httpx.ReadTimeout("slow read"))
    assert not is_local_search_unreachable(ValueError("bogus"))


async def test_web_search_cloud_fallback_on_local_connect_error(monkeypatch):
    """Local SearXNG ConnectError + bound inference JWT → cloud 200 results."""
    from agentcore.tools.builtin.web.cloud_fallback import (
        CLOUD_FALLBACK_NOTE,
        InferenceSearchCredentials,
        inference_search_credentials_scope,
    )

    class _DownBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            raise httpx.ConnectError("connection refused")

    posted: dict = {}

    class _CloudClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json=None, headers=None):
            posted["url"] = url
            posted["json"] = json
            posted["auth"] = (headers or {}).get("Authorization")
            req = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Alpha beta overview",
                            "url": "https://example.com/cloud",
                            # Real InferenceWebSearchResultItem shape uses snippet.
                            "snippet": "alpha beta findings from cloud search",
                        }
                    ],
                    "source": "cloud",
                },
                request=req,
            )

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _DownBackend())
    monkeypatch.setattr(
        "agentcore.tools.builtin.web.cloud_fallback.outbound_async_client",
        lambda **kwargs: _CloudClient(),
    )

    creds = InferenceSearchCredentials(
        api_key="jwt-test",
        base_url="https://api.example.com/v1/inference/v1",
    )
    with inference_search_credentials_scope(creds):
        result = await WebSearchTool().execute({"query": "alpha beta"}, _ctx())

    assert result.success is True
    payload = json.loads(result.output)
    assert payload["results"][0]["url"] == "https://example.com/cloud"
    assert payload["results"][0]["snippet"] == "alpha beta findings from cloud search"
    assert CLOUD_FALLBACK_NOTE in (payload.get("note") or "")
    assert result.metadata.get("cloud_fallback") is True
    assert result.metadata.get("backend") == "cloud_inference"
    assert posted["url"] == "https://api.example.com/v1/inference/web_search"
    assert posted["auth"] == "Bearer jwt-test"
    assert posted["json"]["query"] == "alpha beta"


async def test_web_search_no_cloud_fallback_without_credentials(monkeypatch):
    """No ContextVar creds → ConnectError surfaces; cloud not called."""
    from agentcore.tools.builtin.web.cloud_fallback import (
        bind_inference_search_credentials,
        reset_inference_search_credentials,
    )

    down_req = httpx.Request("GET", "http://127.0.0.1:18888/search")

    class _DownBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            raise httpx.ConnectError("connection refused", request=down_req)

    cloud_calls = {"n": 0}

    async def _no_cloud(*args, **kwargs):
        cloud_calls["n"] += 1
        raise AssertionError("cloud must not be called without creds")

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _DownBackend())
    monkeypatch.setattr(
        "agentcore.tools.builtin.web.cloud_fallback.cloud_inference_web_search",
        _no_cloud,
    )

    # Explicitly clear any leaked creds from a prior test.
    token = bind_inference_search_credentials(None)
    try:
        result = await WebSearchTool().execute({"query": "alpha"}, _ctx())
    finally:
        reset_inference_search_credentials(token)

    assert result.success is False
    assert "搜索失败" in (result.error or "")
    assert "本地搜索" in (result.error or "") and "不可用" in (result.error or "")
    assert "出网受限" not in (result.error or "")
    assert "docker" not in (result.error or "").lower()
    assert cloud_calls["n"] == 0


async def test_web_search_no_cloud_fallback_on_http_403(monkeypatch):
    """HTTP 403 from local SearXNG is not an unreachable-class failure."""
    from agentcore.tools.builtin.web.cloud_fallback import (
        InferenceSearchCredentials,
        inference_search_credentials_scope,
    )

    req = httpx.Request("GET", "http://localhost/search")
    resp = httpx.Response(403, request=req)

    class _ForbiddenBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            raise httpx.HTTPStatusError("forbidden", request=req, response=resp)

    cloud_calls = {"n": 0}

    async def _no_cloud(*args, **kwargs):
        cloud_calls["n"] += 1
        return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _ForbiddenBackend())
    monkeypatch.setattr(
        "agentcore.tools.builtin.web.cloud_fallback.cloud_inference_web_search",
        _no_cloud,
    )

    creds = InferenceSearchCredentials(
        api_key="jwt-test",
        base_url="https://api.example.com/v1/inference/v1",
    )
    with inference_search_credentials_scope(creds):
        result = await WebSearchTool().execute({"query": "alpha"}, _ctx())

    assert result.success is False
    assert cloud_calls["n"] == 0


# --- 全垃圾 SERP 兜底: tool-layer Tavily weak-retry (decision lives in the tool, not the
# backend — FallbackSearchBackend stays exception-only). Trace 1fd37500b7ed49...: EN academic
# queries returned HTTP 200 + all-junk SERPs (coolmathgames / opentable) that the backend's
# exception fallback can't catch. ---

# An English academic query whose overlap tokens are {qmix, mappo, multi, agent}.
_WEAK_QUERY = "QMIX MAPPO multi agent"


def _junk_results() -> list[SearchResult]:
    """Uniformly weak SERP: zero query-token overlap, non-blocked hosts (survive to the
    relevance filter, which then marks uniformly_weak and injects empty)."""
    return [
        SearchResult("Cool Math Games", "https://coolmathgames.com/", "Free online games"),
        SearchResult("OpenTable", "https://opentable.com/", "Restaurant reservations near you"),
        SearchResult("Zoom Earth", "https://zoom.earth/", "Live weather satellite radar"),
    ]


def _ontopic_results() -> list[SearchResult]:
    """Strong SERP: high query-token overlap → passes the relevance filter (non-weak)."""
    return [
        SearchResult(
            "QMIX and MAPPO for multi-agent RL",
            "https://arxiv.org/abs/2003.08839",
            "QMIX MAPPO multi agent value factorization methods",
        ),
        SearchResult(
            "MAPPO cooperative multi-agent PPO",
            "https://arxiv.org/abs/2103.01955",
            "multi agent MAPPO cooperative benchmark",
        ),
    ]


async def test_weak_serp_retries_via_tavily_and_adopts_strong(monkeypatch):
    # 实搜全垃圾 + 有 Tavily 腿 → 恰好重试一次，且采用非 weak 的重试结果。
    primary = _StubBackend(results=_junk_results())
    fallback = _StubBackend(results=_ontopic_results())
    monkeypatch.setattr(
        search_mod, "get_search_backend", lambda: FallbackSearchBackend(primary, fallback)
    )
    result = await WebSearchTool().execute({"query": _WEAK_QUERY}, _ctx())

    assert result.success is True
    urls = [r["url"] for r in json.loads(result.output)["results"]]
    assert "https://arxiv.org/abs/2003.08839" in urls  # adopted the strong retry set
    assert "https://coolmathgames.com/" not in urls  # weak primary discarded
    assert primary.calls == 1
    assert fallback.calls == 1  # exactly one retry
    assert result.metadata.get("low_relevance") is not True  # adopted set is non-weak


async def test_strong_serp_does_not_retry(monkeypatch):
    # 非 weak → 不重试（Tavily 腿零调用，稳态查询不付外部 API 成本）。
    primary = _StubBackend(results=_ontopic_results())
    fallback = _StubBackend(results=_ontopic_results())
    monkeypatch.setattr(
        search_mod, "get_search_backend", lambda: FallbackSearchBackend(primary, fallback)
    )
    result = await WebSearchTool().execute({"query": _WEAK_QUERY}, _ctx())

    assert result.success is True
    assert primary.calls == 1
    assert fallback.calls == 0  # non-weak → no retry


async def test_weak_serp_no_retry_without_tavily_leg(monkeypatch):
    # 无 Tavily 腿（裸 SearXNG 后端）→ 不重试；uniformly_weak → 空注入 + 诚实质量警告。
    primary = _StubBackend(results=_junk_results())
    monkeypatch.setattr(search_mod, "get_search_backend", lambda: primary)
    result = await WebSearchTool().execute({"query": _WEAK_QUERY}, _ctx())

    assert result.success is True
    assert primary.calls == 1  # no fallback leg → no extra search
    payload = json.loads(result.output)
    assert payload["results"] == []
    assert "字面重合不足" in payload["note"]
    assert "离题 SERP" in payload["note"]
    assert "搜索引擎降级" not in payload["note"]
    assert result.metadata.get("low_relevance") is True
    assert result.metadata.get("empty") is True


async def test_weak_serp_retry_still_weak_empties_with_warning(monkeypatch):
    # 重试仍 weak → 保留原 raw 集但注入为空 + 强警告 note（备用引擎也没救回来）。
    primary = _StubBackend(results=_junk_results())
    fallback = _StubBackend(
        results=[SearchResult("Random Blog", "https://randomblog.example/", "cooking recipes")]
    )
    monkeypatch.setattr(
        search_mod, "get_search_backend", lambda: FallbackSearchBackend(primary, fallback)
    )
    result = await WebSearchTool().execute({"query": _WEAK_QUERY}, _ctx())

    assert result.success is True
    assert primary.calls == 1 and fallback.calls == 1  # retried exactly once
    payload = json.loads(result.output)
    assert payload["results"] == []
    assert "字面重合不足" in payload["note"]
    assert result.metadata.get("low_relevance") is True


async def test_weak_cache_hit_does_not_retry(monkeypatch):
    # 缓存写入最终采用集；缓存命中的 weak 不重试。primary+fallback 都 weak → 首搜保留 weak
    # raw 并缓存之；二搜命中缓存（weak）→ 不再打网、不再重试；注入仍为空。
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    primary = _StubBackend(results=_junk_results())
    fallback = _StubBackend(
        results=[SearchResult("Random Blog", "https://randomblog.example/", "cooking recipes")]
    )
    monkeypatch.setattr(
        search_mod, "get_search_backend", lambda: FallbackSearchBackend(primary, fallback)
    )
    ctx = _ctx(conversation_id="conv-weak-cache")
    tool = WebSearchTool()

    r1 = await tool.execute({"query": _WEAK_QUERY}, ctx)  # live: weak → retry weak → empty inject
    assert r1.metadata.get("cached") is not True
    assert primary.calls == 1 and fallback.calls == 1
    assert json.loads(r1.output)["results"] == []

    r2 = await tool.execute({"query": _WEAK_QUERY}, ctx)  # served from cache (weak set)
    assert r2.metadata.get("cached") is True
    assert primary.calls == 1  # no second live search
    assert fallback.calls == 1  # crucially: the cached weak hit does NOT retry
    assert "字面重合不足" in json.loads(r2.output)["note"]
    assert json.loads(r2.output)["results"] == []


async def test_weak_serp_retry_emits_structured_logs(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(search_mod, "logger", spy)
    primary = _StubBackend(results=_junk_results())
    fallback = _StubBackend(results=_ontopic_results())
    monkeypatch.setattr(
        search_mod, "get_search_backend", lambda: FallbackSearchBackend(primary, fallback)
    )
    await WebSearchTool().execute({"query": _WEAK_QUERY}, _ctx())

    names = [n for n, _ in spy.events]
    assert "search.weak_serp_retry" in names
    assert "search.weak_serp_retry_adopted" in names


def test_get_search_backend_is_bare_searxng_without_tavily(monkeypatch):
    monkeypatch.setattr(search_backend_mod, "_backend", None)
    monkeypatch.setattr(search_backend_mod.settings, "tavily_api_key", "")
    assert isinstance(search_backend_mod.get_search_backend(), SearXNGBackend)


def test_get_search_backend_wraps_fallback_when_tavily_configured(monkeypatch):
    monkeypatch.setattr(search_backend_mod, "_backend", None)
    monkeypatch.setattr(search_backend_mod.settings, "tavily_api_key", "tvly-x")
    backend = search_backend_mod.get_search_backend()
    assert isinstance(backend, FallbackSearchBackend)
    assert isinstance(backend.primary, SearXNGBackend)
    assert isinstance(backend.fallback, TavilyBackend)


async def test_probe_unwraps_fallback_to_probe_searxng_primary(monkeypatch):
    # With Tavily configured the active backend is the wrapper; probe must still
    # reach the SearXNG primary behind it (Tavily has nothing SearXNG-specific).
    monkeypatch.setattr(search_backend_mod, "_backend", None)
    monkeypatch.setattr(search_backend_mod.settings, "tavily_api_key", "tvly-x")
    req = httpx.Request("GET", "http://localhost:18888/healthz")

    class _OkClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return httpx.Response(200, text="OK", request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _OkClient())
    assert await search_backend_mod.probe_search_backend() == (True, "http://localhost:18888")


async def test_aclose_search_backend_closes_and_resets(monkeypatch):
    closed = {"n": 0}

    class _Closeable:
        async def search(self, *a, **k):
            return []

        async def aclose(self):
            closed["n"] += 1

    monkeypatch.setattr(search_backend_mod, "_backend", _Closeable())
    await search_backend_mod.aclose_search_backend()
    assert closed["n"] == 1
    assert search_backend_mod._backend is None


# --- search_cache: web_search conversation result cache (案例1 #5 检索去重) ---


def _sresult(url: str, title: str = "T", snippet: str = "s") -> SearchResult:
    return SearchResult(title=title, url=url, snippet=snippet)


def _sentry(query: str, results, *, max_results: int = 8, stored_at=None) -> SearchCacheEntry:
    return SearchCacheEntry(
        query=query,
        results=results,
        max_results=max_results,
        stored_at=time.time() if stored_at is None else stored_at,
    )


def test_search_cache_get_put_and_key_normalization():
    cache = ConversationSearchCache()
    cache.put(_sentry("Hello World", [_sresult("https://a.com")]))
    # trimmed + lowercased + whitespace-collapsed normalises to the same key
    assert cache.get("  hello   world ", min_results=1) is not None
    assert "HELLO world" in cache
    assert cache.get("other", min_results=1) is None


def test_search_cache_capped_entry_needs_refetch_for_more():
    cache = ConversationSearchCache()
    # returned exactly the cap (5) → more results may exist upstream
    cache.put(_sentry("q", [_sresult(f"https://a/{i}") for i in range(5)], max_results=5))
    assert cache.get("q", min_results=5) is not None  # enough captured
    assert cache.get("q", min_results=8) is None  # wants more than the capped set


def test_search_cache_under_cap_entry_serves_any_request():
    cache = ConversationSearchCache()
    # backend returned fewer (2) than the cap (8) → that's everything it had
    cache.put(_sentry("q", [_sresult("https://a/1"), _sresult("https://a/2")], max_results=8))
    assert cache.get("q", min_results=50) is not None


def test_search_cache_expires_after_ttl():
    cache = ConversationSearchCache(ttl_seconds=10.0)
    cache.put(_sentry("q", [_sresult("https://a")], stored_at=time.time() - 100))
    assert cache.get("q", min_results=1) is None


def test_search_cache_negative_marks_and_serves_recently_empty():
    # A1 防重搜风暴: a query that just came back empty is remembered (negatively) so an
    # immediate re-issue is served empty without a network hit. Normalised like the
    # positive key, so trivially-different spellings collapse to one marker.
    cache = ConversationSearchCache()
    assert cache.is_recently_empty("q") is False
    cache.note_empty("  Q ")  # normalises to the same key as "q"
    assert cache.is_recently_empty("q") is True
    assert cache.is_recently_empty("other") is False


def test_search_cache_negative_marker_expires():
    cache = ConversationSearchCache(empty_ttl_seconds=100.0)
    cache.note_empty("q")
    assert cache.is_recently_empty("q") is True
    cache._empty["q"] = time.time() - 1000  # backdate past the (short) empty TTL
    assert cache.is_recently_empty("q") is False  # expired → a genuine retry is allowed


def test_search_cache_positive_result_clears_negative_marker():
    # A real result supersedes a stale "recently empty" marker (engines recovered).
    cache = ConversationSearchCache()
    cache.note_empty("q")
    assert cache.is_recently_empty("q") is True
    cache.put(_sentry("q", [_sresult("https://a.com")]))
    assert cache.is_recently_empty("q") is False
    assert cache.get("q", min_results=1) is not None


def test_search_cache_negative_lru_caps_entries():
    # The negative cache is bounded like the positive one (oldest marker evicted).
    cache = ConversationSearchCache(max_entries=2)
    for i in range(3):
        cache.note_empty(f"q{i}")
    assert cache.is_recently_empty("q0") is False  # oldest empty marker evicted
    assert cache.is_recently_empty("q2") is True


def test_search_cache_lru_evicts_over_count():
    cache = ConversationSearchCache(max_entries=2)
    for i in range(3):
        cache.put(_sentry(f"q{i}", [_sresult(f"https://s{i}.com")]))
    assert len(cache) == 2
    assert "q0" not in cache  # oldest evicted
    assert "q2" in cache


def test_search_cache_lru_evicts_over_bytes():
    cache = ConversationSearchCache(max_bytes=60)
    cache.put(_sentry("q0", [_sresult("https://a.com", snippet="x" * 30)]))
    cache.put(_sentry("q1", [_sresult("https://b.com", snippet="y" * 30)]))  # total > 60
    assert "q0" not in cache  # oldest evicted to fit the byte budget
    assert "q1" in cache


def test_search_cache_registry_scopes_per_conversation():
    reg = SearchCacheRegistry()
    c1 = reg.get_or_create("c1")
    c2 = reg.get_or_create("c2")
    assert c1 is not c2
    assert reg.get_or_create("c1") is c1


def test_search_cache_registry_caps_conversation_count_lru():
    reg = SearchCacheRegistry(max_conversations=2)
    reg.get_or_create("a")
    reg.get_or_create("b")
    reg.get_or_create("c")
    assert len(reg) == 2
    assert "a" not in reg  # LRU-evicted
    assert "c" in reg


def test_search_cache_registry_reaps_idle_conversation(monkeypatch: pytest.MonkeyPatch):
    recorded: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        search_cache_mod.logger,
        "info",
        lambda event, **kwargs: recorded.append((event, dict(kwargs))),
    )
    reg = SearchCacheRegistry(conversation_ttl_seconds=10.0)
    idle = reg.get_or_create("idle")
    idle.last_access = time.time() - 100  # force past the idle window
    reg.get_or_create("fresh")  # creation triggers idle reaping
    assert "idle" not in reg
    assert "fresh" in reg
    assert recorded == [
        ("search_cache.conversation_evicted", {"evicted_conversation_id": "idle"}),
    ]


async def test_web_search_caches_within_conversation(monkeypatch):
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            return [SearchResult("标题", "https://a.com", "摘要")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    ctx = _ctx(conversation_id="conv-search-cache")
    tool = WebSearchTool()
    r1 = await tool.execute({"query": "深圳天气"}, ctx)
    r2 = await tool.execute({"query": "  深圳天气 "}, ctx)  # normalises to the same key

    assert r1.success and r2.success
    assert calls["n"] == 1  # second served from cache, no re-search
    assert r1.metadata.get("cached") is not True
    assert r2.metadata.get("cached") is True
    assert r2.output == r1.output  # cached hit has the identical result shape


async def test_web_search_skips_cache_without_conversation(monkeypatch):
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            return [SearchResult("t", "https://a.com", "s")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    tool = WebSearchTool()
    await tool.execute({"query": "q"}, _ctx())  # conversation_id == "" → no caching
    await tool.execute({"query": "q"}, _ctx())
    assert calls["n"] == 2


async def test_web_search_empty_result_negatively_cached(monkeypatch):
    # CAPTCHA / transient empty (HTTP 200 + results:[]) → negatively cached briefly so a
    # degraded worker re-issuing the SAME empty query doesn't restorm SearXNG (案例1 重搜
    # 风暴). The marker expires fast, so once the transient cause likely cleared the query
    # genuinely re-searches.
    reg = SearchCacheRegistry()
    monkeypatch.setattr(search_cache_mod, "_registry", reg)
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    ctx = _ctx(conversation_id="conv-empty")
    tool = WebSearchTool()

    r1 = await tool.execute({"query": "q"}, ctx)
    r2 = await tool.execute({"query": "q"}, ctx)  # within window → served empty, no re-search
    assert r1.success and r2.success
    assert calls["n"] == 1  # second suppressed by the negative cache
    assert r2.metadata.get("cached") is True
    assert r2.metadata.get("result_count") == 0

    # once the negative marker ages past its TTL, the same query genuinely re-searches
    # Cache keys include inferred language (``en|q`` for ASCII queries).
    from agentcore.tools.builtin.web.search_cache import _query_key

    empty_key = _query_key("q", "en")
    reg.get_or_create("conv-empty")._empty[empty_key] = time.time() - 10_000
    await tool.execute({"query": "q"}, ctx)
    assert calls["n"] == 2


async def test_web_search_empty_result_is_honest(monkeypatch):
    # D5: an empty set is success (HTTP 200, no transport failure) but must carry an
    # explicit note + ``empty`` flag so the model doesn't read silence as "this doesn't
    # exist" — a CAPTCHA-suspended engine returns HTTP 200 + zero results all the same.
    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    result = await WebSearchTool().execute({"query": "q"}, _ctx())

    assert result.success is True
    assert result.metadata.get("empty") is True
    assert result.metadata.get("result_count") == 0
    payload = json.loads(result.output)
    assert payload["results"] == []
    assert payload.get("note")  # an actionable, non-empty hint for the model
    assert "未返回任何结果" in payload["note"]
    assert result.citations is None  # nothing to cite


async def test_web_search_empty_short_query_does_not_flag_verbose(monkeypatch):
    """Short empty query: honest miss + general tip; must NOT claim the query is too long."""

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    result = await WebSearchTool().execute({"query": "茉莉奶白 LV 商标"}, _ctx())

    assert result.success is True
    note = json.loads(result.output)["note"]
    assert "未返回任何结果" in note
    assert "查询词明显过多" not in note
    assert "2–3 个核心词" not in note
    assert "更通用" in note or "同义" in note


async def test_web_search_empty_long_query_suggests_trim_to_core_words(monkeypatch):
    """Long empty query (>4 tokens): explicitly tip 拆分到 2–3 core words at the miss site."""

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    long_q = "茉莉奶白 四叶花卉 商标申请 驳回 国家知识产权局 案号 LV 近似"
    result = await WebSearchTool().execute({"query": long_q}, _ctx())

    assert result.success is True
    note = json.loads(result.output)["note"]
    assert "未返回任何结果" in note
    assert "查询词明显过多" in note
    assert "拆分" in note
    assert "2–3 个核心词" in note


async def test_web_search_cache_refetches_when_more_results_needed(monkeypatch):
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            # always return exactly max_results (capped) → "more may exist"
            return [SearchResult(f"t{i}", f"https://a.com/{i}", "s") for i in range(max_results)]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    ctx = _ctx(conversation_id="conv-more")
    tool = WebSearchTool()
    await tool.execute({"query": "q", "max_results": 3}, ctx)
    assert calls["n"] == 1
    # wants MORE than the capped cached set → re-search with the bigger budget
    await tool.execute({"query": "q", "max_results": 8}, ctx)
    assert calls["n"] == 2
    # now 8 are cached → a <=8 request hits without re-searching
    await tool.execute({"query": "q", "max_results": 5}, ctx)
    assert calls["n"] == 2


# --- A3: query contract at the tool boundary ---


def test_validate_search_query_rejects_latin_over_word_limit():
    words = " ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 1))
    err = validate_search_query(words)
    assert err is not None
    assert "查询词过多" in err
    assert "超出 1" in err  # 明示超限量
    assert "请改为「" in err  # 可照抄的缩短示例
    assert "2–3 个核心词" in err  # 场景中立拆分建议
    assert "引号" in err  # 引号豁免操作
    assert str(_QUERY_LATIN_WORD_LIMIT) in err


def test_validate_search_query_allows_latin_at_limit():
    words = " ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT))
    assert validate_search_query(words) is None


def test_validate_search_query_quote_exemption():
    # Long quoted phrase + few free words stays under the Latin budget.
    q = f'"this is a long verbatim error message with many words" {" ".join(f"k{i}" for i in range(3))}'
    assert validate_search_query(q) is None
    # Unquoted overflow still rejects even when a quote is present.
    overflow = (
        f'"short quote" {" ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 1))}'
    )
    assert validate_search_query(overflow) is not None


def test_validate_search_query_rejects_cjk_over_char_limit():
    # 纯中文超字数上限：无拉丁词 → 加权字数退化为逐字，与旧行为一致。
    chars = "研" * (_QUERY_CJK_CHAR_LIMIT + 1)
    err = validate_search_query(chars)
    assert err is not None
    assert "查询过长" in err
    assert "超出 1" in err
    assert "请删约 1 字" in err
    assert "2–3 个核心词" in err
    assert "引号" in err


def test_validate_search_query_allows_cjk_at_limit():
    assert validate_search_query("研" * _QUERY_CJK_CHAR_LIMIT) is None


def test_validate_search_query_mixed_weighted_allows_technical_query():
    # 中英混合技术查询：旧口径逐字符计数（41 字）会误拒；新口径拉丁词折 4 字 →
    # 3×4 + 8 中文字 = 20，放行。这正是改动 A 要修的过度惩罚。
    q = "multi-agent orchestration framework 系统 协作 编排 架构"
    assert validate_search_query(q) is None


def test_validate_search_query_mixed_weighted_rejects_when_over():
    # 混合查询即便按加权口径仍超 48（3×4 + 40 中文字 = 52）→ validate 层依旧拒绝（不改写）。
    q = "multi-agent orchestration framework " + "研" * 40
    err = validate_search_query(q)
    assert err is not None
    assert "查询过长" in err
    assert "折合 52 字" in err  # 展示与新口径一致的加权字数
    assert "超出 4" in err
    assert str(_QUERY_LATIN_WORD_WEIGHT) in err  # 文案说明英文词折算权重


def test_validate_search_query_mixed_at_weighted_limit():
    # 恰好压线（加权分支由 CJK 预算 + 拉丁词权重决定，与拉丁词数上限无关）：
    # 拉丁词×权重 + 中文字凑满 48 放行，再多 1 个中文字 = 49 拒绝。
    latin_words = ["multi", "agent", "orchestration", "framework"]  # 4 拉丁词 → 16 字
    latin = " ".join(latin_words)
    cjk_at = _QUERY_CJK_CHAR_LIMIT - len(latin_words) * _QUERY_LATIN_WORD_WEIGHT  # 48 - 16 = 32
    at_limit = f"{latin} " + "研" * cjk_at  # 16 + 32 == 48
    over_limit = f"{latin} " + "研" * (cjk_at + 1)  # 16 + 33 == 49
    assert validate_search_query(at_limit) is None
    assert validate_search_query(over_limit) is not None


def test_validate_search_query_mixed_quote_exemption():
    # 引号内报错原文豁免；未加引号仅剩两个中文核心词 → 放行。
    q = '"TypeError: cannot read property of undefined" 报错 排查'
    assert validate_search_query(q) is None


def test_validate_search_query_book_title_exemption():
    """书名号《》内法规全名豁免字数预算（中文语境专名天然用书名号）。"""
    long_title = "研" * (_QUERY_CJK_CHAR_LIMIT + 8)
    assert validate_search_query(f"{long_title} 适用") is not None
    assert validate_search_query(f"《{long_title}》 适用 解释") is None


def test_validate_search_query_cjk_curly_and_corner_quote_exemption():
    """弯引号 “” 与『』与西文引号同等豁免。"""
    long_phrase = "研" * (_QUERY_CJK_CHAR_LIMIT + 8)
    assert validate_search_query(f"“{long_phrase}” 证据") is None
    assert validate_search_query(f"『{long_phrase}』 证据") is None
    # 混合：书名号 + 西文引号同句均豁免。
    assert (
        validate_search_query(f'《{long_phrase}》 "verbatim error message here" 要点')
        is None
    )


def test_validate_search_query_does_not_rewrite():
    """validate 层纯检查不改写——超限返回错误文案，不返回改写后的 query。

    规范化/截断在 ``prepare_search_query``；validate 只诊断。
    """
    overflow = "研" * (_QUERY_CJK_CHAR_LIMIT + 5)
    err = validate_search_query(overflow)
    assert err is not None
    assert overflow not in err  # 不回显改写建议为「缩短后的全文」
    assert "书名号" in err or "引号" in err


async def test_web_search_rejects_oversized_latin_query_without_backend(monkeypatch):
    """词数超限：prepare 截断后仍搜，打 backend，不再 contract_failure。"""
    calls: list[str] = []

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls.append(query)
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    long_q = " ".join(f"term{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 1))
    result = await WebSearchTool().execute({"query": long_q}, _ctx())
    assert result.success is True
    assert result.contract_failure is not True
    assert len(calls) == 1
    expected = " ".join(f"term{i}" for i in range(_QUERY_LATIN_WORD_LIMIT))
    assert calls[0] == expected
    payload = json.loads(result.output)
    assert payload["query"] == expected
    assert payload.get("original_query") == long_q
    assert "已截断至上限" in (payload.get("note") or "")


def test_web_search_schema_documents_query_contract():
    """契约进 schema：≤12 拉丁词 / 加权≤48 / 超限规范化·截断并明示 / 书名号·引号豁免 / 建议 2–3 词。"""
    schema = WebSearchTool().schema
    blob = schema.description + schema.parameters["properties"]["query"]["description"]
    assert str(_QUERY_LATIN_WORD_LIMIT) in blob  # 拉丁词上限 12
    assert str(_QUERY_CJK_CHAR_LIMIT) in blob  # 加权字数上限 48
    assert str(_QUERY_LATIN_WORD_WEIGHT) in blob  # 英文单词每词折 4 字
    assert "引号" in blob  # 引号短语豁免
    assert "书名号" in blob  # 中文专名豁免
    assert "摘要优先" in blob  # 默认摘要优先基调
    assert "搜到" in blob and "可挂来源号" in blob  # 搜到 ≠ 可挂 #rN
    assert "read_url" in blob  # 成稿挂号须先深读
    assert "2–3" in blob  # 建议一次 2–3 个核心词
    assert "规范化" in blob or "截断" in blob
    assert "明示" in blob
    assert "不会自动改写" not in blob
    assert "无法规范化才拒绝" not in blob
    assert "极端过长" in blob  # 仅极端过长拒绝


def test_reject_copy_states_real_ceiling_not_just_suggestion():
    """拒绝文案口径对齐：明示实际上限 + 超限量 + 可照抄缩短示例 / 拆分 / 引号豁免。"""
    latin = validate_search_query(" ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 1)))
    assert latin is not None
    assert f"上限 {_QUERY_LATIN_WORD_LIMIT}" in latin  # 真实上限，而非只给 2–3 建议
    assert "超出 1" in latin
    assert "请改为「" in latin
    assert "2–3 个核心词" in latin  # 拆分建议
    assert "引号" in latin  # 引号豁免

    cjk = validate_search_query("研" * (_QUERY_CJK_CHAR_LIMIT + 1))
    assert cjk is not None
    assert str(_QUERY_CJK_CHAR_LIMIT) in cjk  # 真实上限
    assert "超出 1" in cjk
    assert "2–3 个核心词" in cjk
    assert "引号" in cjk


def test_latin_reject_includes_shortened_example_from_query():
    """拉丁超限拒绝须给出「前 N 词」示例，便于模型直接照抄收敛。"""
    words = [f"term{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 3)]
    err = validate_search_query(" ".join(words))
    assert err is not None
    expected = " ".join(words[:_QUERY_LATIN_WORD_LIMIT])
    assert f"请改为「{expected}」" in err
    assert "超出 3" in err


def test_prepare_search_query_academic_proper_names():
    """学术长 query（>12 词）：专名加引号（必要时丢 venue）→ 无 error、明示 adjustment。

    注：需超过新拉丁上限 12 才触发规范化；此处用 13 词覆盖。
    """
    q = (
        "Limaye Srinivasan Tavenas permanent formula lower bound proof "
        "complexity theory algebraic circuit STOC"
    )
    prep = prepare_search_query(q)
    assert prep.error is None
    assert '"' in prep.query
    assert "Limaye Srinivasan Tavenas" in prep.query
    assert prep.adjustment_note is not None
    assert "query_adjusted" in prep.adjustment_note
    assert q in prep.adjustment_note
    # 加引号后 unquoted ≤12；venue 可保留或丢掉（优选丢）。
    assert validate_search_query(prep.query) is None


def test_prepare_search_query_no_proper_names_still_errors():
    """无专名超限 → prepare 截断至上限，无 error，带 adjustment_note。"""
    words = " ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT + 1))
    prep = prepare_search_query(words)
    assert prep.error is None
    assert prep.adjustment_note is not None
    assert "已截断至上限" in prep.adjustment_note
    expected = " ".join(f"w{i}" for i in range(_QUERY_LATIN_WORD_LIMIT))
    assert prep.query == expected
    assert validate_search_query(prep.query) is None


def test_prepare_search_query_drops_venue_when_still_over():
    """加引号后仍超限时丢掉尾部 venue/年份。"""
    # 2 专名 + 11 普通词 + STOC + 2024 = 加引号后 unquoted 仍 13 → 需丢尾。
    q = "Alice Bob a b c d e f g h i j k STOC 2024"
    prep = prepare_search_query(q)
    assert prep.error is None
    assert '"Alice Bob"' in prep.query
    assert "STOC" not in prep.query
    assert "2024" not in prep.query
    assert prep.adjustment_note is not None
    assert "会议名" in prep.adjustment_note or "年份" in prep.adjustment_note


async def test_web_search_query_adjusted_academic_hits_backend(monkeypatch):
    """学术超限例：execute 规范化后直搜，output 含实搜词 / original / note。"""
    calls: list[str] = []

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls.append(query)
            return [
                SearchResult(
                    title="paper",
                    url="https://example.com/p",
                    snippet="lower bound permanent",
                )
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    original = (
        "Limaye Srinivasan Tavenas permanent formula lower bound proof "
        "complexity theory algebraic circuit STOC"
    )
    result = await WebSearchTool().execute({"query": original}, _ctx())
    assert result.success is True
    assert len(calls) == 1
    assert '"' in calls[0]
    assert "Limaye Srinivasan Tavenas" in calls[0]
    payload = json.loads(result.output)
    assert payload["query"] == calls[0]
    assert payload.get("original_query") == original
    assert "query_adjusted" in (payload.get("note") or "")
    assert original in (payload.get("note") or "")


async def test_web_search_rejects_unnormalizable_latin_without_backend(monkeypatch):
    """极端过长（绝对字符上限）：execute 仍 reject、不打 backend、contract_failure。"""
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            return []

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    long_q = "x" * (_QUERY_ABSOLUTE_CHAR_LIMIT + 1)
    result = await WebSearchTool().execute({"query": long_q}, _ctx())
    assert result.success is False
    assert result.contract_failure is True
    assert "极端过长" in (result.error or "")
    assert calls["n"] == 0


def test_prepare_search_query_truncates_cjk_over_limit():
    """中文超限：prepare 截断至加权上限，无 error。"""
    overflow = "研" * (_QUERY_CJK_CHAR_LIMIT + 5)
    prep = prepare_search_query(overflow)
    assert prep.error is None
    assert prep.query == "研" * _QUERY_CJK_CHAR_LIMIT
    assert prep.adjustment_note is not None
    assert "已截断至上限" in prep.adjustment_note
    assert validate_search_query(prep.query) is None


def test_validate_search_query_rejects_absolute_over_length():
    """validate 对绝对长度硬拒同样返回文案（纯检查不改写）。"""
    long_q = "x" * (_QUERY_ABSOLUTE_CHAR_LIMIT + 1)
    err = validate_search_query(long_q)
    assert err is not None
    assert "极端过长" in err
    assert str(_QUERY_ABSOLUTE_CHAR_LIMIT) in err

# --- A4: cache key casefold + Latin word-order; debate exact keys ---


def test_query_key_latin_word_order_normalized():
    assert _query_key("Anthropic Funding Round") == _query_key("round funding anthropic")
    assert _query_key("  Hello   World ") == _query_key("world hello")


def test_query_key_exact_skips_word_order_sort():
    # Debate carve-out: exact keys keep token order (still casefold + collapse).
    assert _query_key("funding anthropic", exact=True) != _query_key(
        "anthropic funding", exact=True
    )
    assert _query_key("Funding  Anthropic", exact=True) == _query_key(
        "funding anthropic", exact=True
    )


def test_query_key_cjk_keeps_order():
    # Mixed / CJK queries are not word-order sorted (order is semantic).
    assert _query_key("OpenAI 股权 融资") == "openai 股权 融资"
    assert _query_key("融资 股权 OpenAI") != _query_key("OpenAI 股权 融资")


def test_search_cache_word_order_hit():
    cache = ConversationSearchCache()
    cache.put(_sentry("Anthropic funding round", [_sresult("https://a.com")]))
    assert cache.get("round funding Anthropic", min_results=1) is not None


def test_search_cache_debate_exact_misses_reordered():
    cache = ConversationSearchCache()
    cache.put(
        _sentry("funding anthropic round", [_sresult("https://a.com")]), exact=True
    )
    assert cache.get("round funding anthropic", min_results=1, exact=True) is None
    assert cache.get("funding anthropic round", min_results=1, exact=True) is not None


async def test_web_search_debate_run_uses_exact_cache_key(monkeypatch):
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    calls = {"n": 0}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            calls["n"] += 1
            return [SearchResult("t", "https://a.com", "s")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    tool = WebSearchTool()
    debate_ctx = _ctx(conversation_id="conv-debate", run_id="debate_abc_r1_pro")
    await tool.execute({"query": "funding anthropic round"}, debate_ctx)
    # Same words, different order → exact key miss → second live search.
    await tool.execute({"query": "round funding anthropic"}, debate_ctx)
    assert calls["n"] == 2
    # Non-debate run shares via word-order normalisation.
    normal_ctx = _ctx(conversation_id="conv-normal", run_id="worker_1")
    await tool.execute({"query": "funding anthropic round"}, normal_ctx)
    await tool.execute({"query": "round funding anthropic"}, normal_ctx)
    assert calls["n"] == 3  # only one extra live search for the normal conversation


# --- A5: locale pin end-to-end (Latin→en; mixed CN/EN→zh) ---


def test_infer_search_language_latin_pins_en():
    assert infer_search_language("Anthropic funding round 2024") == "en"
    assert infer_search_language("OpenAI GPT API pricing") == "en"


def test_infer_search_language_mixed_cjk_latin_pins_zh():
    # 中英混排 must stay zh-pinned (not fall through to en / IP locale).
    assert infer_search_language("OpenAI 股权 融资") == "zh"
    assert infer_search_language("LV 商标 近似 驳回") == "zh"
    assert infer_search_language("AI 产业 报告 2024") == "zh"


async def test_web_search_passes_inferred_language_to_backend(monkeypatch):
    captured: dict = {}

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            captured["language"] = language
            captured["query"] = query
            return [SearchResult("t", "https://en.example.com", "s")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    await WebSearchTool().execute({"query": "Anthropic funding round"}, _ctx())
    assert captured["language"] == "en"
    await WebSearchTool().execute({"query": "OpenAI 股权分析"}, _ctx())
    assert captured["language"] == "zh"


async def test_searxng_latin_query_sends_language_en_not_auto(monkeypatch):
    """Wire pin: pure Latin → language=en; never omit / never default_lang=auto."""
    host = "localhost"
    _net._states.pop(host, None)
    captured: dict = {}
    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    class _Client:
        async def get(self, url, params=None):
            captured["params"] = dict(params or {})
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = SearXNGBackend("http://localhost:18888")
    lang = infer_search_language("Anthropic funding round")
    assert lang == "en"
    await backend.search("Anthropic funding round", max_results=3, language=lang)
    assert captured["params"]["language"] == "en"
    assert "default_lang" not in captured["params"]
    assert captured["params"]["q"] == "Anthropic funding round"


async def test_searxng_mixed_query_sends_language_zh(monkeypatch):
    host = "localhost"
    _net._states.pop(host, None)
    captured: dict = {}
    req = httpx.Request("GET", "http://localhost:18888/search")
    payload = {"results": [{"url": "https://e.com/a", "title": "A", "content": "x"}]}

    class _Client:
        async def get(self, url, params=None):
            captured["params"] = dict(params or {})
            return httpx.Response(200, json=payload, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = SearXNGBackend("http://localhost:18888")
    q = "OpenAI 股权 融资"
    lang = infer_search_language(q)
    assert lang == "zh"
    await backend.search(q, max_results=3, language=lang)
    assert captured["params"]["language"] == "zh"
    assert "default_lang" not in captured["params"]


async def test_tavily_latin_query_country_united_states(monkeypatch):
    captured: dict = {}
    req = httpx.Request("POST", "https://api.tavily.com/search")

    class _Client:
        async def post(self, url, *, json=None, headers=None):
            captured["json"] = dict(json or {})
            return httpx.Response(200, json={"results": []}, request=req)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _Client())
    backend = TavilyBackend(api_key="tvly-test", base_url="https://api.tavily.com")
    await backend.search("Anthropic funding", language="en")
    assert captured["json"]["country"] == "united states"


# --- A6: structured phase-duration logging via on_phase ---


def test_track_phase_durations_logs_each_phase(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(search_backend_mod, "logger", spy)
    forwarded: list[str] = []
    on_phase, finish = track_phase_durations(forwarded.append)
    on_phase("queued")
    time.sleep(0.01)
    on_phase("querying")
    time.sleep(0.01)
    on_phase("fallback")
    time.sleep(0.01)
    finish()
    assert forwarded == ["queued", "querying", "fallback"]
    events = [name for name, _ in spy.events if name == "search.phase_duration"]
    assert events == ["search.phase_duration"] * 3
    phases = [kw["phase"] for name, kw in spy.events if name == "search.phase_duration"]
    assert phases == ["queued", "querying", "fallback"]
    for _name, kw in spy.events:
        if _name == "search.phase_duration":
            assert isinstance(kw["duration_ms"], int)
            assert kw["duration_ms"] >= 0


async def test_web_search_logs_phase_duration_fields(monkeypatch):
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(search_backend_mod, "logger", spy)

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            if on_phase:
                on_phase("querying")
                time.sleep(0.01)
            return [SearchResult("t", "https://a.com", "s")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())
    phases: list[str] = []
    result = await WebSearchTool().execute(
        {"query": "weather"}, _ctx(on_phase=phases.append)
    )
    assert result.success is True
    assert "querying" in phases
    dur = spy.get("search.phase_duration")
    assert dur["phase"] == "querying"
    assert isinstance(dur["duration_ms"], int)
    assert dur["duration_ms"] >= 0


# --- ToolResult.output_limit ---


def test_tool_result_default_truncates_head_and_tail_at_4000():
    body = "HEAD起始" + ("a" * 5000) + "TAIL尾注金额￥999"
    r = ToolResult(tool_call_id="", success=True, output=body)
    assert r.output.startswith("HEAD起始")  # head kept
    assert r.output.endswith("TAIL尾注金额￥999")  # tail kept — head-only used to drop it
    assert "系统视图截断" in r.output  # transport elision between the ends
    assert len(r.output) <= 4000


def test_tool_result_respects_higher_output_limit():
    body = "a" * 5000
    r = ToolResult(tool_call_id="", success=True, output=body, output_limit=8000)
    assert r.output == body  # under budget → untouched


def test_tool_result_custom_lower_limit_keeps_both_ends():
    body = "HEAD" + ("a" * 1000) + "TAIL"
    r = ToolResult(tool_call_id="", success=True, output=body, output_limit=200)
    assert r.output.startswith("HEAD")
    assert r.output.endswith("TAIL")
    assert len(r.output) <= 200


# --- read_url output budget: json escaping must not chop the envelope ---


def _newline_dense_html(blocks: int) -> str:
    """A page whose extracted text is ~2/3 newlines (每个 ``</p></div>`` 各产一个 ``\\n``).

    Escaped by ``json.dumps`` each ``\\n`` costs two chars, so the raw-char budget
    (``max_chars``) badly under-counts the serialized payload — the TOOL-A2 shape.
    """
    body = "<div><p>行</p></div>" * blocks
    return f"<html><head><title>密集换行页</title></head><body>{body}</body></html>"


async def test_read_url_escape_heavy_page_stays_valid_json_and_declares_cut(monkeypatch):
    """转义膨胀超预算：仍是合法 JSON + 截断事实在带内可见（不许伪装成完整正文）。"""
    max_chars = 3000
    html = _newline_dense_html(2000)

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute(
        {"url": "https://dense.example.com/table", "max_chars": max_chars}, _ctx()
    )

    assert result.success is True
    # Pre-fix: the dump blew ``max_chars + 1024`` and ToolResult head+tail chopped it,
    # so the model got a JSON object sliced through the middle (and no error field).
    payload = json.loads(result.output)
    assert "系统视图截断" not in result.output  # no character-level cut of the envelope
    assert payload["url"] == "https://dense.example.com/table"
    assert payload["title"] == "密集换行页"
    assert payload["content"]  # a real prefix survives, not an empty body
    # The cut is a stated fact — in-band for the model and in metadata for logs.
    assert payload["truncated"] is True
    assert "截断" in payload["note"]
    assert result.metadata["output_truncated"] is True
    # Budget actually holds now that it is spent on the SERIALIZED form.
    assert len(result.output) <= max_chars + read_url_mod._OUTPUT_ENVELOPE_SLACK
    assert result.output_limit >= len(result.output)  # ToolResult must not re-trim


async def test_read_url_plain_page_not_flagged_truncated(monkeypatch):
    """预算内的页面不加 truncated/note（截断标记必须只在真截断时出现）。"""
    html = "<html><head><title>短页</title></head><body><p>正文内容</p></body></html>"

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    result = await ReadUrlTool().execute({"url": "https://short.example.com/p"}, _ctx())

    payload = json.loads(result.output)
    assert payload == {
        "url": "https://short.example.com/p",
        "title": "短页",
        "content": "正文内容",
    }
    assert result.metadata["output_truncated"] is False


async def test_read_url_cache_hit_shares_the_output_budget(monkeypatch):
    """缓存命中走同一预算收口：命中结果同样是合法 JSON + 带内截断声明。"""
    max_chars = 3000
    html = _newline_dense_html(2000)
    calls = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        calls["n"] += 1
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    ctx = _ctx(conversation_id="conv-dense-budget")
    tool = ReadUrlTool()
    args = {"url": "https://dense.example.com/cached", "max_chars": max_chars}
    await tool.execute(args, ctx)
    hit = await tool.execute(args, ctx)

    assert calls["n"] == 1
    assert hit.metadata["cached"] is True
    payload = json.loads(hit.output)
    assert payload["truncated"] is True
    assert hit.metadata["output_truncated"] is True
    assert len(hit.output) <= max_chars + read_url_mod._OUTPUT_ENVELOPE_SLACK
    assert hit.output_limit >= len(hit.output)


def test_page_output_keeps_json_valid_when_envelope_alone_exceeds_budget():
    """退化情形（超长 title/url 撑爆信封）：宁可超预算，也不交半截 JSON。"""
    output, truncated = read_url_mod._page_output(
        url="https://x.example.com/" + ("p" * 500),
        title="标题" * 500,
        text="正文" * 500,
        limit=200,
    )
    assert truncated is True
    payload = json.loads(output)  # parseable even though the envelope cannot fit
    assert payload["content"] == ""  # nothing of the body survived — and it says so
    assert payload["truncated"] is True
    r = ToolResult(
        tool_call_id="",
        success=True,
        output=output,
        output_limit=max(200, len(output)),
    )
    assert json.loads(r.output) == payload  # the caller's max() keeps it intact


# --- citations: site_of + tool wiring + cross-round dedup ---


def test_site_of_strips_www_and_lowercases():
    assert site_of("https://www.Example.com/path?q=1") == "example.com"
    assert site_of("https://news.site.cn/a") == "news.site.cn"
    assert site_of("https://1.1.1.1/x") == "1.1.1.1"
    assert site_of("not a url") == ""


async def test_web_search_emits_structured_citations(monkeypatch):
    class _FakeBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [
                SearchResult("深圳天气日报", "https://www.example.com/a", "深圳今日天气晴"),
                SearchResult("深圳天气指数", "https://b.cn/p", "深圳天气实况摘要"),
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _FakeBackend())
    result = await WebSearchTool().execute({"query": "深圳天气"}, _ctx())

    assert result.success is True
    assert result.citations == [
        {
            "url": "https://www.example.com/a",
            "title": "深圳天气日报",
            "snippet": "深圳今日天气晴",
            "site": "example.com",
            "query": "深圳天气",
            "tier": "unknown",
        },
        {
            "url": "https://b.cn/p",
            "title": "深圳天气指数",
            "snippet": "深圳天气实况摘要",
            "site": "b.cn",
            "query": "深圳天气",
            "tier": "unknown",
        },
    ]


async def test_web_search_emits_structured_display(monkeypatch):
    # 工具结果富渲染: the client renders the hits as cards from ``display`` (not the
    # JSON output), so it carries each hit's title/url/snippet + parsed site.
    class _FakeBackend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [
                SearchResult("深圳天气日报", "https://www.example.com/a", "深圳今日天气晴"),
                SearchResult("深圳天气指数", "https://b.cn/p", "深圳天气实况摘要"),
            ]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _FakeBackend())
    result = await WebSearchTool().execute({"query": "深圳天气"}, _ctx())

    assert result.success is True
    assert result.display == {
        "query": "深圳天气",
        "results": [
            {
                "title": "深圳天气日报",
                "url": "https://www.example.com/a",
                "snippet": "深圳今日天气晴",
                "site": "example.com",
            },
            {
                "title": "深圳天气指数",
                "url": "https://b.cn/p",
                "snippet": "深圳天气实况摘要",
                "site": "b.cn",
            },
        ],
    }


def test_merge_citations_dedups_across_rounds_by_normalized_url():
    sink: list[dict] = []
    first = merge_citations(sink, [{"url": "https://a.com/p", "title": "A", "site": "a.com"}])
    assert first == {"https://a.com/p": 1}
    # same page (trailing slash + fragment) from a later round + a fresh source
    second = merge_citations(
        sink,
        [
            {"url": "https://a.com/p/#sec", "title": "A again", "site": "a.com"},
            {"url": "https://b.com", "title": "B", "site": "b.com"},
        ],
    )
    assert [c["url"] for c in sink] == ["https://a.com/p", "https://b.com"]
    # A2: the dup reuses source #1's number; the fresh source gets the next card
    # index — numbers stay stable across rounds so body [n] keeps pointing right.
    assert second == {"https://a.com/p": 1, "https://b.com": 2}


def test_merge_citations_skips_blank_url_no_hard_cap():
    sink: list[dict] = []
    blank = merge_citations(sink, [{"url": "", "title": "blank"}])
    assert sink == []
    assert blank == {}  # a blank URL yields no card and no number
    numbers = merge_citations(
        sink, [{"url": f"https://s{i}.com", "title": str(i)} for i in range(50)]
    )
    # P2：池帽 24 退役；mid-turn sink 无硬帽（卡片投影另按 cited_ids）
    assert len(sink) == 50
    assert len(numbers) == 50
    assert numbers["https://s0.com"] == 1
    assert numbers["https://s23.com"] == 24
    assert numbers["https://s49.com"] == 50


def test_annotate_tool_citations_appends_assigned_numbers():
    cites = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://b.com", "title": "B"},
    ]
    numbers = {"https://a.com": 1, "https://b.com": 2}
    out = annotate_tool_citations("RESULT", cites, numbers)
    assert out.startswith("RESULT")
    assert "[来源编号]" in out
    assert "[1]=https://a.com" in out
    assert "[2]=https://b.com" in out


def test_annotate_tool_citations_omits_capped_and_dedups_by_number():
    cites = [
        {"url": "https://a.com", "title": "A"},
        {"url": "https://a.com/#frag", "title": "A dup"},  # same card → one entry
        {"url": "https://x.com", "title": "X"},  # dropped by cap → no number
    ]
    numbers = {"https://a.com": 1}
    out = annotate_tool_citations("R", cites, numbers)
    assert out.count("[1]=") == 1
    assert "x.com" not in out


def test_annotate_tool_citations_no_numbers_leaves_content_unchanged():
    assert annotate_tool_citations("R", [{"url": "https://a.com"}], {}) == "R"


# --- read_url conversation-scoped fetch cache (P2) ---


async def test_read_url_caches_within_conversation(monkeypatch):
    html = (
        "<html><head><title>缓存页</title>"
        '<meta name="description" content="摘要">'
        "</head><body><p>正文内容</p></body></html>"
    )
    calls = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        calls["n"] += 1
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    ctx = _ctx(conversation_id="conv-cache-hit")
    tool = ReadUrlTool()
    r1 = await tool.execute({"url": "https://x.example.com/p"}, ctx)
    r2 = await tool.execute({"url": "https://x.example.com/p"}, ctx)

    assert r1.success and r2.success
    assert calls["n"] == 1  # second read served from cache, no re-fetch
    assert r1.metadata.get("cached") is not True
    assert r2.metadata.get("cached") is True
    # the cached hit preserves content + citation metadata
    assert "正文内容" in r2.output
    assert r2.citations[0]["title"] == "缓存页"
    assert r2.citations[0]["snippet"] == "摘要"
    assert r2.citations[0]["site"] == "x.example.com"
    # cache hit also emits a full display (same shape as a fresh fetch)
    assert r2.display == {
        "url": "https://x.example.com/p",
        "title": "缓存页",
        "site": "x.example.com",
        "snippet": "摘要",
        "content": "正文内容",
    }
    # same page via trailing slash + fragment normalises to the same cache key
    r3 = await tool.execute({"url": "https://x.example.com/p/#sec"}, ctx)
    assert calls["n"] == 1
    assert r3.metadata.get("cached") is True


async def test_read_url_skips_cache_without_conversation(monkeypatch):
    html = "<html><head><title>T</title></head><body><p>x</p></body></html>"
    calls = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        calls["n"] += 1
        return httpx.Response(200, html=html, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    tool = ReadUrlTool()
    await tool.execute({"url": "https://nocache.example.com/a"}, _ctx())
    await tool.execute({"url": "https://nocache.example.com/a"}, _ctx())
    assert calls["n"] == 2  # unscoped (conversation_id == "") → no caching, fetched twice


async def test_read_url_cache_refetches_when_more_chars_needed(monkeypatch):
    body = "<html><body><p>" + ("z" * 500) + "</p></body></html>"
    calls = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        calls["n"] += 1
        return httpx.Response(200, html=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(read_url_mod, "_classify_url", _allow)
    monkeypatch.setattr(read_url_mod, "_safe_request", _fake_request)

    ctx = _ctx(conversation_id="conv-cache-chars")
    tool = ReadUrlTool()
    # first read captures only 100 chars (truncated); a later read needing 400 must
    # re-fetch with the bigger budget rather than serve the short cached copy.
    await tool.execute({"url": "https://big.example.com/p", "max_chars": 100}, ctx)
    assert calls["n"] == 1
    r2 = await tool.execute({"url": "https://big.example.com/p", "max_chars": 400}, ctx)
    assert calls["n"] == 2
    assert r2.metadata.get("cached") is not True
    # now 400 chars are cached → a <=400 request hits without re-fetching
    r3 = await tool.execute({"url": "https://big.example.com/p", "max_chars": 300}, ctx)
    assert calls["n"] == 2
    assert r3.metadata.get("cached") is True


def _entry(
    url: str, content: str = "body", *, max_chars: int = 8000, truncated: bool = False
) -> UrlCacheEntry:
    return UrlCacheEntry(
        url=url,
        title="T",
        content=content,
        snippet="s",
        site=site_of(url),
        max_chars=max_chars,
        truncated=truncated,
        stored_at=time.time(),
    )


def test_url_cache_get_put_and_key_normalization():
    cache = ConversationUrlCache()
    cache.put(_entry("https://a.com/p"))
    # trailing slash + fragment normalise to the same key as the stored URL
    assert cache.get("https://a.com/p/#frag", min_chars=8000) is not None
    assert "https://a.com/p" in cache
    assert cache.get("https://other.com", min_chars=1) is None


def test_url_cache_truncated_entry_needs_refetch_for_more_chars():
    cache = ConversationUrlCache()
    cache.put(_entry("https://a.com", content="x" * 100, max_chars=100, truncated=True))
    assert cache.get("https://a.com", min_chars=100) is not None  # enough captured
    assert cache.get("https://a.com", min_chars=200) is None  # wants more than captured


def test_url_cache_full_entry_serves_any_char_request():
    cache = ConversationUrlCache()
    cache.put(_entry("https://a.com", content="short", truncated=False))
    # a non-truncated entry holds the whole page → even a larger ask is a hit
    assert cache.get("https://a.com", min_chars=99999) is not None


def test_url_cache_expires_after_ttl():
    cache = ConversationUrlCache(ttl_seconds=10.0)
    stale = _entry("https://a.com")
    stale.stored_at = time.time() - 100  # older than the TTL window
    cache.put(stale)
    assert cache.get("https://a.com", min_chars=1) is None


def test_url_cache_lru_evicts_over_count():
    cache = ConversationUrlCache(max_entries=2)
    for i in range(3):
        cache.put(_entry(f"https://s{i}.com", content="x"))
    assert len(cache) == 2
    assert "https://s0.com" not in cache  # oldest evicted
    assert "https://s2.com" in cache


def test_url_cache_lru_evicts_over_bytes():
    cache = ConversationUrlCache(max_bytes=10)
    cache.put(_entry("https://a.com", content="x" * 8))
    cache.put(_entry("https://b.com", content="y" * 8))  # total 16 > 10
    assert "https://a.com" not in cache  # oldest evicted to fit the byte budget
    assert "https://b.com" in cache


def test_url_cache_registry_scopes_per_conversation():
    reg = UrlCacheRegistry()
    c1 = reg.get_or_create("conv1")
    c2 = reg.get_or_create("conv2")
    assert c1 is not c2
    assert reg.get_or_create("conv1") is c1  # stable per conversation


def test_url_cache_registry_caps_conversation_count_lru():
    reg = UrlCacheRegistry(max_conversations=2)
    reg.get_or_create("a")
    reg.get_or_create("b")
    reg.get_or_create("c")
    assert len(reg) == 2
    assert "a" not in reg  # LRU-evicted
    assert "c" in reg


def test_url_cache_registry_reaps_idle_conversation(monkeypatch: pytest.MonkeyPatch):
    recorded: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        url_cache_mod.logger,
        "info",
        lambda event, **kwargs: recorded.append((event, dict(kwargs))),
    )
    reg = UrlCacheRegistry(conversation_ttl_seconds=10.0)
    idle = reg.get_or_create("idle")
    idle.last_access = time.time() - 100  # force past the idle window
    reg.get_or_create("fresh")  # creation triggers idle reaping
    assert "idle" not in reg
    assert "fresh" in reg
    assert recorded == [
        ("url_cache.conversation_evicted", {"evicted_conversation_id": "idle"}),
    ]


def test_stop_read_hint_aligns_closing_with_howto_path_honesty():
    """howto A′：读失败收口 ≠ 可伪精确逐步菜单（与 prompt claim_evidence 对齐）。"""
    from agentcore.tools.builtin.web.read_url import _STOP_READ_HINT

    assert "收口" in _STOP_READ_HINT
    assert "伪精确" in _STOP_READ_HINT or "逐步菜单" in _STOP_READ_HINT
    assert "易变" in _STOP_READ_HINT or "待实测" in _STOP_READ_HINT
    # C2: per-failure trailer must not name web_search as the default next move.
    assert "web_search" not in _STOP_READ_HINT


def test_read_url_retire_steer_closes_web_search_thrash():
    """Retirement copy must close «继续 web_search» — not point at more search."""
    from agentcore.tools.builtin.web._net import READ_URL_RETIRE_STEER

    assert "停用" in READ_URL_RETIRE_STEER
    assert "收束继续 web_search" in READ_URL_RETIRE_STEER
    assert "不要把继续检索当默认出路" in READ_URL_RETIRE_STEER
    assert "基于已有材料" in READ_URL_RETIRE_STEER


def test_search_notes_skip_read_url_nudge_when_retired():
    """After read_url retirement, empty/hit search notes must not urge deep-read."""
    from agentcore.tools.builtin.web._net import (
        POST_READ_RETIRE_SEARCH_HINT,
        clear_read_url_retired,
        consume_post_read_retire_search_hint,
        mark_read_url_retired,
    )
    from agentcore.tools.builtin.web.search import (
        _empty_result_note,
        _strategy_change_note,
    )

    run_id = "search-after-read-retire"
    clear_read_url_retired(run_id)
    mark_read_url_retired(run_id)

    empty = _empty_result_note("q", empty_streak=2, read_url_retired=True)
    assert "先对已有命中 read_url" not in empty
    assert "勿再催 read_url" in empty
    assert "继续检索当默认出路" in empty

    weak = _strategy_change_note(empty_streak=2, read_url_retired=True)
    assert "先 read_url" not in weak
    assert "勿再催 read_url" in weak

    hint = consume_post_read_retire_search_hint(run_id)
    assert hint == POST_READ_RETIRE_SEARCH_HINT
    assert consume_post_read_retire_search_hint(run_id) is None  # one-shot
    clear_read_url_retired(run_id)
