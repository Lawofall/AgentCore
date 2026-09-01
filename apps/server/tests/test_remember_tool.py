"""CEO remember tool — records an explicit user directive as a USER RULE (§5.7 分流).

DB-free here: the schema contract + the pure mutate helpers. The end-to-end write (directive →
``role='rule', ai_maintained=false`` document, immediate injection) is exercised against a real
schema in ``tests/integration/test_documents.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentcore.memory.rules_injection import (
    UserRuleMutationResult,
    append_user_rule_bullet,
    mutate_user_rule_markdown,
)
from agentcore.tools.builtin.remember import (
    RememberTool,
    _is_incomplete_rule_content,
    build_remember_tool,
)
from agentcore.tools.protocol import ToolContext


def _ctx() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="local"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
    )


def test_remember_schema_is_static():
    tool = RememberTool(folder_id=None)
    assert tool.schema.name == "remember"
    # Steers the model to the split: explicit directive here, inferred preferences to巩固.
    assert "明确" in tool.schema.description
    assert tool.schema.parameters["required"] == []
    assert tool.schema.parameters["properties"]["scope"]["enum"] == ["global", "folder"]
    assert tool.schema.parameters["properties"]["action"]["enum"] == [
        "add",
        "replace",
        "forget",
        "list",
    ]
    assert "replaces" in tool.schema.parameters["properties"]


def test_build_remember_tool_defaults():
    tool = build_remember_tool(folder_id="fold-1")
    assert isinstance(tool, RememberTool)
    assert tool.folder_id == "fold-1"


def test_append_user_rule_bullet_adds_and_dedupes():
    md, changed = append_user_rule_bullet("", "以后都用中文回复")
    assert changed is True
    assert md == "- 以后都用中文回复\n"

    # A normalized duplicate (whitespace-only difference) is a no-op — re-remembering never grows.
    md2, changed2 = append_user_rule_bullet(md, "以后都用中文回复  ")
    assert changed2 is False
    assert md2 == md

    # A genuinely new rule appends as another bullet.
    md3, changed3 = append_user_rule_bullet(md, "别用表格")
    assert changed3 is True
    assert md3 == "- 以后都用中文回复\n- 别用表格\n"


def test_append_user_rule_bullet_ignores_blank():
    assert append_user_rule_bullet("- x\n", "   ") == ("- x\n", False)


def test_mutate_add_default_and_dedupe():
    added = mutate_user_rule_markdown("", action="add", content="用中文")
    assert added.changed is True
    assert "已追加" in added.message
    assert added.markdown == "- 用中文\n"

    # Missing action defaults to add.
    again = mutate_user_rule_markdown(added.markdown, content="用中文")
    assert again.action == "add"
    assert again.changed is False
    assert "已经记过了" in again.message


def test_mutate_replace_removes_old_then_writes():
    base = "- 用英文\n- 别用表格\n"
    result = mutate_user_rule_markdown(
        base, action="replace", content="用中文", replaces="用英文"
    )
    assert result.changed is True
    assert "已替换" in result.message
    assert "用英文" in result.message
    assert result.markdown == "- 别用表格\n- 用中文\n"
    assert result.removed == ("用英文",)


def test_mutate_replace_missing_old_appends_honestly():
    base = "- 别用表格\n"
    result = mutate_user_rule_markdown(
        base, action="replace", content="用中文", replaces="用英文"
    )
    assert result.changed is True
    assert "未找到旧条" in result.message
    assert "已追加" in result.message
    assert "已替换" not in result.message
    assert result.markdown == "- 别用表格\n- 用中文\n"


def test_mutate_replace_missing_old_and_new_exists():
    base = "- 用中文\n"
    result = mutate_user_rule_markdown(
        base, action="replace", content="用中文", replaces="用英文"
    )
    assert result.changed is False
    assert "未找到旧条" in result.message
    assert "已存在" in result.message


def test_mutate_forget_deletes_all_same_key():
    # Same normalized key via leading/trailing whitespace on the bullet text.
    base = "- 用中文\n- 别用表格\n-   用中文   \n"
    result = mutate_user_rule_markdown(base, action="forget", content="用中文")
    assert result.changed is True
    assert "已删除" in result.message
    assert result.markdown == "- 别用表格\n"
    assert len(result.removed) == 2


def test_mutate_forget_casefold_latin():
    base = "- Prefer English replies\n- 别用表格\n- prefer   english replies\n"
    result = mutate_user_rule_markdown(
        base, action="forget", content="PREFER ENGLISH REPLIES"
    )
    assert result.changed is True
    assert result.markdown == "- 别用表格\n"
    assert len(result.removed) == 2


def test_mutate_forget_not_found():
    result = mutate_user_rule_markdown("- x\n", action="forget", content="不存在的规则")
    assert result.changed is False
    assert "未找到" in result.message
    assert result.markdown == "- x\n"


def test_mutate_list_returns_body_without_claiming_write():
    empty = mutate_user_rule_markdown("", action="list")
    assert empty.changed is False
    assert "暂无" in empty.message
    assert empty.rules_markdown == ""

    listed = mutate_user_rule_markdown("- 用中文\n", action="list")
    assert listed.changed is False
    assert "用中文" in listed.message
    assert listed.rules_markdown == "- 用中文\n"


# --- content integrity gate (add/replace) -------------------------------------


def test_incomplete_rule_content_trailing_ellipsis():
    assert _is_incomplete_rule_content("以后都用中文...")
    assert _is_incomplete_rule_content("以后都用中文…")
    assert _is_incomplete_rule_content("以后都用中文……")
    assert not _is_incomplete_rule_content("以后都用中文回复")
    assert not _is_incomplete_rule_content("")


def test_incomplete_rule_content_mid_omission_marker():
    # Reuses file_ops.has_omission_marker (remember / evals; not a file_write gate).
    assert _is_incomplete_rule_content("以后都用中文（略）回复")
    assert _is_incomplete_rule_content("see ... omitted details")
    assert _is_incomplete_rule_content("中间省略，已保留首尾")


@pytest.mark.anyio
async def test_remember_rejects_trailing_ellipsis():
    tool = RememberTool(folder_id=None)
    for suffix in ("...", "…", "……"):
        result = await tool.execute({"content": f"用中文{suffix}"}, _ctx())
        assert result.success is False
        assert "完整一句" in (result.output or "")
        assert "省略号" in (result.output or "")


@pytest.mark.anyio
async def test_remember_rejects_mid_omission_marker():
    tool = RememberTool(folder_id=None)
    result = await tool.execute({"content": "用中文（略）别用表格"}, _ctx())
    assert result.success is False
    assert "完整一句" in (result.output or "")


@pytest.mark.anyio
async def test_remember_rejects_replace_incomplete_content():
    tool = RememberTool(folder_id=None)
    result = await tool.execute(
        {"action": "replace", "content": "用中文...", "replaces": "用英文"},
        _ctx(),
    )
    assert result.success is False
    assert "完整一句" in (result.output or "")


@pytest.mark.anyio
async def test_remember_empty_content_unchanged():
    tool = RememberTool(folder_id=None)
    result = await tool.execute({"content": "   "}, _ctx())
    assert result.success is False
    assert result.error == "缺少 content。"


@pytest.mark.anyio
async def test_remember_complete_content_still_writes(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def _fake_mutate(_repo, _uid, *, folder_id, action, content, replaces):
        captured["content"] = content
        captured["action"] = action
        return UserRuleMutationResult(
            action=action,
            changed=True,
            message="已追加规则：以后都用中文回复",
            content=content,
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.async_session_factory",
        lambda: _FakeSession(),
    )

    tool = RememberTool(folder_id=None)
    result = await tool.execute({"content": "以后都用中文回复"}, _ctx())
    assert result.success is True
    assert captured["content"] == "以后都用中文回复"
    assert "已追加" in (result.output or "")


@pytest.mark.anyio
async def test_remember_forget_trailing_ellipsis_not_gated(monkeypatch: pytest.MonkeyPatch):
    """forget has no new-body semantics — trailing ellipsis must not block delete."""
    called = {"ok": False}

    async def _fake_mutate(_repo, _uid, *, folder_id, action, content, replaces):
        called["ok"] = True
        assert action == "forget"
        assert content == "用中文..."
        return UserRuleMutationResult(
            action="forget",
            changed=True,
            message="已删除规则：用中文...",
            content=content,
            removed=("用中文...",),
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.async_session_factory",
        lambda: _FakeSession(),
    )

    tool = RememberTool(folder_id=None)
    result = await tool.execute(
        {"action": "forget", "content": "用中文..."}, _ctx()
    )
    assert result.success is True
    assert called["ok"] is True


@pytest.mark.anyio
async def test_remember_list_unaffected_by_ellipsis_gate(monkeypatch: pytest.MonkeyPatch):
    async def _fake_mutate(_repo, _uid, *, folder_id, action, content, replaces):
        assert action == "list"
        return UserRuleMutationResult(
            action="list",
            changed=False,
            message="当前用户规则：\n- 用中文\n",
            markdown="- 用中文\n",
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.async_session_factory",
        lambda: _FakeSession(),
    )

    tool = RememberTool(folder_id=None)
    result = await tool.execute({"action": "list"}, _ctx())
    assert result.success is True
    assert "用中文" in (result.output or "")


class _QuotaDoc:
    id = "d1"
    content = "- 已有规则\n"
    apply_mode = "always"
    role = "rule"


class _QuotaRepo:
    def __init__(self) -> None:
        self.upserted = False

    async def get_user_rules_doc(self, user_id, folder_id):  # noqa: ARG002
        return _QuotaDoc()

    async def upsert_user_rules_doc(self, user_id, folder_id, content):  # noqa: ARG002
        self.upserted = True


@pytest.mark.anyio
async def test_mutate_user_rule_ai_growth_denied(monkeypatch: pytest.MonkeyPatch):
    from agentcore.memory.always_quota import (
        AlwaysQuotaDecision,
        AlwaysQuotaExceededError,
        AlwaysUsage,
    )
    from agentcore.memory.rules_injection import mutate_user_rule

    async def _deny(*args, **kwargs):  # noqa: ARG001
        return AlwaysQuotaDecision(
            allowed=False,
            usage=AlwaysUsage(used_chars=100, max_chars=50),
            message="常驻条目配额已满",
        )

    async def _no_notify(*args, **kwargs):  # noqa: ARG001
        return None

    monkeypatch.setattr("agentcore.memory.always_quota.check_always_write", _deny)
    monkeypatch.setattr(
        "agentcore.memory.always_quota.notify_always_quota_exceeded", _no_notify
    )
    repo = _QuotaRepo()
    with pytest.raises(AlwaysQuotaExceededError) as ei:
        await mutate_user_rule(
            repo,  # type: ignore[arg-type]
            "u1",
            folder_id=None,
            action="add",
            content="以后都用中文回复",
        )
    assert "配额" in ei.value.message
    assert repo.upserted is False


@pytest.mark.anyio
async def test_remember_quota_denied_message(monkeypatch: pytest.MonkeyPatch):
    from agentcore.memory.always_quota import AlwaysQuotaExceededError, AlwaysUsage

    async def _boom(*args, **kwargs):  # noqa: ARG001
        raise AlwaysQuotaExceededError(
            AlwaysUsage(used_chars=100, max_chars=50),
            "常驻条目配额已满",
            file="用户规则.md",
        )

    monkeypatch.setattr("agentcore.tools.builtin.remember.mutate_user_rule", _boom)
    monkeypatch.setattr(
        "agentcore.tools.builtin.remember.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )
    tool = RememberTool(folder_id=None)
    result = await tool.execute({"content": "以后都用中文回复"}, _ctx())
    assert result.success is False
    assert "配额" in (result.output or "")
    assert "请稍后再试" not in (result.output or "")


class _FakeSession:
    """Minimal async context manager standing in for async_session_factory()."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None
