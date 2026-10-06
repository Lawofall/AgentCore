"""Envelope projections: date, each workspace fact line, and the CEO file index."""

from agentcore.runtime.context.envelope_switches import (
    FILE_INDEX,
    PROJECTIONS,
    RUNTIME_DATE,
    bind_envelope_omissions,
    include_file_index,
    include_runtime_date,
    normalize_omitted_projections,
    reset_envelope_omissions,
)
from agentcore.runtime.context.workspace_context import build_workspace_context
from agentcore.runtime.resolve.prompt.envelope import (
    render_ceo_turn_envelope,
    render_worker_turn_envelope,
)


class _FakeBackend:
    def __init__(self, location: str, root_label: str = "workspace") -> None:
        self.location = location
        self.root_label = root_label


def _facts() -> str:
    return build_workspace_context(
        _FakeBackend("server", root_label="白板"),
        desktop_online=False,
        run_enabled=False,
    )


def test_projection_vocabulary_is_the_assembly_order():
    assert [row.id for row in PROJECTIONS] == [
        "runtime_date",
        "execution",
        "boundary",
        "desk",
        "system",
        "git",
        "client",
        "mounts",
        "gaps",
        "sandbox",
        "interpreters",
        "file_index",
    ]


def test_normalize_keeps_known_ids_in_vocabulary_order():
    assert normalize_omitted_projections(None) == ()
    assert normalize_omitted_projections("runtime_date") == ()
    assert normalize_omitted_projections(
        ["nope", "file_index", "runtime_date", "runtime_date"]
    ) == ("runtime_date", "file_index")
    assert normalize_omitted_projections(
        ["interpreters", "nope", "file_index", "execution", "runtime_date"]
    ) == ("runtime_date", "execution", "interpreters", "file_index")


def test_omitting_the_date_drops_it_for_captain_and_worker():
    token = bind_envelope_omissions(["runtime_date"])
    try:
        assert include_runtime_date() is False
        assert include_file_index() is True
        captain = render_ceo_turn_envelope(
            workspace_context="<工作区>\n边界：这个文件夹\n</工作区>",
            workspace_file_index="文件：a.txt",
        )
        worker = render_worker_turn_envelope(
            workspace_context="<工作区>\n边界：这个文件夹\n</工作区>",
        )
    finally:
        reset_envelope_omissions(token)
    assert "<运行时>" not in captain
    assert "<运行时>" not in worker
    assert "边界：这个文件夹" in captain
    assert "边界：这个文件夹" in worker
    assert "文件：a.txt" in captain
    assert "文件：" not in worker


def test_omitting_the_file_index_keeps_workspace_facts():
    token = bind_envelope_omissions(["file_index"])
    try:
        text = render_ceo_turn_envelope(
            workspace_context="<工作区>\n边界：只看\n桌：草稿\n</工作区>",
            workspace_file_index="文件：secret.txt",
        )
    finally:
        reset_envelope_omissions(token)
    assert "<运行时>" in text
    assert "边界：只看" in text
    assert "桌：草稿" in text
    assert "secret.txt" not in text
    assert "文件：" not in text


def test_caller_can_still_suppress_the_date_without_a_conversation_flag():
    text = render_ceo_turn_envelope(include_runtime=False)
    assert "<运行时>" not in text


def test_omitting_one_fact_line_drops_only_that_line():
    token = bind_envelope_omissions(["boundary"])
    try:
        out = _facts()
    finally:
        reset_envelope_omissions(token)
    assert "边界：" not in out
    assert "执行：云端" in out
    assert "桌：本会话草稿" in out
    assert "系统：" in out
    assert out.startswith("<工作区>\n")
    assert out.endswith("\n</工作区>")


def test_omitting_every_fact_line_drops_the_workspace_tag():
    fact_ids = [row.id for row in PROJECTIONS if row.id not in {RUNTIME_DATE, FILE_INDEX}]
    token = bind_envelope_omissions(fact_ids)
    try:
        out = _facts()
    finally:
        reset_envelope_omissions(token)
    assert out == ""


def test_file_index_alone_still_opens_a_workspace_block():
    token = bind_envelope_omissions(
        [row.id for row in PROJECTIONS if row.id != FILE_INDEX]
    )
    try:
        facts = _facts()
        captain = render_ceo_turn_envelope(
            workspace_context=facts,
            workspace_file_index="文件：a.txt",
        )
        worker = render_worker_turn_envelope(workspace_context=facts)
    finally:
        reset_envelope_omissions(token)
    assert facts == ""
    assert captain.count("<工作区>") == 1
    assert "文件：a.txt" in captain
    assert "执行：" not in captain
    assert "<运行时>" not in captain
    assert worker == ""
