"""Account narrow-ticket rules/memory cloud path (定案 R3b)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agentcore.account.credentials import (
    AccountCloudError,
    AccountCredentials,
    account_credentials_scope,
    cloud_list_user_rules,
    cloud_write_user_rule,
)
from agentcore.memory.document_store import DocumentMemoryStore
from agentcore.memory.rules_injection import (
    assemble_turn_rules,
    load_on_demand_user_rules,
)
from agentcore.tools.builtin.file_ops import FileDeleteTool, FileWriteTool
from agentcore.tools.protocol import ToolContext

pytestmark = pytest.mark.anyio


class _FakeTransport(httpx.AsyncBaseTransport):
    def __init__(self, handler) -> None:
        self._handler = handler

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._handler(request)


@pytest.fixture
def account_creds() -> AccountCredentials:
    return AccountCredentials(
        api_key="account-jwt",
        base_url="https://cloud.example/v1/account",
    )


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="local"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="host-1",
    )


class _EmptyMemoryStore:
    """Minimal MemoryStore stub (no AI memory) for assemble_turn_rules tests."""

    async def list(self, user_id: str, scope: str | None = None) -> list[Any]:
        return []

    async def load(self, user_id: str, path: str, scope: str | None = None) -> str:
        return ""

    async def save(
        self, user_id: str, path: str, markdown: str, scope: str | None = None
    ) -> None:
        raise AssertionError("empty store must not save")

    async def delete(self, user_id: str, path: str, scope: str | None = None) -> None:
        raise AssertionError("empty store must not delete")

    async def project_scopes(self, user_id: str) -> list[str]:
        return []


# --- cloud HTTP client --------------------------------------------------------


async def test_cloud_list_user_rules_ok(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url).endswith("/rules/list")
        assert request.headers["Authorization"] == "Bearer account-jwt"
        return httpx.Response(
            200,
            json={
                "global_rules": [{"name": "用户规则.md", "content": "- 用中文"}],
                "project_rules": [],
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    data = await cloud_list_user_rules(account_creds, folder_id=None)
    assert data["global_rules"][0]["content"] == "- 用中文"


async def test_cloud_write_user_rule_ok(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/rules/write")
        import json

        payload = json.loads(request.content.decode())
        assert payload["content"] == "以后都用中文"
        assert payload["name"] == "回复语言.md"
        assert payload["folder_id"] is None
        assert "action" not in payload
        return httpx.Response(
            200,
            json={
                "changed": True,
                "action": "write",
                "message": "已写入规则「回复语言.md」（常驻）。",
                "name": "回复语言.md",
                "apply": "always",
                "ok": True,
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    result = await cloud_write_user_rule(
        account_creds,
        name="回复语言.md",
        content="以后都用中文",
        folder_id=None,
    )
    assert result["changed"] is True
    assert result["action"] == "write"
    assert "已写入" in result["message"]
    assert result["name"] == "回复语言.md"


async def test_cloud_write_user_rule_quota_exceeded(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            409,
            json={
                "detail": {
                    "code": "ALWAYS_QUOTA_EXCEEDED",
                    "message": "常驻条目配额已满",
                }
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    with pytest.raises(AccountCloudError) as ei:
        await cloud_write_user_rule(
            account_creds,
            name="回复语言.md",
            content="以后都用中文",
            folder_id=None,
        )
    assert ei.value.code == "ALWAYS_QUOTA_EXCEEDED"
    assert "配额" in ei.value.message


async def test_cloud_write_user_rule_payload(monkeypatch: pytest.MonkeyPatch, account_creds):
    async def _handler(request: httpx.Request) -> httpx.Response:
        import json

        payload = json.loads(request.content.decode())
        assert payload["name"] == "回复语言.md"
        assert payload["content"] == "用中文"
        assert payload["apply"] == "always"
        return httpx.Response(
            200,
            json={
                "changed": True,
                "action": "write",
                "message": "已写入规则「回复语言.md」（常驻）。",
                "name": "回复语言.md",
                "apply": "always",
            },
        )

    monkeypatch.setattr(
        "agentcore.account.credentials.outbound_async_client",
        lambda **kwargs: httpx.AsyncClient(transport=_FakeTransport(_handler), **kwargs),
    )
    result = await cloud_write_user_rule(
        account_creds,
        name="回复语言.md",
        content="用中文",
        folder_id=None,
        apply="always",
    )
    assert result["changed"] is True
    assert result["action"] == "write"





# --- assemble / file overlay / store with ContextVar ------------------------------


async def test_assemble_turn_rules_ticketed_miss_skips_cloud(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """Ticketed prepare is cache_only: miss → empty, never await /rules/list."""
    from agentcore.memory.account_prepare_cache import clear_account_rules_memory_cache

    clear_account_rules_memory_cache()
    called = {"n": 0}

    async def _fake_list(*_a, **_k):
        called["n"] += 1
        return {"global_rules": [{"name": "用户规则.md", "content": "- 永远用中文"}]}

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _fake_list
    )

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id=None,
        )
    assert rules_md == ""
    assert called["n"] == 0


async def test_assemble_turn_rules_ticketed_hit_after_seed(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.account_prepare_cache import (
        AccountPrepareSnapshot,
        clear_account_rules_memory_cache,
        seed_account_rules_memory_cache,
    )

    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        None,
        AccountPrepareSnapshot(
            rules_payload={
                "global_on_demand_rules": [
                    {
                        "name": "用户规则.md",
                        "content": "- 永远用中文",
                        "description": "回复语言",
                    }
                ],
                "project_rules": [],
            },
            memory_bodies={("", "偏好.md"): "- 偏好偏好\n"},
        ),
    )

    async def _boom(*_a, **_k):
        raise AssertionError("must not call cloud on cache hit")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _boom
    )

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id=None,
        )
        on_demand = await load_on_demand_user_rules("u1", folder_id=None)
    assert rules_md == ""
    assert any(item.name == "用户规则" for item in on_demand)
    assert "偏好偏好" not in rules_md


async def test_assemble_turn_rules_cloud_failure_soft_empty(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """Miss (no seed) with ticket → empty; cloud not consulted on prepare."""
    from agentcore.memory.account_prepare_cache import clear_account_rules_memory_cache

    clear_account_rules_memory_cache()

    async def _boom(*_a, **_k):
        raise AccountCloudError("down", code="account_cloud_unreachable")

    monkeypatch.setattr("agentcore.account.credentials.cloud_list_user_rules", _boom)

    with account_credentials_scope(account_creds):
        rules_md = await assemble_turn_rules(
            _EmptyMemoryStore(),  # type: ignore[arg-type]
            "u1",
            folder_id=None,
        )
    assert rules_md == ""


async def test_file_write_rule_cloud_success(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _fake_write(creds, **kwargs):
        assert kwargs.get("content") == "以后都用中文"
        assert kwargs.get("name") == "回复语言.md"
        assert kwargs.get("folder_id") is None
        assert creds is account_creds
        return {
            "changed": True,
            "action": "write",
            "message": "已写入规则「回复语言.md」（常驻）。",
            "name": "回复语言.md",
            "apply": "always",
            "ok": True,
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_write_user_rule", _fake_write
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("must not open local DB")),
    )
    warmed = {"n": 0}

    async def _fake_warm(_creds, *, user_id, folder_id):
        warmed["n"] += 1
        assert user_id == "u1"
        assert folder_id is None

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules._rewarm_account_rules_memory",
        _fake_warm,
    )

    with account_credentials_scope(account_creds):
        result = await FileWriteTool().execute(
            {"file_path": ".agentcore/rules/回复语言.md", "content": "以后都用中文"},
            _ctx(),
        )
    assert result.success is True
    assert "已写入" in (result.output or "")
    assert warmed["n"] == 1


async def test_file_delete_rule_cloud(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _fake_delete(creds, **kwargs):
        del creds
        assert kwargs.get("name") == "回复语言.md"
        return {
            "changed": True,
            "action": "delete",
            "message": "已删除规则「回复语言.md」。",
            "name": "回复语言.md",
            "ok": True,
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_delete_user_rule", _fake_delete
    )

    async def _noop_rewarm(*_a, **_k):
        return None

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules._rewarm_account_rules_memory",
        _noop_rewarm,
    )
    with account_credentials_scope(account_creds):
        result = await FileDeleteTool().execute(
            {"path": ".agentcore/rules/回复语言.md"}, _ctx()
        )
    assert result.success is True
    assert "已删除" in (result.output or "")


async def test_file_write_rule_cloud_failure_explicit(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    async def _boom(*_a, **_k):
        raise AccountCloudError("unreachable", code="account_cloud_unreachable")

    monkeypatch.setattr("agentcore.account.credentials.cloud_write_user_rule", _boom)

    with account_credentials_scope(account_creds):
        result = await FileWriteTool().execute(
            {"file_path": ".agentcore/rules/回复语言.md", "content": "完整一篇规则正文"},
            _ctx(),
        )
    assert result.success is False
    assert "请稍后再试" in (result.error or result.output or "")




async def test_document_store_bound_session_skips_cloud(
    account_creds,
):
    """Request DI path (session bound) must stay on DB even if ContextVar is set."""
    class _FakeRepo:
        async def get_memory_note(self, *_a, **_k):
            return SimpleNamespace(content="from-db")

    store = DocumentMemoryStore(session=SimpleNamespace())  # type: ignore[arg-type]

    @asynccontextmanager
    async def _repo():
        yield _FakeRepo()

    store._repo = _repo  # type: ignore[method-assign]
    with account_credentials_scope(account_creds):
        body = await store.load("u1", "画像.md")
    assert body == "from-db"


# --- on_demand rules via account narrow ticket (禁静默空转) --------------------


def test_on_demand_user_rules_from_cloud_maps_catalog():
    from agentcore.memory.rules_injection import on_demand_user_rules_from_cloud

    rules = on_demand_user_rules_from_cloud(
        {
            "global_rules": [{"name": "用户规则.md", "content": "- always"}],
            "global_on_demand_rules": [
                {
                    "name": "合规附录.md",
                    "content": "- 对外须用中文\n",
                    "description": "对外发布前查的合规口径",
                },
            ],
            "project_on_demand_rules": [
                {"name": "出差报销.md", "content": "- 先走审批\n"},
            ],
        },
        folder_id="F1",
    )
    assert [r.name for r in rules] == ["合规附录"]
    assert rules[0].summary == "对外发布前查的合规口径"


def test_on_demand_from_cloud_empty_when_keys_absent():
    """Older clouds without on_demand fields must not invent catalog entries."""
    from agentcore.memory.rules_injection import on_demand_user_rules_from_cloud

    assert on_demand_user_rules_from_cloud(
        {"global_rules": [{"name": "用户规则.md", "content": "- x"}]},
        folder_id=None,
    ) == []


def test_lookup_on_demand_body_project_then_global():
    from agentcore.memory.rules_injection import lookup_on_demand_rule_body_from_cloud

    payload = {
        "global_on_demand_rules": [
            {
                "name": "合规附录.md",
                "content": "- global body\n",
                "description": "查阅合规",
            },
        ],
        "project_on_demand_rules": [
            {
                "name": "合规附录.md",
                "content": "- project body\n",
                "description": "查阅合规",
            },
        ],
    }
    assert (
        lookup_on_demand_rule_body_from_cloud(
            payload, folder_id="F1", name="合规附录"
        )
        == "- project body\n"
    )
    assert (
        lookup_on_demand_rule_body_from_cloud(
            payload, folder_id=None, name="合规附录"
        )
        == "- global body\n"
    )


async def test_load_on_demand_uses_snapshot_when_ticketed(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    """Regression: account path must NOT silently return [] when on_demand was seeded."""
    from agentcore.memory.account_prepare_cache import (
        AccountPrepareSnapshot,
        clear_account_rules_memory_cache,
        seed_account_rules_memory_cache,
    )
    from agentcore.memory.rules_injection import load_on_demand_user_rules

    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        "F1",
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- always"}],
                "project_rules": [],
                "global_on_demand_rules": [
                    {
                        "name": "合规附录.md",
                        "content": "- 对外须用中文\n",
                        "description": "查阅合规",
                    },
                ],
                "project_on_demand_rules": [],
            }
        ),
    )

    async def _boom(*_a, **_k):
        raise AssertionError("must not call cloud on cache hit")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _boom
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("must not open local DB")),
    )

    with account_credentials_scope(account_creds):
        rules = await load_on_demand_user_rules("u1", folder_id="F1")
    assert len(rules) == 1
    assert rules[0].name == "合规附录"


async def test_load_on_demand_ticketed_miss_is_empty(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.account_prepare_cache import clear_account_rules_memory_cache
    from agentcore.memory.rules_injection import load_on_demand_user_rules

    clear_account_rules_memory_cache()
    called = {"n": 0}

    async def _fake_list(*_a, **_k):
        called["n"] += 1
        return {
            "global_on_demand_rules": [
                {"name": "合规附录.md", "content": "- x\n"},
            ],
        }

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _fake_list
    )
    with account_credentials_scope(account_creds):
        assert await load_on_demand_user_rules("u1", folder_id=None) == []
    assert called["n"] == 0


async def test_load_on_demand_ticketed_empty_catalog_is_honest(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.account_prepare_cache import (
        AccountPrepareSnapshot,
        clear_account_rules_memory_cache,
        seed_account_rules_memory_cache,
    )
    from agentcore.memory.rules_injection import load_on_demand_user_rules

    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        None,
        AccountPrepareSnapshot(
            rules_payload={
                "global_rules": [{"name": "用户规则.md", "content": "- always"}],
                "global_on_demand_rules": [],
                "project_on_demand_rules": [],
            }
        ),
    )
    with account_credentials_scope(account_creds):
        assert await load_on_demand_user_rules("u1", folder_id=None) == []


async def test_consult_ticketed_hit_uses_snapshot(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.account_prepare_cache import (
        AccountPrepareSnapshot,
        clear_account_rules_memory_cache,
        seed_account_rules_memory_cache,
    )
    from agentcore.runtime.context.consult_sources import (
        MergedConsultSource,
        RuleConsultSource,
    )
    from agentcore.tools.builtin.consult import ConsultTool

    body = "- 对外沟通须用中文\n"
    clear_account_rules_memory_cache()
    seed_account_rules_memory_cache(
        "u1",
        "F1",
        AccountPrepareSnapshot(
            rules_payload={
                "global_on_demand_rules": [
                    {"name": "合规附录.md", "content": body, "description": "查阅合规"}
                ],
                "project_on_demand_rules": [],
            }
        ),
    )

    async def _boom(*_a, **_k):
        raise AssertionError("consult fetch must not live-list /rules")

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _boom
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("must not open local DB")),
    )

    tool = ConsultTool(source=MergedConsultSource(rule=RuleConsultSource(folder_id="F1")))
    with account_credentials_scope(account_creds):
        result = await tool.execute({"name": "合规附录"}, _ctx())
    assert result.success
    assert result.output == body
    assert result.display.get("name") == "合规附录"
    assert result.display.get("origin") == "user"
    # 细 kind 只进日志；display 只带两桶 origin。
    assert "kind" not in result.display


async def test_consult_ticketed_miss_does_not_http(
    monkeypatch: pytest.MonkeyPatch, account_creds
):
    from agentcore.memory.account_prepare_cache import clear_account_rules_memory_cache
    from agentcore.runtime.context.consult_sources import RuleConsultSource

    clear_account_rules_memory_cache()
    called = {"n": 0}

    async def _fake_list(*_a, **_k):
        called["n"] += 1
        return {"global_on_demand_rules": [{"name": "合规附录.md", "content": "- x\n"}]}

    monkeypatch.setattr(
        "agentcore.account.credentials.cloud_list_user_rules", _fake_list
    )
    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: (_ for _ in ()).throw(AssertionError("must not open local DB")),
    )
    src = RuleConsultSource(folder_id="F1")
    with account_credentials_scope(account_creds):
        assert await src.fetch_by_name("u", "合规附录") is None
    assert called["n"] == 0


async def test_assemble_without_ticket_uses_db_path(
    monkeypatch: pytest.MonkeyPatch,
):
    """No account ContextVar → assemble still opens a session (may soft-fail empty)."""
    opened = {"n": 0}

    class _SessCtx:
        async def __aenter__(self):
            opened["n"] += 1
            raise RuntimeError("no local pg")

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(
        "agentcore.db.base.async_session_factory",
        lambda: _SessCtx(),
    )
    rules_md = await assemble_turn_rules(
        _EmptyMemoryStore(),  # type: ignore[arg-type]
        "u1",
        folder_id=None,
    )
    assert opened["n"] == 1
    assert rules_md == ""
