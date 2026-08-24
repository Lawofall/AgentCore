"""Team kickoff helpers — thin re-exports of the orchestration-layer module.

Historical import path for delegate tests. Prefer
``agentcore.runtime.kickoff`` for new call sites.
"""

from __future__ import annotations

from agentcore.runtime.kickoff.summary import worker_rows

__all__ = [
    "worker_rows",
]
