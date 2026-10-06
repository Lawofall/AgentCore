"""Unit tests for write-side always-entry quota (闸在写侧)."""

from __future__ import annotations

from agentcore.api.routes.documents import _always_chars
from agentcore.memory.always_quota import (
    _AI_DENIED_MESSAGE,
    _CARD_SUMMARY,
    _USER_CREATE_DENIED,
    _USER_OVER_WARNING,
    AlwaysUsage,
    always_entry_chars,
    evaluate_always_write,
    measure_always_usage,
    project_usage_after,
)


def test_always_entry_chars_strips_frontmatter():
    raw = "---\napply: always\ndescription: x\n---\nhello"
    assert always_entry_chars(raw) == len("hello")


def test_always_entry_chars_unclosed_is_zero():
    assert always_entry_chars("---\napply: always\nno close") == 0


def test_always_chars_null_for_non_always_rows():
    class _Row:
        def __init__(
            self,
            *,
            kind: str,
            role: str,
            apply_mode: str,
            content: str,
            ai_maintained: bool = False,
        ) -> None:
            self.kind = kind
            self.role = role
            self.apply_mode = apply_mode
            self.content = content
            self.ai_maintained = ai_maintained

    always = _Row(
        kind="document",
        role="rule",
        apply_mode="always",
        content="---\napply: always\n---\nhello",
    )
    assert _always_chars(always) == len("hello")  # type: ignore[arg-type]
    assert (
        _always_chars(
            _Row(kind="document", role="rule", apply_mode="on_demand", content="x")  # type: ignore[arg-type]
        )
        is None
    )
    unbounded = _Row(
        kind="document",
        role="rule",
        apply_mode="paths",
        content="---\napply: paths\npaths: **\n---\nhello",
    )
    assert _always_chars(unbounded) == len("hello")  # type: ignore[arg-type]
    bounded = _Row(
        kind="document",
        role="rule",
        apply_mode="paths",
        content="---\napply: paths\npaths: src/*.ts\n---\nhello",
    )
    assert _always_chars(bounded) is None  # type: ignore[arg-type]
    assert (
        _always_chars(
            _Row(kind="folder", role="general", apply_mode="always", content="")  # type: ignore[arg-type]
        )
        is None
    )
    assert (
        _always_chars(
            _Row(  # type: ignore[arg-type]
                kind="document",
                role="rule",
                apply_mode="always",
                content="---\napply: always\n---\nhello",
                ai_maintained=True,
            )
        )
        is None
    )


def test_always_chars_null_when_disputed():
    class _Row:
        kind = "document"
        role = "rule"
        apply_mode = "always"
        content = "---\napply: always\n---\nhello"
        ai_maintained = False
        disputed_at = "2026-07-19T12:00:00Z"

    assert _always_chars(_Row()) is None  # type: ignore[arg-type]


def test_user_edit_existing_always_over_limit_allows_with_warning():
    projected = AlwaysUsage(used_chars=30_000, max_chars=24_000, fingerprint="fp1")
    decision = evaluate_always_write(
        writer="user",
        editing_existing_always=True,
        current_used=20_000,
        projected=projected,
    )
    assert decision.allowed is True
    assert decision.warning == _USER_OVER_WARNING


def test_user_facing_quota_copy_omits_char_meter():
    texts = (
        _USER_OVER_WARNING,
        _USER_CREATE_DENIED,
        _AI_DENIED_MESSAGE,
        _CARD_SUMMARY.format(denied=1),
    )
    for text in texts:
        assert "{used}" not in text
        assert "{max}" not in text
        assert "24000" not in text
        assert "字符" not in text


def test_user_create_over_limit_denied():
    projected = AlwaysUsage(used_chars=30_000, max_chars=24_000, fingerprint="fp1")
    decision = evaluate_always_write(
        writer="user",
        editing_existing_always=False,
        current_used=20_000,
        projected=projected,
    )
    assert decision.allowed is False
    assert decision.message == _USER_CREATE_DENIED
    assert "字符" not in decision.message
    assert "24000" not in decision.message


