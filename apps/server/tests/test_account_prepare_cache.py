"""Account prepare rules/memory snapshot cache (cache_only + warm seed)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agentcore.account.credentials import (
    AccountCredentials,
    account_credentials_scope,
)
from agentcore.memory import account_prepare_cache
from agentcore.memory.account_prepare_cache import (
    AccountPrepareSnapshot,
    account_rules_memory_ttl_remaining,
    clear_account_rules_memory_cache,
    get_account_rules_memory_snapshot,
    hibernate_folder_injection_cache,
    prepare_account_folder_id,
    prepare_reads_cache_only,
    seed_account_rules_memory_cache,
    warm_account_rules_memory,
)
from agentcore.memory.document_store import DocumentMemoryStore
from agentcore.memory.injection import MemoryTopic, load_memory_topics
from agentcore.memory.rules_injection import assemble_turn_rules, load_on_demand_user_rules
from agentcore.sidecar.protocol import INVALID_REQUEST, NOT_INITIALIZED
from agentcore.sidecar.server import SidecarServer

pytestmark = pytest.mark.anyio


@pytest.fixture
def account_creds() -> AccountCredentials:
    return AccountCredentials(
        api_key="account-jwt",
        base_url="https://example.test/v1/account",
    )


class _EmptyMemoryStore:
    async def list(self, *_a, **_k):
        return []

    async def load(self, *_a, **_k):
        return ""


class _Clock:
    """Stand-in for the cache module's ``time`` (only ``monotonic`` is read there)."""

    def __init__(self, start: float = 10_000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@pytest.fixture
def cache_clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    """Hand-driven clock for TTL lapse: real turns sit minutes apart, tests can't wait."""
    clock = _Clock()
    monkeypatch.setattr(account_prepare_cache, "time", clock)
    return clock


def _injectable_snapshot(*, degraded: bool = False) -> AccountPrepareSnapshot:
    """A snapshot whose every part is visible in the assembled turn."""
    return AccountPrepareSnapshot(
        rules_payload={
            "global_rules": [{"name": "用户规则.md", "content": "- 总是用中文回答"}],
            "project_rules": [{"name": "项目规则.md", "content": "- 项目规则"}],
            "global_on_demand_rules": [{"name": "合规.md", "content": "- 合规摘要行\n"}],
            "project_on_demand_rules": [],
        },
        memory_bodies={
            ("", "偏好.md"): "- 沟通偏好\n",
            ("F1", "画像.md"): "- 项目画像\n",
        },
        memory_topics=(MemoryTopic(name="api", summary="API 约定"),),
        degraded=degraded,
    )


async def _prepare_injection(user_id: str, folder_id: str | None):
    """What prepare would inject right now: (rules markdown, topics, on-demand rules)."""
    rules_md = await assemble_turn_rules(
        _EmptyMemoryStore(),  # type: ignore[arg-type]
        user_id,
        folder_id=folder_id,
        enabled=True,
    )
    topics = await load_memory_topics(
        _EmptyMemoryStore(),  # type: ignore[arg-type]
        user_id,
        folder_id=folder_id,
        enabled=True,
    )
    on_demand = await load_on_demand_user_rules(user_id, folder_id=folder_id)
    return rules_md, topics, on_demand


def _forbid_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any cloud read under ticketed prepare is a contract break (cache_only)."""

    async def _boom(*_a, **_k):
        raise AssertionError("cloud must not be called under prepare_reads_cache_only")

    for name in ("cloud_list_user_rules", "cloud_memory_list", "cloud_memory_load"):
        monkeypatch.setattr(f"agentcore.account.credentials.{name}", _boom)


async def test_ticketed_miss_skips_cloud(monkeypatch: pytest.MonkeyPatch, account_creds):
    clear_account_rules_memory_cache()
    calls: list[str] = []

    async def _rules(*_a, **_k):
        calls.append("rules")
        raise AssertionError("unexpected cloud call: rules")

    async def _mem_list(*_a, **_k):
        calls.append("mem_list")
        raise AssertionError("unexpected cloud call: mem_list")

    async def _mem_load(*_a, **_k):
        calls.append("mem_load")
        raise AssertionError("unexpected cloud call: mem_load")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _rules
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_memory_list", _mem_list
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_memory_load", _mem_load
    )

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        topics = await load_memory_topics(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        on_demand = await load_on_demand_user_rules("u1", folder_id="F1")

    assert rules_md == ""
    assert topics == []
    assert on_demand == []
    assert calls == []


async def test_seed_then_hit(account_creds):
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        "F1",
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
                "project_rules": [
                    {"name": "项目规则.md", "content": "- 项目规则"},
                ],
                "global_on_demand_rules": [
                    {"name": "合规.md", "content": "- 合规摘要行\n更多"},
                ],
                "project_on_demand_rules": [],
            },
            memory_bodies={
                ("", "偏好.md"): "- 沟通偏好\n",
                ("", "画像.md"): "- 全局画像\n",
                ("F1", "画像.md"): "- 项目画像\n",
                ("F1", "导航.md"): "- 项目导航\n",
            },
            memory_topics=(MemoryTopic(name="api", summary="API 约定"),),
        ),
    )

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        topics = await load_memory_topics(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        on_demand = await load_on_demand_user_rules("u1", folder_id="F1")

    assert "全局规则" in rules_md
    assert "项目规则" in rules_md
    assert "沟通偏好" in rules_md
    assert "项目画像" in rules_md
    assert "项目导航" in rules_md
    assert topics == [MemoryTopic(name="api", summary="API 约定")]
    assert len(on_demand) == 1
    assert on_demand[0].name == "合规"


async def test_folder_none_key_distinct_from_project(account_creds):
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        None,
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- bare"}],
            }
        ),
    )
    seed_account_rules_memory_cache(
        "u1",
        "F1",
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- project"}],
            }
        ),
    )
    bare = get_account_rules_memory_snapshot("u1", None)
    proj = get_account_rules_memory_snapshot("u1", "F1")
    assert bare is not None and "bare" in str(bare.rules_payload)
    assert proj is not None and "project" in str(proj.rules_payload)


async def test_snapshot_lapses_after_ttl_and_prepare_injects_nothing(
    monkeypatch: pytest.MonkeyPatch, account_creds, cache_clock: _Clock
):
    """Past the 300s TTL every rule + memory silently drops out of the turn.

    Characterizes the user-visible failure ("AI 突然失忆、不守规矩"): prepare stays
    cache-only on the miss, so nothing re-fetches — only a re-warm can restore it.
    """
    clear_account_rules_memory_cache()
    _forbid_cloud(monkeypatch)
    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())

    with account_credentials_scope(account_creds):
        rules_md, topics, on_demand = await _prepare_injection("u1", "F1")
    assert "总是用中文回答" in rules_md
    assert "项目画像" in rules_md
    assert topics == [MemoryTopic(name="api", summary="API 约定")]
    assert len(on_demand) == 1

    cache_clock.advance(299.0)
    assert get_account_rules_memory_snapshot("u1", "F1") is not None

    cache_clock.advance(2.0)
    assert get_account_rules_memory_snapshot("u1", "F1") is None
    with account_credentials_scope(account_creds):
        rules_md, topics, on_demand = await _prepare_injection("u1", "F1")
    assert rules_md == ""
    assert topics == []
    assert on_demand == []


async def test_degraded_snapshot_lapses_on_the_short_negative_ttl(
    account_creds, cache_clock: _Clock
):
    """Degraded seeds get 30s, healthy ones 300s — renewal must track each entry."""
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot(degraded=True))
    seed_account_rules_memory_cache("u1", None, _injectable_snapshot())

    cache_clock.advance(29.0)
    assert get_account_rules_memory_snapshot("u1", "F1") is not None

    cache_clock.advance(2.0)
    assert get_account_rules_memory_snapshot("u1", "F1") is None
    assert get_account_rules_memory_snapshot("u1", None) is not None


async def test_ttl_remaining_is_the_renewal_deadline(cache_clock: _Clock):
    """The number handed to the warmer must be this entry's real remaining life."""
    clear_account_rules_memory_cache()
    assert account_rules_memory_ttl_remaining("u1", "F1") == 0.0  # never seeded

    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())
    assert account_rules_memory_ttl_remaining("u1", "F1") == pytest.approx(300.0)

    cache_clock.advance(120.0)
    assert account_rules_memory_ttl_remaining("u1", "F1") == pytest.approx(180.0)

    # Renewing inside the window resets the deadline (no drift toward expiry).
    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())
    assert account_rules_memory_ttl_remaining("u1", "F1") == pytest.approx(300.0)

    cache_clock.advance(300.0)
    assert account_rules_memory_ttl_remaining("u1", "F1") == 0.0
    assert get_account_rules_memory_snapshot("u1", "F1") is None

    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot(degraded=True))
    assert account_rules_memory_ttl_remaining("u1", "F1") == pytest.approx(30.0)


