"""CEO seed_notes + team_brief (共享便签 Phase 2)."""

from agentcore.runtime.delegate.seed_notes import (
    CEO_SEED_RUN_ID,
    MAX_SEED_NOTES,
    MAX_TEAM_BRIEF_CHARS,
    is_note_wall_batch,
    materialize_brief_as_seed_notes,
    parse_seed_notes,
    parse_team_brief,
    resolve_coordination,
    seed_note_wall,
)
from agentcore.runtime.events import EventSink, EventType
from agentcore.runtime.runs.notewall import NOTE_KIND_DECISION, NOTE_KIND_HEADS_UP, NoteWall


def test_parse_seed_notes_accepts_valid_items():
    notes, err = parse_seed_notes(
        [
            {"kind": "decision", "text": "  受众：初学者  "},
            {"text": "别写太长"},
        ]
    )
    assert err is None
    assert notes == [
        {"kind": NOTE_KIND_DECISION, "text": "受众：初学者"},
        {"kind": NOTE_KIND_HEADS_UP, "text": "别写太长"},
    ]


def test_parse_seed_notes_rejects_invalid():
    assert parse_seed_notes("x")[1] is not None
    too_many = [{"text": f"n{i}"} for i in range(MAX_SEED_NOTES + 1)]
    assert "最多" in (parse_seed_notes(too_many)[1] or "")
    assert "非空" in (parse_seed_notes([{"text": "  "}])[1] or "")


def test_parse_team_brief_trims_and_caps(monkeypatch):
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    brief, err = parse_team_brief("  跨波共识\n第二行  ")
    assert err is None and brief == "跨波共识\n第二行"

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    long_text = "字" * (MAX_TEAM_BRIEF_CHARS + 50)
    capped, err = parse_team_brief(long_text, execution_id="exec-brief")
    assert err is None and capped is not None and len(capped) <= MAX_TEAM_BRIEF_CHARS
    fields = spy.get("delegate.context_capped")
    assert fields["site"] == "team_brief"
    assert fields["original_chars"] == MAX_TEAM_BRIEF_CHARS + 50
    assert fields["final_chars"] == len(capped)
    assert fields["execution_id"] == "exec-brief"


def test_parse_team_brief_under_cap_does_not_log(monkeypatch):
    from agentcore.runtime import context_cap
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    brief, err = parse_team_brief("短共识")
    assert err is None and brief == "短共识"
    assert not any(name == "delegate.context_capped" for name, _ in spy.events)


def test_parse_seed_notes_caps_and_logs(monkeypatch):
    from agentcore.runtime import context_cap
    from agentcore.runtime.runs.notewall import MAX_NOTE_CHARS
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    notes, err = parse_seed_notes(
        [{"kind": "decision", "text": "字" * (MAX_NOTE_CHARS + 20)}],
        execution_id="exec-seed",
    )
    assert err is None and len(notes) == 1
    assert len(notes[0]["text"]) <= MAX_NOTE_CHARS
    fields = spy.get("delegate.context_capped")
    assert fields["site"] == "seed_note"
    assert fields["original_chars"] == MAX_NOTE_CHARS + 20
    assert fields["final_chars"] == len(notes[0]["text"])
    assert fields["execution_id"] == "exec-seed"
    assert fields["kind"] == "decision"


def test_parse_team_brief_rejects_non_string():
    assert parse_team_brief(42)[1] is not None
    assert parse_team_brief("   ")[1] is not None


def test_seed_note_wall_posts_and_emits_ceo_source():
    wall = NoteWall()
    sink = EventSink()
    count = seed_note_wall(
        wall,
        [{"kind": "decision", "text": "方向：科普向"}],
        sink=sink,
        execution_id="exec-1",
    )
    assert count == 1
    assert len(wall._notes) == 1  # noqa: SLF001
    note = wall._notes[0]  # noqa: SLF001
    assert note.run_id == CEO_SEED_RUN_ID
    events = [e for e in sink._history if e.type == EventType.TEAM_NOTE_POSTED]  # noqa: SLF001
    assert len(events) == 1
    assert events[0].payload["source"] == "ceo"
    assert events[0].payload["text"] == "方向：科普向"


def test_materialize_brief_as_seed_notes_splits_lines_and_caps():
    notes = materialize_brief_as_seed_notes(
        "已确认约束：自研画布\n\n协作后置但架构预留\n第三行"
    )
    assert [n["kind"] for n in notes] == [
        NOTE_KIND_DECISION,
        NOTE_KIND_DECISION,
        NOTE_KIND_DECISION,
    ]
    assert [n["text"] for n in notes] == [
        "已确认约束：自研画布",
        "协作后置但架构预留",
        "第三行",
    ]
    too_many = "\n".join(f"行{i}" for i in range(MAX_SEED_NOTES + 4))
    capped = materialize_brief_as_seed_notes(too_many)
    assert len(capped) == MAX_SEED_NOTES
    assert capped[0]["text"] == "行0"


def test_materialize_brief_as_seed_notes_truncates_long_line(monkeypatch):
    from agentcore.runtime import context_cap
    from agentcore.runtime.runs.notewall import MAX_NOTE_CHARS
    from tests.conftest import LogSpy

    spy = LogSpy()
    monkeypatch.setattr(context_cap, "logger", spy)
    notes = materialize_brief_as_seed_notes("字" * (MAX_NOTE_CHARS + 20))
    assert len(notes) == 1
    assert len(notes[0]["text"]) <= MAX_NOTE_CHARS
    assert spy.get("delegate.context_capped")["site"] == "seed_note"


