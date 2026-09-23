"""User-rule overlay — .agentcore/rules/*.md via file_* onto documents.

DB-free here: path classify + mutate helpers + tool overlay (mutates mocked).
End-to-end write is in ``tests/integration/test_documents.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentcore.memory.rule_files import (
    RULES_CATALOG_ROOT,
    RULES_DIR_REL,
    classify_rule_path,
)
from agentcore.memory.rules_injection import (
    UserRuleMutationResult,
    mutate_user_rule,
    normalize_rule_filename,
)
from agentcore.tools.builtin.file_ops import (
    FileDeleteTool,
    FileListTool,
    FileReadTool,
    FileWriteTool,
    StrReplaceTool,
)
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace


def _tool_err(result) -> str:
    return result.error or result.output or ""


def _ctx(*, agent_role: str = "", write_coordinator: object | None = None) -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=SimpleNamespace(location="local"),  # type: ignore[arg-type]
        user_id="u1",
        conversation_id="c1",
        agent_role=agent_role,
        write_coordinator=write_coordinator,  # type: ignore[arg-type]
    )


def test_classify_rule_path():
    assert classify_rule_path(".agentcore") == ("agentcore_root", None)
    assert classify_rule_path(".agentcore/rules") == ("rules_dir", None)
    assert classify_rule_path(".agentcore/rules/回复语言.md") == (
        "rule_file",
        "回复语言.md",
    )
    assert classify_rule_path(".agentcore/rules/回复语言") == ("rule_file", "回复语言.md")
    assert classify_rule_path(".agentcore/rules/sub/x.md") == ("invalid", None)
    assert classify_rule_path("notes.md") == (None, None)
    assert classify_rule_path(".agentcore/rules/画像.md") == ("invalid", None)
    assert classify_rule_path("AgentCore") == (None, None)
    assert classify_rule_path("AgentCore/rules") == (None, None)
    assert classify_rule_path("AgentCore/rules/回复语言.md") == (None, None)
    assert classify_rule_path("规则/回复语言.md") == (None, None)
    assert classify_rule_path(".agentcore/规则") == ("invalid", None)
    assert classify_rule_path(".agentcore/规则/回复语言.md") == ("invalid", None)


def test_normalize_rule_filename():
    assert normalize_rule_filename("回复语言") == "回复语言.md"
    assert normalize_rule_filename("回复语言.md") == "回复语言.md"
    assert normalize_rule_filename("  回复语言.md  ") == "回复语言.md"
    assert normalize_rule_filename("") is None
    assert normalize_rule_filename("../x.md") is None
    assert normalize_rule_filename("dir/x.md") is None
    assert normalize_rule_filename("偏好.md") is None
    assert normalize_rule_filename("画像.md") is None
    assert normalize_rule_filename("导航.md") is None
    assert normalize_rule_filename("x" * 80) is None


class _FakeDoc:
    def __init__(
        self,
        name: str,
        content: str,
        apply_mode: str = "always",
        description: str = "",
    ) -> None:
        self.id = f"id-{name}"
        self.name = name
        self.content = content
        self.apply_mode = apply_mode
        self.description = description
        self.kind = "document"


class _FakeRepo:
    def __init__(self) -> None:
        self.docs: dict[str, _FakeDoc] = {}
        self.upserted = False

    async def list_user_rule_docs(self, user_id, folder_id):  # noqa: ARG002
        return list(self.docs.values())

    async def get_user_rule_doc(self, user_id, folder_id, name):  # noqa: ARG002
        return self.docs.get(name)

    async def upsert_user_rule_doc(
        self,
        user_id,
        folder_id,
        name,
        content,
        *,
        apply="always",
        description=None,
    ):  # noqa: ARG002
        from agentcore.documents.frontmatter import set_entry_frontmatter

        body = set_entry_frontmatter(content, apply=apply, description=description)
        doc = _FakeDoc(name, body, apply, description or "")
        self.docs[name] = doc
        self.upserted = True
        return doc

    async def delete_user_rule_doc(self, user_id, folder_id, name):  # noqa: ARG002
        return self.docs.pop(name, None) is not None


@pytest.mark.anyio
async def test_mutate_write_read_delete_list(monkeypatch: pytest.MonkeyPatch):
    from agentcore.memory.always_quota import AlwaysQuotaDecision, AlwaysUsage

    async def _allow(*args, **kwargs):  # noqa: ARG001
        return AlwaysQuotaDecision(
            allowed=True,
            usage=AlwaysUsage(used_chars=0, max_chars=1000),
            message="",
        )

    monkeypatch.setattr("agentcore.memory.always_quota.check_always_write", _allow)
    monkeypatch.setattr(
        "agentcore.memory.rules_injection.maybe_schedule_description_fill",
        lambda **kwargs: None,
    )
    repo = _FakeRepo()
    written = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="回复语言.md",
        content="用中文回复。",
        description="回复语言",
    )
    assert written.ok and written.changed
    assert written.name == "回复语言.md"
    assert written.apply == "on_demand"
    assert "按需" in written.message
    assert "用中文回复" in (repo.docs["回复语言.md"].content)

    again = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="回复语言.md",
        content="用中文回复。",
        description="回复语言",
    )
    assert again.ok and again.changed is False
    assert "没有变化" in again.message

    listed = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="list",
    )
    assert listed.ok and listed.changed is False
    assert listed.catalog[0][0] == "回复语言.md"
    assert "回复语言.md" in listed.message

    read = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="read",
        name="回复语言",
    )
    assert read.ok and "用中文回复" in read.body

    missing = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="delete",
        name="不存在.md",
    )
    assert missing.ok is False

    deleted = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="delete",
        name="回复语言.md",
    )
    assert deleted.ok and deleted.changed
    assert "已删除" in deleted.message
    assert "回复语言.md" not in repo.docs


@pytest.mark.anyio
async def test_mutate_rejects_missing_and_reserved_names():
    repo = _FakeRepo()
    missing = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        content="用中文",
    )
    assert missing.ok is False
    assert "缺少 name" in missing.message

    reserved = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="画像.md",
        content="不该写到记忆叶",
    )
    assert reserved.ok is False
    assert "name 不可用" in reserved.message
    assert repo.upserted is False


@pytest.mark.anyio
async def test_mutate_user_rule_ai_growth_denied(monkeypatch: pytest.MonkeyPatch):
    from agentcore.memory.always_quota import (
        AlwaysQuotaDecision,
        AlwaysQuotaExceededError,
        AlwaysUsage,
    )

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
    repo = _FakeRepo()
    with pytest.raises(AlwaysQuotaExceededError) as ei:
        await mutate_user_rule(
            repo,  # type: ignore[arg-type]
            "u1",
            folder_id=None,
            action="write",
            name="回复语言.md",
            content="以后都用中文回复",
            apply="always",
        )
    assert "配额" in ei.value.message
    assert ei.value.file == "回复语言.md"
    assert repo.upserted is False


@pytest.mark.anyio
async def test_mutate_omitted_apply_skips_always_quota(
    monkeypatch: pytest.MonkeyPatch,
):
    from agentcore.memory.always_quota import AlwaysQuotaDecision, AlwaysUsage

    async def _deny(*args, **kwargs):  # noqa: ARG001
        return AlwaysQuotaDecision(
            allowed=False,
            usage=AlwaysUsage(used_chars=100, max_chars=50),
            message="常驻条目配额已满",
        )

    monkeypatch.setattr("agentcore.memory.always_quota.check_always_write", _deny)
    monkeypatch.setattr(
        "agentcore.memory.rules_injection.maybe_schedule_description_fill",
        lambda **kwargs: None,
    )
    repo = _FakeRepo()
    written = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="回复语言.md",
        content="以后都用中文回复",
    )
    assert written.ok and written.apply == "on_demand"
    assert repo.docs["回复语言.md"].apply_mode == "on_demand"


@pytest.mark.anyio
async def test_mutate_rewrite_omitted_apply_keeps_existing_tier(
    monkeypatch: pytest.MonkeyPatch,
):
    from agentcore.memory.always_quota import AlwaysQuotaDecision, AlwaysUsage

    async def _allow(*args, **kwargs):  # noqa: ARG001
        return AlwaysQuotaDecision(
            allowed=True,
            usage=AlwaysUsage(used_chars=0, max_chars=1000),
            message="",
        )

    monkeypatch.setattr("agentcore.memory.always_quota.check_always_write", _allow)
    monkeypatch.setattr(
        "agentcore.memory.rules_injection.maybe_schedule_description_fill",
        lambda **kwargs: None,
    )
    repo = _FakeRepo()
    repo.docs["回复语言.md"] = _FakeDoc(
        "回复语言.md",
        "---\napply: always\n---\n旧正文",
        "always",
    )
    rewritten = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="回复语言.md",
        content="新正文",
    )
    assert rewritten.ok and rewritten.apply == "always"
    assert "常驻" in rewritten.message


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):  # noqa: ANN002
        return None


@pytest.mark.anyio
async def test_file_write_rule_empty_content():
    result = await FileWriteTool().execute(
        {"file_path": f"{RULES_DIR_REL}/回复语言.md", "content": "   "},
        _ctx(),
    )
    assert result.success is False
    assert "缺少 content" in _tool_err(result)


@pytest.mark.anyio
async def test_file_write_legacy_catalog_path_rejected(tmp_path):
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
    )
    result = await FileWriteTool().execute(
        {
            "file_path": ".agentcore/规则/回复语言.md",
            "content": "以后都用中文回复",
        },
        ctx,
    )
    assert result.success is False
    assert "rules" in _tool_err(result)
    assert not (tmp_path / ".agentcore").exists()
    assert not (tmp_path / "AgentCore" / "规则").exists()


@pytest.mark.anyio
async def test_file_write_rule_complete_content_still_writes(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def _fake_mutate(_repo, _uid, **kwargs):
        captured.update(kwargs)
        return UserRuleMutationResult(
            action="write",
            changed=True,
            message="已写入规则「回复语言.md」（常驻）。",
            name="回复语言.md",
            apply="always",
            content=str(kwargs.get("content") or ""),
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )

    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "以后都用中文回复",
        },
        _ctx(),
    )
    assert result.success is True
    assert captured["content"] == "以后都用中文回复"
    assert captured["name"] == "回复语言.md"
    assert captured["action"] == "write"
    assert "已写入" in (result.output or "")
    assert RULES_DIR_REL in (result.output or "")


@pytest.mark.anyio
async def test_file_write_rule_allows_trailing_ellipsis(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, object] = {}

    async def _fake_mutate(_repo, _uid, **kwargs):
        captured.update(kwargs)
        return UserRuleMutationResult(
            action="write",
            changed=True,
            message="已写入规则「回复语言.md」（常驻）。",
            name="回复语言.md",
            apply="always",
            content=str(kwargs.get("content") or ""),
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )

    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "遇到这种事就先等等……",
        },
        _ctx(),
    )
    assert result.success is True
    assert captured["content"] == "遇到这种事就先等等……"


@pytest.mark.anyio
async def test_file_write_rule_worker_project_scope(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    async def _fake_mutate(_repo, _uid, **kwargs):
        captured.update(kwargs)
        return UserRuleMutationResult(
            action="write",
            changed=True,
            message="已写入规则「回复语言.md」（常驻）。",
            name="回复语言.md",
            apply="always",
            content=str(kwargs.get("content") or ""),
        )

    _patch_local_rule_mutate(monkeypatch, _fake_mutate)
    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "以后都用中文回复",
        },
        _ctx(agent_role="研究员", write_coordinator=MagicMock()),
    )
    assert result.success is True
    assert captured["action"] == "write"
    assert captured["content"] == "以后都用中文回复"


@pytest.mark.anyio
async def test_file_write_rule_write_scope_none():
    ctx = _ctx(agent_role="研究员", write_coordinator=MagicMock())
    ctx.write_scope = "none"
    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "以后都用中文回复",
        },
        ctx,
    )
    assert result.success is False
    assert "write_scope=none" in _tool_err(result)


@pytest.mark.anyio
async def test_file_write_rule_quota_denied_message(monkeypatch: pytest.MonkeyPatch):
    from agentcore.memory.always_quota import AlwaysQuotaExceededError, AlwaysUsage

    async def _boom(*args, **kwargs):  # noqa: ARG001
        raise AlwaysQuotaExceededError(
            AlwaysUsage(used_chars=100, max_chars=50),
            "常驻条目配额已满",
            file="回复语言.md",
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.mutate_user_rule", _boom
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )
    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "以后都用中文回复",
        },
        _ctx(),
    )
    assert result.success is False
    assert "配额" in _tool_err(result)
    assert "请稍后再试" not in _tool_err(result)


@pytest.mark.anyio
async def test_file_write_rule_does_not_touch_workspace_disk(tmp_path, monkeypatch):
    async def _fake_mutate(_repo, _uid, **kwargs):
        return UserRuleMutationResult(
            action="write",
            changed=True,
            message="已写入规则「回复语言.md」（常驻）。",
            name="回复语言.md",
            apply="always",
            content=str(kwargs.get("content") or ""),
        )

    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.mutate_user_rule", _fake_mutate
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
    )
    result = await FileWriteTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "content": "以后都用中文回复",
        },
        ctx,
    )
    assert result.success is True
    assert not (tmp_path / ".agentcore" / "rules" / "回复语言.md").exists()
    assert not (tmp_path / "AgentCore" / "rules" / "回复语言.md").exists()
    assert not (tmp_path / ".agentcore" / "规则" / "回复语言.md").exists()
    assert not (tmp_path / "AgentCore" / "规则" / "回复语言.md").exists()


def _patch_local_rule_mutate(monkeypatch: pytest.MonkeyPatch, handler):
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.mutate_user_rule", handler
    )
    monkeypatch.setattr(
        "agentcore.tools.builtin.file_ops.user_rules.async_session_factory",
        lambda: _FakeSession(),
    )
    monkeypatch.setattr(
        "agentcore.account.credentials.get_account_credentials",
        lambda: None,
    )


@pytest.mark.anyio
async def test_file_read_rule_returns_body(monkeypatch: pytest.MonkeyPatch):
    async def _fake_mutate(_repo, _uid, **kwargs):
        assert kwargs["action"] == "read"
        assert kwargs["name"] == "回复语言.md"
        return UserRuleMutationResult(
            action="read",
            changed=False,
            message="",
            name="回复语言.md",
            apply="always",
            body="以后都用中文回复",
        )

    _patch_local_rule_mutate(monkeypatch, _fake_mutate)
    result = await FileReadTool().execute(
        {"file_path": f"{RULES_DIR_REL}/回复语言.md"},
        _ctx(),
    )
    assert result.success is True
    assert "以后都用中文回复" in (result.output or "")


@pytest.mark.anyio
async def test_file_list_rule_dir_lists_catalog(monkeypatch: pytest.MonkeyPatch):
    async def _fake_mutate(_repo, _uid, **kwargs):
        assert kwargs["action"] == "list"
        return UserRuleMutationResult(
            action="list",
            changed=False,
            message="",
            catalog=(("回复语言.md", "always", ""),),
        )

    _patch_local_rule_mutate(monkeypatch, _fake_mutate)
    result = await FileListTool().execute(
        {"directory": RULES_DIR_REL},
        _ctx(),
    )
    assert result.success is True
    assert "回复语言.md" in (result.output or "")


@pytest.mark.anyio
async def test_file_delete_rule_deletes(monkeypatch: pytest.MonkeyPatch):
    async def _fake_mutate(_repo, _uid, **kwargs):
        assert kwargs["action"] == "delete"
        assert kwargs["name"] == "回复语言.md"
        return UserRuleMutationResult(
            action="delete",
            changed=True,
            message="已删除规则「回复语言.md」。",
            name="回复语言.md",
        )

    _patch_local_rule_mutate(monkeypatch, _fake_mutate)
    result = await FileDeleteTool().execute(
        {"path": f"{RULES_DIR_REL}/回复语言.md"},
        _ctx(),
    )
    assert result.success is True
    assert "已删除" in (result.output or "")


@pytest.mark.anyio
async def test_file_delete_rule_write_scope_none():
    ctx = _ctx(agent_role="研究员", write_coordinator=MagicMock())
    ctx.write_scope = "none"
    result = await FileDeleteTool().execute(
        {"path": f"{RULES_DIR_REL}/回复语言.md"},
        ctx,
    )
    assert result.success is False
    assert "write_scope=none" in _tool_err(result)


@pytest.mark.anyio
async def test_str_replace_rule_rewrites(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def _fake_mutate(_repo, _uid, **kwargs):
        calls.append(str(kwargs["action"]))
        if kwargs["action"] == "read":
            return UserRuleMutationResult(
                action="read",
                changed=False,
                message="",
                name="回复语言.md",
                apply="always",
                body="以后都用中文回复",
            )
        assert kwargs["content"] == "以后都用英文回复"
        return UserRuleMutationResult(
            action="write",
            changed=True,
            message="已写入规则「回复语言.md」（常驻）。",
            name="回复语言.md",
            apply="always",
            content=str(kwargs.get("content") or ""),
        )

    _patch_local_rule_mutate(monkeypatch, _fake_mutate)
    result = await StrReplaceTool().execute(
        {
            "file_path": f"{RULES_DIR_REL}/回复语言.md",
            "old_string": "中文",
            "new_string": "英文",
        },
        _ctx(),
    )
    assert result.success is True
    assert calls == ["read", "write"]
    assert RULES_DIR_REL in (result.output or "")


@pytest.mark.anyio
async def test_file_list_catalog_root_synthesizes_rules_dir(tmp_path):
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
    )
    result = await FileListTool().execute(
        {"directory": RULES_CATALOG_ROOT},
        ctx,
    )
    assert result.success is True
    assert RULES_DIR_REL in (result.output or "")


@pytest.mark.anyio
async def test_file_list_product_named_folder_is_workspace_not_catalog(tmp_path):
    ctx = ToolContext.create(
        execution_id="e",
        run_id="r",
        agent_id="ceo",
        backend=ServerWorkspace(root=tmp_path, sandbox=SubprocessSandbox()),
        user_id="u1",
    )
    result = await FileListTool().execute({"directory": "AgentCore"}, ctx)
    assert result.success is True
    assert RULES_DIR_REL not in (result.output or "")
    assert "尚未创建" in (result.output or "")