async def test_rewarm_after_lapse_restores_injection(
    monkeypatch: pytest.MonkeyPatch, account_creds, cache_clock: _Clock
):
    """Closure: the warmer re-running past the TTL brings rules/memory back."""
    clear_account_rules_memory_cache()

    async def _rules(creds, *, folder_id):
        return {
            "global_rules": [{"name": "用户规则.md", "content": "- 总是用中文回答"}],
            "project_rules": [],
            "global_on_demand_rules": [],
            "project_on_demand_rules": [],
        }

    async def _mem_list(creds, *, scope):
        return [{"path": "偏好.md", "version": "1"}] if scope is None else []

    async def _mem_load(creds, *, path, scope):
        return "- 沟通偏好\n"

    async def _scope_state(creds, *, scope):
        return {"last_semantic_at": None}

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_list_user_rules", _rules
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_list", _mem_list
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_load", _mem_load
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_scope_state_get",
        _scope_state,
    )

    await warm_account_rules_memory(account_creds, user_id="u1", folder_id="F1")
    cache_clock.advance(301.0)
    with account_credentials_scope(account_creds):
        lapsed_md, _, _ = await _prepare_injection("u1", "F1")
    assert lapsed_md == ""

    await warm_account_rules_memory(account_creds, user_id="u1", folder_id="F1")
    assert account_rules_memory_ttl_remaining("u1", "F1") == pytest.approx(300.0)
    with account_credentials_scope(account_creds):
        renewed_md, _, _ = await _prepare_injection("u1", "F1")
    assert "总是用中文回答" in renewed_md
    assert "沟通偏好" in renewed_md