def test_user_create_adding_nothing_while_over_allowed():
    """An empty new entry is how content moves out of a bloated always entry."""
    projected = AlwaysUsage(used_chars=30_000, max_chars=24_000, fingerprint="fp1")
    decision = evaluate_always_write(
        writer="user",
        editing_existing_always=False,
        current_used=30_000,
        projected=projected,
    )
    assert decision.allowed is True
    assert decision.warning is None


def test_ai_growth_over_limit_denied():
    projected = AlwaysUsage(used_chars=25_000, max_chars=24_000, fingerprint="fp1")
    decision = evaluate_always_write(
        writer="ai",
        editing_existing_always=True,
        current_used=20_000,
        projected=projected,
    )
    assert decision.allowed is False
    assert decision.message == _AI_DENIED_MESSAGE


def test_ai_shrink_while_over_allowed():
    projected = AlwaysUsage(used_chars=24_500, max_chars=24_000, fingerprint="fp1")
    decision = evaluate_always_write(
        writer="ai",
        editing_existing_always=True,
        current_used=30_000,
        projected=projected,
    )
    assert decision.allowed is True
    assert decision.warning is None


def test_under_limit_always_allowed():
    projected = AlwaysUsage(used_chars=100, max_chars=24_000, fingerprint="fp")
    for writer, existing in (("user", False), ("user", True), ("ai", True)):
        d = evaluate_always_write(
            writer=writer,
            editing_existing_always=existing,
            current_used=50,
            projected=projected,
        )
        assert d.allowed is True
        assert d.warning is None


def test_quota_disabled_when_max_zero():
    projected = AlwaysUsage(used_chars=999_999, max_chars=0, fingerprint="fp")
    d = evaluate_always_write(
        writer="ai",
        editing_existing_always=False,
        current_used=0,
        projected=projected,
    )
    assert d.allowed is True


def test_always_usage_percent():
    u = AlwaysUsage(used_chars=12_000, max_chars=24_000)
    assert u.percent == 50.0
    assert u.over_limit is False
    assert AlwaysUsage(used_chars=25_000, max_chars=24_000).over_limit is True


class _Doc:
    def __init__(self, id: str, content: str) -> None:
        self.id = id
        self.content = content


def test_project_usage_after_replaces_excluded():
    docs = [
        _Doc("a", "---\napply: always\n---\n" + ("x" * 100)),
        _Doc("b", "---\napply: always\n---\n" + ("y" * 50)),
    ]
    # type: ignore[arg-type] — duck-typed for the helper
    projected = project_usage_after(
        docs,  # type: ignore[arg-type]
        exclude_id="a",
        new_chars=200,
        new_is_always=True,
    )
    assert projected.used_chars == 250


async def test_measure_usage_entry_sum_equals_used_and_split():
    """各常驻条目 always_entry_chars 之和 == used_chars == global_chars + project_chars."""
    global_body = "---\napply: always\n---\nglobal-hi"
    project_body = "---\napply: always\n---\nproj-hi"
    g = _Doc("g1", global_body)
    p = _Doc("p1", project_body)
    g_chars = always_entry_chars(global_body)
    p_chars = always_entry_chars(project_body)

    class FakeRepo:
        async def list_injectable_rules(self, user_id, folder_id, *, ai_maintained):
            # User-rule pool (ai_maintained=False); bool filters kept for other callers.
            if folder_id is None:
                docs = [g]
            elif folder_id == "F1":
                docs = [p]
            else:
                docs = []
            if ai_maintained is True:
                return []
            if ai_maintained is False:
                return docs
            return docs  # None → both (here only user docs)

        async def list_path_user_rules(self, user_id, folder_id):
            del user_id, folder_id
            return []

    usage_global = await measure_always_usage(FakeRepo(), "u", folder_id=None)  # type: ignore[arg-type]
    assert usage_global.used_chars == g_chars
    assert usage_global.global_chars == g_chars
    assert usage_global.project_chars == 0
    assert usage_global.used_chars == usage_global.global_chars + usage_global.project_chars

    usage_proj = await measure_always_usage(FakeRepo(), "u", folder_id="F1")  # type: ignore[arg-type]
    assert usage_proj.used_chars == g_chars + p_chars
    assert usage_proj.global_chars == g_chars
    assert usage_proj.project_chars == p_chars
    assert usage_proj.used_chars == usage_proj.global_chars + usage_proj.project_chars


