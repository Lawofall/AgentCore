"""harness documents_fixture：预置生效 + 用例间无残留（需 PostgreSQL）。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import func, select

from agentcore.db.models import Document
from agentcore.db.repositories import DocumentRepository
from agentcore.evals import documents_fixture as docs_fx_mod
from agentcore.evals.documents_fixture import apply_documents_fixture, purge_user_documents
from agentcore.evals.harness import _EVAL_USER_ID, EvalHarness
from agentcore.evals.types import EvalCase
from agentcore.llm.provider.protocol import LLMChunk
from agentcore.memory import assemble_injected_rules
from agentcore.memory.document_store import DocumentMemoryStore
from agentcore.memory.injection import load_memory_topics

pytestmark = pytest.mark.asyncio

_FIXTURES = Path(docs_fx_mod.__file__).resolve().parent / "fixtures"


class _ScriptedProvider:
    def __init__(self, text: str = "ok") -> None:
        self._text = text

    async def stream(self, request):  # noqa: ANN001
        yield LLMChunk(delta_content=self._text)


def _make_scope(session_factory):
    @asynccontextmanager
    async def _scope(session=None):
        if session is not None:
            yield session
            return
        async with session_factory() as owned:
            yield owned

    return _scope


async def _live_doc_count(session_factory, user_id: str) -> int:
    async with session_factory() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Document)
            .where(Document.user_id == user_id, Document.deleted_at.is_(None))
        )
        return int(result.scalar_one())


@pytest.fixture
def patch_eval_docs_session(session_factory, monkeypatch):
    """把 documents_fixture / harness 的 session 指到集成测试 schema。"""
    import agentcore.evals.documents_fixture as df
    import agentcore.evals.harness as harness_mod

    monkeypatch.setattr(df, "_session_scope", _make_scope(session_factory))
    monkeypatch.setattr(harness_mod, "purge_user_documents", df.purge_user_documents)
    monkeypatch.setattr(harness_mod, "apply_documents_fixture", df.apply_documents_fixture)
    return session_factory


async def test_apply_and_purge_memory_fixture(session_factory):
    root = _FIXTURES / "docs_memory_launch_code"
    async with session_factory() as session:
        await purge_user_documents(_EVAL_USER_ID, session=session)
        n = await apply_documents_fixture(root, _EVAL_USER_ID, session=session)
        assert n == 1
        store = DocumentMemoryStore(session=session)
        topics = await load_memory_topics(
            store, _EVAL_USER_ID, folder_id=None, enabled=True
        )
        assert any(t.name == "发射口令" for t in topics)
        body = await store.load(_EVAL_USER_ID, "主题/发射口令.md")
        assert "MARKER_LAUNCH_7F3A" in body

    async with session_factory() as session:
        deleted = await purge_user_documents(_EVAL_USER_ID, session=session)
        assert deleted >= 1
        store = DocumentMemoryStore(session=session)
        topics = await load_memory_topics(
            store, _EVAL_USER_ID, folder_id=None, enabled=True
        )
        assert topics == []


async def test_apply_user_rules_always_and_ondemand(session_factory):
    always_root = _FIXTURES / "docs_always_rule_token"
    ondemand_root = _FIXTURES / "docs_ondemand_rule_secret"

    async with session_factory() as session:
        await purge_user_documents(_EVAL_USER_ID, session=session)
        await apply_documents_fixture(always_root, _EVAL_USER_ID, session=session)
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        rules_md = await assemble_injected_rules(
            store, repo, _EVAL_USER_ID, folder_id=None, enabled=True
        )
        assert "RULE_TOKEN_Z9" in rules_md

    async with session_factory() as session:
        await purge_user_documents(_EVAL_USER_ID, session=session)
        await apply_documents_fixture(ondemand_root, _EVAL_USER_ID, session=session)
        repo = DocumentRepository(session)
        store = DocumentMemoryStore(session=session)
        rules_md = await assemble_injected_rules(
            store, repo, _EVAL_USER_ID, folder_id=None, enabled=True
        )
        assert "MARKER_RULE_Q4K" not in rules_md  # on_demand 不进 always <设定>
        listed = await repo.list_on_demand_user_rules(_EVAL_USER_ID, None)
        assert {d.name for d in listed} == {"演练暗号.md"}
        assert "MARKER_RULE_Q4K" in (listed[0].content or "")


async def test_harness_seeds_then_clears_between_cases(patch_eval_docs_session):
    """预置在 run_case 中生效，结束后无残留；下例夹具不会看到上例内容。"""
    session_factory = patch_eval_docs_session
    harness = EvalHarness(provider=_ScriptedProvider("hello"), fixtures_dir=_FIXTURES)
    orig_single = harness._run_single

    case_a = EvalCase(
        id="t_docs_a",
        category="qa",
        user_message="x",
        path="single",
        checks=[],
        documents_fixture="docs_memory_launch_code",
    )
    case_b = EvalCase(
        id="t_docs_b",
        category="qa",
        user_message="x",
        path="single",
        checks=[],
        documents_fixture="docs_always_rule_token",
    )

    seen_marker: list[str] = []

    async def _probe_a(*args, **kwargs):
        async with session_factory() as session:
            store = DocumentMemoryStore(session=session)
            seen_marker.append(await store.load(_EVAL_USER_ID, "主题/发射口令.md"))
        return await orig_single(*args, **kwargs)

    harness._run_single = _probe_a  # type: ignore[method-assign]
    outcome_a = await harness.run_case(case_a)
    assert outcome_a.error is None
    assert any("MARKER_LAUNCH_7F3A" in b for b in seen_marker)
    assert await _live_doc_count(session_factory, _EVAL_USER_ID) == 0

    async def _probe_b(*args, **kwargs):
        async with session_factory() as session:
            store = DocumentMemoryStore(session=session)
            assert await store.load(_EVAL_USER_ID, "主题/发射口令.md") == ""
            repo = DocumentRepository(session)
            rules_md = await assemble_injected_rules(
                store, repo, _EVAL_USER_ID, folder_id=None, enabled=True
            )
            assert "RULE_TOKEN_Z9" in rules_md
        return await orig_single(*args, **kwargs)

    harness._run_single = _probe_b  # type: ignore[method-assign]
    outcome_b = await harness.run_case(case_b)
    assert outcome_b.error is None
    assert await _live_doc_count(session_factory, _EVAL_USER_ID) == 0