async def test_keepalive_rewarm_keeps_harvest_cache_only_hit_past_ttl(
    monkeypatch: pytest.MonkeyPatch, account_creds, cache_clock: _Clock
):
    """Desktop TTL keepalive: re-seed before expiry so a later harvest still hits.

    Without the mid-window re-warm, 280s + 280s would lapse the first seed (300s
    TTL) and a cache_only harvest prepare would inject nothing.
    """
    clear_account_rules_memory_cache()
    _forbid_cloud(monkeypatch)
    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())

    cache_clock.advance(280.0)
    assert get_account_rules_memory_snapshot("u1", "F1") is not None

    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())
    cache_clock.advance(280.0)
    snap = get_account_rules_memory_snapshot("u1", "F1")
    assert snap is not None
    with account_credentials_scope(account_creds):
        from agentcore.runtime.delegate.post_close_gate import (
            bind_user_message_origin,
            reset_user_message_origin,
        )

        token = bind_user_message_origin("execution_harvest")
        try:
            rules_md, _, _ = await _prepare_injection("u1", "F1")
        finally:
            reset_user_message_origin(token)
    assert "总是用中文回答" in rules_md


async def test_cache_miss_logs_harvest_origin_for_empty_injection(
    monkeypatch: pytest.MonkeyPatch,
):
    """Lapsed / missing snapshot under harvest origin is grep-able, not silent."""
    clear_account_rules_memory_cache()
    events: list[tuple[str, dict]] = []

    def _capture(event: str, **kwargs):
        events.append((event, kwargs))

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.logger.info", _capture
    )
    from agentcore.runtime.delegate.post_close_gate import (
        bind_user_message_origin,
        reset_user_message_origin,
    )

    token = bind_user_message_origin("execution_harvest")
    try:
        assert get_account_rules_memory_snapshot("u1", "F1") is None
    finally:
        reset_user_message_origin(token)
    miss = [e for e in events if e[0] == "account.rules_memory_cache_miss"]
    assert len(miss) == 1
    assert miss[0][1]["origin"] == "execution_harvest"
    assert miss[0][1]["user_id"] == "u1"
    assert miss[0][1]["folder_id"] == "F1"