def test_ceo_seeds_visible_to_workers_via_new_for():
    wall = NoteWall()
    seed_note_wall(
        wall,
        [{"text": "共享验收维度"}],
        sink=EventSink(),
        execution_id="e",
    )
    fresh = wall.new_for("worker-run-1")
    assert [n.text for n in fresh] == ["共享验收维度"]
    assert fresh[0].run_id == CEO_SEED_RUN_ID


def test_ceo_seeds_opening_pull_renders_once():
    """Cold-open preload contract: first pull (executor before react) sees seeds; second empty."""
    from agentcore.runtime.runs.notewall import format_notes_for_injection

    wall = NoteWall()
    seed_note_wall(
        wall,
        [{"kind": "decision", "text": "受众：初学者"}],
        sink=EventSink(),
        execution_id="e",
    )
    opening = wall.new_for("worker-run-1")
    assert [n.text for n in opening] == ["受众：初学者"]
    rendered = format_notes_for_injection(opening)
    assert "受众：初学者" in rendered
    assert "团队便签" in rendered
    assert wall.new_for("worker-run-1") == []


def test_new_for_exclude_skips_return_but_advances_cursor():
    wall = NoteWall()
    seed_note_wall(
        wall,
        [{"kind": "decision", "text": "主协调口径"}],
        sink=EventSink(),
        execution_id="e",
    )
    wall.post(
        run_id="sibling",
        agent_id="w2",
        role="架构师",
        kind=NOTE_KIND_DECISION,
        text="接口用 REST",
    )
    fresh = wall.new_for(
        "worker-run-1", exclude_run_ids=frozenset({CEO_SEED_RUN_ID})
    )
    assert [n.text for n in fresh] == ["接口用 REST"]
    assert wall.new_for("worker-run-1") == []


def test_teammate_active_count_excludes_engine_seeds():
    from agentcore.runtime.runs.notewall import SYSTEM_RUN_ID

    wall = NoteWall()
    seed_note_wall(
        wall,
        [{"text": "口径甲"}, {"text": "口径乙"}],
        sink=EventSink(),
        execution_id="e",
    )
    wall.post(
        run_id=SYSTEM_RUN_ID,
        agent_id="system",
        role="系统",
        kind=NOTE_KIND_HEADS_UP,
        text="可能冲突",
    )
    exclude = frozenset({CEO_SEED_RUN_ID, SYSTEM_RUN_ID})
    assert wall.teammate_active_count("w1", exclude_run_ids=exclude) == 0
    wall.post(
        run_id="w2",
        agent_id="w2",
        role="产品",
        kind=NOTE_KIND_DECISION,
        text="名词表用英文",
    )
    assert wall.teammate_active_count("w1", exclude_run_ids=exclude) == 1
    assert wall.teammate_active_count("w2", exclude_run_ids=exclude) == 0


def test_resolve_coordination_defaults_none():
    assert (
        resolve_coordination(
            raw=None, complexity_hint="standard", seed_notes=None, team_brief=None
        )
        == "none"
    )


def test_resolve_coordination_explicit_wall():
    assert (
        resolve_coordination(
            raw="wall", complexity_hint="standard", seed_notes=None, team_brief=None
        )
        == "wall"
    )


def test_resolve_coordination_light_forces_none():
    assert (
        resolve_coordination(
            raw="wall",
            complexity_hint="light",
            seed_notes=[{"text": "x"}],
            team_brief="brief",
        )
        == "none"
    )


def test_resolve_coordination_seed_notes_upgrades_none():
    assert (
        resolve_coordination(
            raw="none",
            complexity_hint="standard",
            seed_notes=[{"text": "定了 X"}],
            team_brief=None,
        )
        == "wall"
    )


def test_resolve_coordination_team_brief_upgrades_none():
    assert (
        resolve_coordination(
            raw=None,
            complexity_hint="standard",
            seed_notes=None,
            team_brief="共享验收",
        )
        == "wall"
    )


def test_resolve_coordination_no_playbook_name_raises_wall():
    assert (
        resolve_coordination(
            raw=None,
            complexity_hint="standard",
            seed_notes=None,
            team_brief=None,
            playbook="map_fanout",
        )
        == "none"
    )
    assert (
        resolve_coordination(
            raw="none",
            complexity_hint="standard",
            seed_notes=None,
            team_brief=None,
            playbook="cite_write_review",
        )
        == "none"
    )


def test_resolve_coordination_retired_website_playbook_does_not_default_wall():
    assert (
        resolve_coordination(
            raw=None,
            complexity_hint="standard",
            seed_notes=None,
            team_brief=None,
            playbook="build_website",
        )
        == "none"
    )
    assert (
        resolve_coordination(
            raw="none",
            complexity_hint="standard",
            seed_notes=None,
            team_brief=None,
            playbook="build_website",
        )
        == "none"
    )


def test_is_note_wall_batch_matches_setup_predicate():
    assert is_note_wall_batch(2, "wall") is True
    assert is_note_wall_batch(1, "wall") is False
    assert is_note_wall_batch(2, "none") is False
