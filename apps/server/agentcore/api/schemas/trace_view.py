"""Owner-facing model transcript for the desktop developer mode."""

from typing import Any

from pydantic import BaseModel, Field


class JournalLine(BaseModel):
    """One window fact, in the order it was written.

    Display events that only paint the chat stay in the journal and are omitted
    here. ``payload`` is the stored object. ``mark`` is the single field that tells
    this line from its neighbors (a round, a tool name, or a run). ``run_id``
    and ``round_idx`` are set only on ``llm_call``, so the client can ask for
    that call's folded window.
    """

    id: str
    kind: str
    ts: str | None = None
    mark: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    run_id: str | None = None
    round_idx: int | None = None


class TurnTraceResponse(BaseModel):
    """One assistant turn's model transcript.

    ``gap`` is set when there is nothing to list. ``lines`` is empty then.
    Display events stay in the journal and are not listed.
    """

    trace_id: str | None = None
    where: str | None = None
    model: str | None = None
    duration_ms: int | None = None
    in_progress: bool = False
    gap: str | None = None
    lines: list[JournalLine] = Field(default_factory=list)