async def test_warm_rules_list_once_and_seeds(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    clear_account_rules_memory_cache()
    rules_calls = {"n": 0}

    async def _rules(creds, *, folder_id):
        rules_calls["n"] += 1
        assert folder_id == "F1"
        return {
            "global_rules": [{"name": "用户规则.md", "content": "- r"}],
            "project_rules": [],
            "global_on_demand_rules": [
                {"name": "附录.md", "content": "- od\n"},
            ],
            "project_on_demand_rules": [],
        }

    async def _mem_list(creds, *, scope):
        if scope is None:
            return [
                {"path": "偏好.md", "version": "1"},
                {"path": "主题/foo.md", "version": "1"},
            ]
        return [{"path": "画像.md", "version": "1"}]

    async def _mem_load(creds, *, path, scope):
        return f"# {path}\n- body for {scope}\n"

    async def _scope_state(creds, *, scope):
        return {
            "last_semantic_at": None,
            "explore_workspace_key": None,
            "explore_fingerprint": None,
            "explore_fingerprint_dirty": False,
        }

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_list_user_rules", _rules
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_list", _mem_list
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_load", _mem_load
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_scope_state_get",
        _scope_state,
    )

    snap = await warm_account_rules_memory(
        account_creds, user_id="u1", folder_id="F1"
    )
    assert rules_calls["n"] == 1
    assert snap.degraded is False
    assert get_account_rules_memory_snapshot("u1", "F1") is snap or (
        get_account_rules_memory_snapshot("u1", "F1") is not None
    )

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        topics = await load_memory_topics(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        on_demand = await load_on_demand_user_rules("u1", folder_id="F1")

    assert " - r" in rules_md or "r" in rules_md
    assert "偏好" in rules_md or "body" in rules_md
    assert any(t.name == "foo" for t in topics)
    assert len(on_demand) == 1
    assert rules_calls["n"] == 1  # prepare did not re-hit cloud


def _recorder() -> tuple[list[dict[str, Any]], Any]:
    sent: list[dict[str, Any]] = []

    async def write_line(line: str) -> None:
        sent.append(json.loads(line))

    return sent, write_line


async def _flush_pending(server: SidecarServer) -> None:
    pending = [t for t in list(server._pending_sends) if not t.done()]
    if pending:
        await asyncio.gather(*pending)


def test_warm_account_rules_memory_requires_initialize(tmp_path: Path) -> None:
    clear_account_rules_memory_cache()
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": None,
                        "accountAuth": {
                            "baseUrl": "https://example.test/v1/account",
                            "apiKey": "k",
                        },
                    },
                }
            )
        )

    asyncio.run(run())
    err = next(m for m in sent if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == NOT_INITIALIZED


def test_warm_account_rules_memory_requires_account(tmp_path: Path) -> None:
    clear_account_rules_memory_cache()
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "local",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {"folderId": "F1"},
                }
            )
        )

    asyncio.run(run())
    err = next(m for m in sent if m.get("id") == 2 and "error" in m)
    assert err["error"]["code"] == INVALID_REQUEST


def test_warm_account_rules_memory_seeds_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clear_account_rules_memory_cache()
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def _warm(creds, *, user_id, folder_id):
        snap = AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- warm"}],
            },
            memory_topics=(MemoryTopic(name="t", summary="s"),),
        )
        seed_account_rules_memory_cache(user_id, folder_id, snap)
        return snap

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.warm_account_rules_memory",
        _warm,
    )

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "user-1",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": "F1",
                        "accountAuth": {
                            "baseUrl": "https://example.test/v1/account",
                            "apiKey": "k",
                        },
                    },
                }
            )
        )
        await _flush_pending(server)

    asyncio.run(run())
    init = next(m for m in sent if m.get("id") == 1)
    assert init["result"]["capabilities"]["warmAccountRulesMemory"] is True
    ok = next(m for m in sent if m.get("id") == 2)
    assert ok["result"]["ok"] is True
    assert ok["result"]["topicCount"] == 1
    hit = get_account_rules_memory_snapshot("user-1", "F1")
    assert hit is not None
    assert "warm" in str(hit.rules_payload)


def test_warm_account_rules_memory_reply_carries_renewal_ttl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cache_clock: _Clock
) -> None:
    """Reply's ``ttlSeconds`` = the seeded entry's real deadline (desktop renews on it)."""
    clear_account_rules_memory_cache()
    (tmp_path / "x.py").write_text("x = 1\n", encoding="utf-8")
    sent, write_line = _recorder()
    server = SidecarServer(write_line)

    async def _warm(creds, *, user_id, folder_id):
        snap = AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- warm"}],
            },
        )
        seed_account_rules_memory_cache(user_id, folder_id, snap)
        return snap

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.warm_account_rules_memory",
        _warm,
    )

    async def run() -> None:
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "userId": "user-1",
                        "workspaceRoot": str(tmp_path),
                        "approvalsEnabled": True,
                    },
                }
            )
        )
        await server.handle_line(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "warmAccountRulesMemory",
                    "params": {
                        "folderId": "F1",
                        "accountAuth": {
                            "baseUrl": "https://example.test/v1/account",
                            "apiKey": "k",
                        },
                    },
                }
            )
        )
        await _flush_pending(server)

    asyncio.run(run())
    ttl = next(m for m in sent if m.get("id") == 2)["result"]["ttlSeconds"]
    assert ttl == pytest.approx(300.0)

    # Honour the number and the snapshot is still there; outlive it and it is gone.
    cache_clock.advance(ttl - 1.0)
    assert get_account_rules_memory_snapshot("user-1", "F1") is not None
    cache_clock.advance(2.0)
    assert get_account_rules_memory_snapshot("user-1", "F1") is None


