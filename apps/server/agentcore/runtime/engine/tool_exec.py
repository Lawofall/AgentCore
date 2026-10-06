"""Parallel tool execution for one ReAct round.

Thin facade: implementation is split by axis —

* ``tool_exec_gates`` — approval / destructive baseline
* ``tool_exec_args`` — args sanitize / miss feedback / failure markers
* ``tool_exec_call`` — single tool-call lifecycle (parse → gates → execute → end)
* ``tool_exec_parallel`` — parallel round orchestration (gather / terminal / facts)
* ``tool_exec_coalesce`` — same-round read path coalesce helpers
* ``tool_exec_citations`` — citation sink / ledger side-effects

Public import paths (``execute_tools``, ``TOOL_FAILED_MARKER``,
``with_tool_failed_marker``, ``strip_model_failure_envelope``,
``_apply_local_destructive_baseline_gate``) stay stable.
"""

from .tool_exec_args import (
    TOOL_FAILED_MARKER,
    strip_model_failure_envelope,
    with_tool_failed_marker,
)
from .tool_exec_gates import _apply_local_destructive_baseline_gate
from .tool_exec_parallel import execute_tools

__all__ = [
    "TOOL_FAILED_MARKER",
    "_apply_local_destructive_baseline_gate",
    "execute_tools",
    "strip_model_failure_envelope",
    "with_tool_failed_marker",
]
