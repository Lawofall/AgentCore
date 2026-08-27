"""CEO seeding for the per-batch team note wall (Phase 2 · seed_notes)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from agentcore.core.logging import get_logger
from agentcore.runtime.context_cap import log_context_capped
from agentcore.runtime.events import team_note_posted
from agentcore.runtime.runs.notewall import (
    MAX_NOTE_CHARS,
    NOTE_KIND_DECISION,
    NOTE_KIND_HEADS_UP,
    NOTE_KINDS,
    NoteWall,
)

if TYPE_CHECKING:
    from agentcore.runtime.events.sink import EventSink

logger = get_logger(__name__)

CEO_SEED_RUN_ID = "__ceo_seed__"
CEO_SEED_AGENT_ID = "ceo"
CEO_SEED_ROLE = "主协调"
MAX_SEED_NOTES = 8
MAX_TEAM_BRIEF_CHARS = 1500

CoordinationMode = Literal["wall", "none"]


def resolve_coordination(
    *,
    raw: Any,
    complexity_hint: str,
    seed_notes: list[dict[str, str]] | None,
    team_brief: str | None,
    playbook: str | None = None,  # noqa: ARG001 — call-site compat; ignored
) -> CoordinationMode:
    """Resolve batch-level note-wall coordination (缺省 none；seed/brief 隐含升级).

    ``light`` always resolves to ``none`` (existing skip-wall behaviour).
    Explicit ``wall`` / ``none`` are honoured; any other / omitted value is ``none``.
    ``playbook`` is accepted for call-site compat and does not change the result.
    Non-empty ``seed_notes`` / ``team_brief`` upgrades ``none`` → ``wall`` (even if
    the CEO explicitly passed ``none``), with a debug log.
    """
    if complexity_hint == "light":
        return "none"

    if raw == "wall" or raw == "none":
        resolved: CoordinationMode = raw
    else:
        resolved = "none"

    has_seed = bool(seed_notes)
    has_brief = bool(team_brief and str(team_brief).strip())
    if resolved == "none" and (has_seed or has_brief):
        reason = "seed_notes" if has_seed else "team_brief"
        logger.debug(
            "delegate.coordination_upgraded",
            reason=reason,
            from_mode="none",
            to_mode="wall",
        )
        return "wall"
    return resolved


def is_note_wall_batch(node_count: int, coordination: str) -> bool:
    """Whether this fan-out owns a note wall (same predicate as ``setup_note_wall``)."""
    return node_count > 1 and coordination == "wall"


def _clean_brief(text: str, *, execution_id: str | None = None) -> str:
    collapsed = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    original = len(collapsed)
    if original > MAX_TEAM_BRIEF_CHARS:
        collapsed = collapsed[: MAX_TEAM_BRIEF_CHARS - 1].rstrip() + "…"
        log_context_capped(
            site="team_brief",
            original_chars=original,
            final_chars=len(collapsed),
            execution_id=execution_id,
        )
    return collapsed


def parse_team_brief(
    raw: Any, *, execution_id: str | None = None
) -> tuple[str | None, str | None]:
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, "team_brief 必须是字符串。"
    brief = _clean_brief(raw, execution_id=execution_id)
    if not brief:
        return None, "team_brief 清理后为空。"
    return brief, None


def materialize_brief_as_seed_notes(
    brief: str, *, execution_id: str | None = None
) -> list[dict[str, str]]:
    """Split ``team_brief`` into wall seeds (newline = one note; cap 8 × 200).

    CEO schema does not expose ``seed_notes``; this is the engine projection so
    升墙 is visible on the wall (看) without a second fill-surface field.
    Worker opening still injects the full brief block — callers must not also
    推增量 these seeds when the brief block is present.
    """
    items: list[dict[str, str]] = []
    for raw in brief.splitlines():
        text = " ".join(raw.split())
        if not text:
            continue
        items.append({"kind": NOTE_KIND_DECISION, "text": text})
        if len(items) >= MAX_SEED_NOTES:
            break
    if not items:
        return []
    notes, err = parse_seed_notes(items, execution_id=execution_id)
    return notes if err is None else []


def parse_seed_notes(
    raw: Any, *, execution_id: str | None = None
) -> tuple[list[dict[str, str]], str | None]:
    if raw is None:
        return [], None
    if not isinstance(raw, list):
        return [], "seed_notes 必须是数组。"
    if len(raw) > MAX_SEED_NOTES:
        return [], f"seed_notes 最多 {MAX_SEED_NOTES} 条。"
    out: list[dict[str, str]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            return [], f"seed_notes[{i}] 必须是对象。"
        kind = item.get("kind", NOTE_KIND_HEADS_UP)
        if not isinstance(kind, str):
            return [], f"seed_notes[{i}].kind 必须是字符串。"
        kind = kind if kind in NOTE_KINDS else NOTE_KIND_HEADS_UP
        text = item.get("text", "")
        if not isinstance(text, str) or not text.strip():
            return [], f"seed_notes[{i}].text 必须是非空字符串。"
        collapsed = " ".join(text.split())
        original = len(collapsed)
        if original > MAX_NOTE_CHARS:
            collapsed = collapsed[: MAX_NOTE_CHARS - 1].rstrip() + "…"
            log_context_capped(
                site="seed_note",
                original_chars=original,
                final_chars=len(collapsed),
                execution_id=execution_id,
                kind=kind,
            )
        out.append({"kind": kind, "text": collapsed})
    return out, None


def seed_note_wall(
    wall: NoteWall,
    notes: list[dict[str, str]],
    *,
    sink: EventSink,
    execution_id: str,
) -> int:
    """Pin CEO-authored notes before the first worker wave runs. Returns count seeded."""
    count = 0
    for item in notes:
        note = wall.post(
            run_id=CEO_SEED_RUN_ID,
            agent_id=CEO_SEED_AGENT_ID,
            role=CEO_SEED_ROLE,
            kind=item.get("kind", NOTE_KIND_HEADS_UP),
            text=item["text"],
        )
        if note is None:
            continue
        count += 1
        sink.emit(
            team_note_posted(
                execution_id=execution_id,
                note_id=note.note_id,
                run_id=note.run_id,
                agent_id=note.agent_id,
                role=note.role,
                kind=note.kind,
                text=note.text,
                ts=note.ts,
                source="ceo",
            )
        )
    if count:
        logger.info("delegate.seed_notes", count=count, execution_id=execution_id)
    return count