async def test_warm_includes_scope_state_alongside_memory_bodies(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """Scope state is warmed via ``/scope-state/get``, not as a document-tree meta file."""
    clear_account_rules_memory_cache()
    state_scopes: list[str | None] = []

    async def _rules(*_a, **_k):
        return {
            "global_rules": [],
            "project_rules": [],
            "global_on_demand_rules": [],
            "project_on_demand_rules": [],
        }

    async def _mem_list(creds, *, scope):
        if scope is None:
            return [{"path": "偏好.md", "version": "1"}]
        return [{"path": "画像.md", "version": "1"}, {"path": "导航.md", "version": "1"}]

    async def _mem_load(creds, *, path, scope):
        return f"# {path}\n"

    async def _scope_state(creds, *, scope):
        state_scopes.append(scope)
        return {
            "last_semantic_at": None,
            "explore_workspace_key": "ws:1",
            "explore_fingerprint": "fp1",
            "explore_fingerprint_dirty": False,
        }

    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_list_user_rules", _rules
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_list", _mem_list
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_load", _mem_load
    )
    monkeypatch.setattr(
        "agentcore.memory.account_prepare_cache.cloud_memory_scope_state_get",
        _scope_state,
    )

    snap = await warm_account_rules_memory(
        account_creds, user_id="u1", folder_id="F1"
    )
    assert None in state_scopes and "F1" in state_scopes
    assert snap.scope_states["F1"].explore_workspace_key == "ws:1"
    assert snap.memory_bodies[("F1", "画像.md")].startswith("#")
    assert all(path != "_memory_meta.json" for (_, path) in snap.memory_bodies)


