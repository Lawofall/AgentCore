"""Log correlation context — one trace id across a turn's whole lifecycle.

structlog's contextvars are auto-merged into every log line (stdout + JSONL
file) by ``merge_contextvars`` (see ``core/logging.py``). Binding the
correlation keys here once at each execution boundary makes every downstream log
line carry them with zero per-call wiring.

AgentCore runs the team in-process (asyncio): a delegated worker / DAG node runs
as a child task, and ``contextvars`` are copied into a task at creation — so a
``trace_id`` bound at the turn boundary auto-propagates into every worker's logs
with no payload threading (the in-process analogue of the reference design's
cross-NATS trace stitching).

Canonical keys (bound where they first become known):
  - ``trace_id``        one user interaction, end to end (minted at turn start)
  - ``conversation_id`` / ``attempt_id`` / ``user_id``
  - ``message_id``      assistant message (= durable journal/audit turn_id)
  - ``agent_id``        current agent (turn start; re-scoped per delegation run)
  - ``run_id`` / ``depth``  delegation / DAG sub-node (scoped via ``log_context``)
  - ``http_method`` / ``http_path`` / ``http_req_id``  request identity
    (``RequestAttributionMiddleware``; request-scoped)
  - ``client_platform`` / ``client_version``  raw ``X-Client-*`` headers
    (same middleware; ``-`` = header absent, ``""`` = present but empty)
  - ``stream_path_reason``  optional allowlisted ``X-AgentCore-Stream-Path-Reason``
    (desktop overbridge enum; unbound when absent / unknown — not a ``-`` sentinel)

``attempt_id`` is the N-th run of a turn (fresh on resume). It is deliberately
*not* named ``turn_id`` — that name belongs to the durable journal/audit identity
(``≡ message_id``). Distinct from billing's cost-parentage markers (a DB column).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import structlog


def new_trace_id() -> str:
    """Mint a fresh correlation id for one user interaction."""
    return uuid4().hex


def bind_log_context(**ids: Any) -> None:
    """Bind correlation keys into the current structlog contextvars.

    Empty / None values are dropped so a missing id never overwrites an
    already-bound one with a blank.
    """
    cleaned = {k: v for k, v in ids.items() if v}
    if cleaned:
        structlog.contextvars.bind_contextvars(**cleaned)


def unbind_log_context(*keys: str) -> None:
    """Drop bound keys so a later call in the same task cannot leak stale values."""
    cleaned = [k for k in keys if k]
    if cleaned:
        structlog.contextvars.unbind_contextvars(*cleaned)


def clear_log_context() -> None:
    """Reset all bound correlation keys (call at each worker task entry)."""
    structlog.contextvars.clear_contextvars()


def log_context(**ids: Any) -> Any:
    """Scoped bind that auto-restores prior values on exit.

    Use around a single delegation / DAG sub-run so its logs (and its nested
    tools') carry ``run_id`` / ``depth`` / ``agent_id`` without leaking those
    keys back to the parent once the run finishes.
    """
    cleaned = {k: v for k, v in ids.items() if v}
    return structlog.contextvars.bound_contextvars(**cleaned)


def get_log_value(key: str, default: str = "") -> str:
    """Read one correlation id already bound in the current context."""
    value = structlog.contextvars.get_contextvars().get(key, default)
    return str(value) if value else default