async def test_measure_usage_counts_only_user_rules():
    """Quota pool is user rules (ai_maintained=False); AI cores do not occupy it."""
    global_user = _Doc("gu", "---\napply: always\n---\ngu")
    project_user = _Doc("pu", "---\napply: always\n---\npu")
    calls: list[tuple[str | None, bool | None]] = []

    class FakeRepo:
        async def list_injectable_rules(self, user_id, folder_id, *, ai_maintained):
            calls.append((folder_id, ai_maintained))
            if ai_maintained is not False:
                raise AssertionError("quota path must count user rules only")
            if folder_id is None:
                return [global_user]
            if folder_id == "F1":
                return [project_user]
            return []

        async def list_path_user_rules(self, user_id, folder_id):
            del user_id, folder_id
            return []

    usage = await measure_always_usage(FakeRepo(), "u", folder_id="F1")  # type: ignore[arg-type]
    assert calls == [(None, False), ("F1", False)]
    g_chars = always_entry_chars(global_user.content)
    p_chars = always_entry_chars(project_user.content)
    assert usage.global_chars == g_chars
    assert usage.project_chars == p_chars
    assert usage.used_chars == g_chars + p_chars


async def test_starred_assembly_always_pool_ignores_column_only_docs():
    """A star's 必带 is the account pool. A column-always doc not on the star does not count."""
    from agentcore.db.models import LlmModelProfile, User

    on_star = _Doc("star-doc", "---\napply: always\n---\n" + ("s" * 8))
    on_star.user_id = "u"  # type: ignore[attr-defined]
    on_star.deleted_at = None  # type: ignore[attr-defined]
    on_star.ai_maintained = False  # type: ignore[attr-defined]
    column_only = _Doc("col", "---\napply: always\n---\n" + ("c" * 40))

    class _Skill:
        document_id = "star-doc"
        apply_mode = "always"

    class _Session:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, model, key):  # type: ignore[no-untyped-def]
            del key
            if model is User:
                return type("U", (), {"default_assembly_id": "asm-1"})()
            if model is LlmModelProfile:
                return type("A", (), {"user_id": "u"})()
            return None

        async def execute(self, stmt):  # type: ignore[no-untyped-def]
            del stmt
            self.calls += 1
            rows = [_Skill()] if self.calls == 1 else [on_star]

            class _Result:
                def scalars(self):  # type: ignore[no-untyped-def]
                    return iter(rows)

            return _Result()

    class _Repo:
        def __init__(self) -> None:
            self._session = _Session()

        async def list_injectable_rules(self, user_id, folder_id, *, ai_maintained):  # type: ignore[no-untyped-def]
            del user_id, ai_maintained
            if folder_id is None:
                raise AssertionError("starred account pool must not read the column")
            return [column_only]

        async def list_path_user_rules(self, user_id, folder_id):  # type: ignore[no-untyped-def]
            del user_id, folder_id
            return []

    usage = await measure_always_usage(_Repo(), "u", folder_id=None)  # type: ignore[arg-type]
    assert usage.used_chars == always_entry_chars(on_star.content)
    assert usage.global_chars == usage.used_chars

    usage_folder = await measure_always_usage(_Repo(), "u", folder_id="F1")  # type: ignore[arg-type]
    assert usage_folder.global_chars == always_entry_chars(on_star.content)
    assert usage_folder.project_chars == always_entry_chars(column_only.content)