async def test_warm_pulls_the_ancestor_folders_the_cloud_resolved(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """A sidecar has no folders table: the chain (and its layers) can only come from云."""
    clear_account_rules_memory_cache()
    listed_scopes: list[str | None] = []

    async def _rules(creds, *, folder_id):
        return {
            "global_rules": [],
            "project_rules": [],
            "ancestor_rules": [{"name": "用户规则.md", "content": "- 外层规则"}],
            "global_on_demand_rules": [],
            "project_on_demand_rules": [],
            "folder_chain": ["F_outer", folder_id],
        }

    async def _mem_list(creds, *, scope):
        listed_scopes.append(scope)
        return [{"path": "画像.md", "version": "1"}]

    async def _mem_load(creds, *, path, scope):
        return f"- {scope or 'global'} 画像\n"

    async def _scope_state(creds, *, scope):
        del scope
        return {"last_semantic_at": None}

    for name, fn in (
        ("cloud_list_user_rules", _rules),
        ("cloud_memory_list", _mem_list),
        ("cloud_memory_load", _mem_load),
        ("cloud_memory_scope_state_get", _scope_state),
    ):
        monkeypatch.setattr(f"agentcore.memory.account_prepare_cache.{name}", fn)

    snap = await warm_account_rules_memory(
        account_creds, user_id="u1", folder_id="F1"
    )
    assert snap.folder_chain == ("F_outer", "F1")
    assert "F_outer" in listed_scopes
    assert snap.memory_bodies[("F_outer", "画像.md")] == "- F_outer 画像\n"
    assert snap.scope_states["F_outer"].last_semantic_at is None
    assert not snap.degraded

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
    assert "外层规则" in rules_md
    assert rules_md.index("F_outer 画像") < rules_md.index("F1 画像")


async def test_warm_empty_folder_chain_drops_the_dead_desk(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """First-phase folder fetch races /rules/list; empty chain must not keep that desk."""
    clear_account_rules_memory_cache()

    async def _rules(creds, *, folder_id):
        del creds, folder_id
        return {
            "global_rules": [{"name": "用户规则.md", "content": "- 全局规则"}],
            "project_rules": [{"name": "用户规则.md", "content": "- 当前规则"}],
            "folder_chain": [],
        }

    async def _mem_list(creds, *, scope):
        del creds
        return [{"path": "画像.md", "version": "1"}]

    async def _mem_load(creds, *, path, scope):
        del creds, path
        return f"- {scope or 'global'} 画像\n"

    async def _scope_state(creds, *, scope):
        del creds, scope
        return {"last_semantic_at": None}

    for name, fn in (
        ("cloud_list_user_rules", _rules),
        ("cloud_memory_list", _mem_list),
        ("cloud_memory_load", _mem_load),
        ("cloud_memory_scope_state_get", _scope_state),
    ):
        monkeypatch.setattr(f"agentcore.memory.account_prepare_cache.{name}", fn)

    snap = await warm_account_rules_memory(
        account_creds, user_id="u1", folder_id="F1"
    )
    assert snap.folder_chain == ()
    assert ("F1", "画像.md") not in snap.memory_bodies
    assert ("", "画像.md") in snap.memory_bodies
    assert "F1" not in snap.scope_states

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
        topics = await load_memory_topics(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id="F1",
            enabled=True,
        )
    assert "全局规则" in rules_md
    assert "当前规则" not in rules_md
    assert "F1 画像" not in rules_md
    assert topics == []


async def test_hibernate_drops_only_the_named_folder_snapshots():
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache("u1", "F1", _injectable_snapshot())
    seed_account_rules_memory_cache("u1", "F2", _injectable_snapshot())
    seed_account_rules_memory_cache("u1", None, _injectable_snapshot())
    await hibernate_folder_injection_cache("u1", ["F1"])
    assert get_account_rules_memory_snapshot("u1", "F1") is None
    assert get_account_rules_memory_snapshot("u1", "F2") is not None
    assert get_account_rules_memory_snapshot("u1", None) is not None


async def test_document_store_cache_only_miss_skips_cloud(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    clear_account_rules_memory_cache()
    calls: list[str] = []

    async def _boom(*_a, **_k):
        calls.append("cloud")
        raise AssertionError("cloud must not be called under prepare_reads_cache_only")

    monkeypatch.setattr("agentcore.account.credentials.cloud_memory_load", _boom)
    monkeypatch.setattr("agentcore.account.credentials.cloud_memory_list", _boom)
    monkeypatch.setattr("agentcore.account.credentials.cloud_memory_save", _boom)

    store = DocumentMemoryStore()
    token = prepare_reads_cache_only.set(True)
    folder_token = prepare_account_folder_id.set("F1")
    try:
        with account_credentials_scope(account_creds):
            assert await store.load("u1", "画像.md", scope="F1") == ""
            assert await store.list("u1", scope="F1") == []
            await store.save("u1", "_memory_meta.json", "{}\n", scope="F1")
    finally:
        prepare_reads_cache_only.reset(token)
        prepare_account_folder_id.reset(folder_token)
    assert calls == []


async def test_document_store_cache_only_seed_serves_explore_profile(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.episode_store import ScopeMemoryMeta
    from agentcore.memory.explore_profile import (
        folder_profile_explore_reason,
        load_folder_profile,
    )
    from agentcore.memory.store import CORE_MEMORY_FILE

    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        "F1",
        AccountPrepareSnapshot(
            memory_bodies={
                ("F1", CORE_MEMORY_FILE): "## 技术栈与工具\n- Go\n",
            },
            scope_states={
                "F1": ScopeMemoryMeta(
                    last_semantic_at=None,
                    explore_workspace_key="ws:abc",
                    explore_fingerprint=None,
                    explore_fingerprint_dirty=False,
                ),
            },
        ),
    )

    async def _boom(*_a, **_k):
        raise AssertionError("must not call cloud on cache hit")

    monkeypatch.setattr("agentcore.account.credentials.cloud_memory_load", _boom)
    monkeypatch.setattr("agentcore.account.credentials.cloud_memory_save", _boom)
    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_memory_scope_state_get", _boom
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_memory_scope_state_save", _boom
    )

    store = DocumentMemoryStore()
    token = prepare_reads_cache_only.set(True)
    folder_token = prepare_account_folder_id.set("F1")
    try:
        with account_credentials_scope(account_creds):
            profile = await load_folder_profile(store, "u1", "F1")
            assert "Go" in profile
            reason = await folder_profile_explore_reason(
                store, "u1", "F1", current_workspace_key="ws:abc"
            )
            assert reason is None  # non-empty + matching key → no explore
    finally:
        prepare_reads_cache_only.reset(token)
        prepare_account_folder_id.reset(folder_token)
