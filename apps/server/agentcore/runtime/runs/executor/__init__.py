"""Host-side AGENT run executor: run one RunSpec node via the shared ReAct loop.

Thin facade — implementation split by lifecycle under ``runtime/runs/executor/``:

* ``.setup`` — registry / identity / opening messages
* ``.loop`` — react+capture + contract decision ladder body
* ``.retry`` — light-repair / write-pass / budget skip predicates
* ``.hooks`` — retrieval / citation domain hooks
* ``.terminal`` — salvage / cancel / terminal RunState
* ``.context`` — worker context blocks / opening messages
* ``.shared`` — react capture / priced failure / finish override
* ``.agent`` / ``.captain`` / ``.continuation`` — entry builders
* ``.node`` / ``.env`` / ``.identities`` / ``.escalation`` — node wiring

Public import: ``agentcore.runtime.runs.executor`` (re-exports below) or
``agentcore.runtime.runs.executor.<leaf>``.
→ 见设计: docs/03-AI核心/执行引擎架构设计.md §八（Run 模型）
"""

from __future__ import annotations

from agentcore.runtime.runs.executor.agent import build_agent_executor
from agentcore.runtime.runs.executor.captain import (
    build_captain_executor,
    build_captain_resumer,
)
from agentcore.runtime.runs.executor.continuation import continue_run
from agentcore.runtime.runs.executor.identities import ESCALATION_CONCURRENCY_CAP, DelegateFactory

__all__ = [
    "DelegateFactory",
    "ESCALATION_CONCURRENCY_CAP",
    "build_agent_executor",
    "build_captain_executor",
    "build_captain_resumer",
    "continue_run",
]
