"""Turn Journal — persist a turn's execution fact stream and project it back.

The §8.3 Turn Journal is the唯一事实源 for a turn's execution: an append-only,
per-turn ordered stream of facts (run/tool/interaction events for a multi-agent
turn; reasoning/tool 步 for a single-agent turn; a closing ``turn_end``). It lives
in the ``turn_journal`` table (keyed by ``turn_id`` == the assistant ``message_id``)
and REPLACES the old ``messages.runs`` JSON blob.

「一切皆投影」(§8.3): nothing else stores the replay payload. The assistant
message's ``MessageDetail.runs`` is rebuilt from the journal on read via
:func:`fold.runs_from_entries`; the write side flattens the in-memory sink payload to
journal entries via :func:`entries.journal_entries_from_display_runs`. The two are exact
inverses on the display replay shape, so a turn round-trips through the journal unchanged.

Subpackages:
- ``entries`` — write path (runs payload → ordered facts)
- ``fold`` — read path (facts → runs / LLM window / resume seed)
- ``persist`` — best-effort Postgres write
- ``team_batch`` — 本回合团队状态三态（journal 纯函数投影）
"""

from .entries import KIND_TURN_END, entries_from_runs, journal_entries_from_display_runs
from .fold import (
    completed_from_journal,
    execution_id_from_journal,
    plan_from_journal,
    runs_from_entries,
    window_from_journal,
)
from .fold_cache import clear_runs_cache, runs_from_entries_cached
from .team_batch import team_batch_from_entries

__all__ = [
    "KIND_TURN_END",
    "clear_runs_cache",
    "completed_from_journal",
    "entries_from_runs",
    "execution_id_from_journal",
    "journal_entries_from_display_runs",
    "TurnJournalWriter",
    "current_journal_writer",
    "persist_turn_journal",
    "plan_from_journal",
    "runs_from_entries",
    "team_batch_from_entries",
    "runs_from_entries_cached",
    "window_from_journal",
]


def __getattr__(name: str):
    """Lazy DB-backed journal helpers — keep sidecar import closure free of sqlalchemy."""
    if name in ("TurnJournalWriter", "current_journal_writer"):
        from .writer import TurnJournalWriter, current_journal_writer

        return TurnJournalWriter if name == "TurnJournalWriter" else current_journal_writer
    if name == "persist_turn_journal":
        from .persist import persist_turn_journal

        return persist_turn_journal
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
