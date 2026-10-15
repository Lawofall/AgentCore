"""Named user-rule markdown: mutate by name, no reserved path."""

from __future__ import annotations

import pytest

from agentcore.memory.rules_injection import mutate_user_rule, normalize_rule_filename


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
        enroll=False,
    ):  # noqa: ARG002
        from agentcore.documents.frontmatter import set_entry_frontmatter

        body = set_entry_frontmatter(content, apply=apply, description=description)
        previous = self.docs.get(name)
        indexed = description or ""
        if not enroll:
            indexed = previous.description if previous is not None else ""
        doc = _FakeDoc(name, body, apply, indexed)
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
async def test_mutate_model_write_does_not_enroll():
    repo = _FakeRepo()
    written = await mutate_user_rule(
        repo,  # type: ignore[arg-type]
        "u1",
        folder_id=None,
        action="write",
        name="回复语言.md",
        content="以后都用中文回复",
        description="回复语言",
    )
    assert written.ok and written.changed
    assert repo.docs["回复语言.md"].description == ""


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
    assert rewritten.ok and rewritten.apply == "on_demand"
    assert "按需" in rewritten.message
    assert repo.docs["回复语言.md"].description == ""

