"""Sandbox → approval policy table (安全权限与治理 §三 / §五).

Maps the workspace execution environment to whether GRANTABLE *execution-class*
tools still need a human approval prompt. File-mutation tools: local workers
always share the turn gate; cloud workers historically skipped per-call cards
for server-sandbox tools — **except** when ``file_write=ask`` (谨慎), which
must still prompt the file-mutation class on cloud (PermissionAxes 优先于
历史云端免审).

Desktop Client Tools (MCP stdio / Host face) touch the user's machine even when
the workspace is cloud — they still share the turn ApprovalGate.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.core.types import PermissionAxes
from agentcore.runtime.always_confirm import requires_always_confirm

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend


class ExecutionApprovalPosture(StrEnum):
    """Whether execution-class tools need approval (sandbox → posture table)."""

    REQUIRES_AUTH = "requires_auth"  # local subprocess — user must authorize
    AUTO_PASS = "auto_pass"  # cloud gVisor true isolation — auto-approve
    UNAVAILABLE = "unavailable"  # cloud without sandbox — tools not registered


def execution_approval_posture(backend: WorkspaceBackend | None) -> ExecutionApprovalPosture:
    """Resolve the sandbox → execution-approval cell for this workspace."""
    if backend is None:
        return ExecutionApprovalPosture.REQUIRES_AUTH
    if backend.location == "local":
        return ExecutionApprovalPosture.REQUIRES_AUTH
    # Cloud (location=server): no real sandbox → tools withheld at registry;
    # gVisor on → true isolation, execution auto-passes.
    # Dev escape hatch (CODE_EXECUTE_CLOUD_ENABLED, 安全权限与治理 §5.4): tools ARE
    # registered despite UNAVAILABLE here — the posture only feeds auto-pass, and a
    # cloud worker's execution-class call skips the per-call card via
    # :func:`cloud_worker_skips_per_call_gate` anyway, so the escape hatch executes
    # ungated without touching this table.
    if settings.gvisor_enabled:
        return ExecutionApprovalPosture.AUTO_PASS
    return ExecutionApprovalPosture.UNAVAILABLE


def worker_gate_applies(backend: WorkspaceBackend | None) -> bool:
    """Whether delegated workers need a per-call card for *all* GRANTABLE tools.

    Local subprocess: yes (real machine). Cloud: not for the full GRANTABLE set —
    either tools are withheld (no sandbox) or gVisor isolates execution (AUTO_PASS).
    File ops on cloud still skip the card when ``file_write=session``; under
    ``file_write=ask`` they keep it — see :func:`cloud_worker_skips_per_call_gate`.

    Desktop-touch tools (MCP / Host) still need a card on cloud+desktop — see
    :func:`is_desktop_touch_tool`. CEO / captain always gate GRANTABLE regardless
    of backend location (tool_exec only narrows when ``role=="worker"``).

    This is a *policy* predicate consumed by the tool_exec chokepoint (and the
    kickoff-card decision), **not** a hand-out rule: whether a worker is handed the
    turn's ``ApprovalGate`` object is no longer predicted upstream — it always is,
    when the turn has one.
    """
    return backend is not None and backend.location == "local"


def cloud_worker_skips_per_call_gate(
    backend: WorkspaceBackend | None,
    tool_name: str,
    *,
    arguments: dict[str, Any] | None = None,
    permission_axes: PermissionAxes | None = None,
    file_op_tools: frozenset[str] = frozenset(),
) -> bool:
    """True when the cloud-worker path may drop ``needs_approval`` for this tool.

    Local workers never skip via this helper (``worker_gate_applies``).
    Desktop-touch (MCP / Host) never skip. 恒确认 shapes (``git push`` /
    ``create_pr`` / ``host(action=install_package)``) never skip — the cloud sandbox
    isolates the server, not the remote being published to. File-mutation class
    under ``file_write=ask`` never skip (谨慎 must prompt reversible writes on
    cloud). Other server-sandbox tools on cloud stay historically ungated.
    """
    if worker_gate_applies(backend):
        return False
    if is_desktop_touch_tool(tool_name):
        return False
    if requires_always_confirm(tool_name, arguments):
        return False
    return not (
        permission_axes is not None
        and not permission_axes.trusts_file_writes
        and tool_name in file_op_tools
    )


def is_desktop_touch_tool(tool_name: str) -> bool:
    """True when the tool side-effects the user's machine via desktop Client Tools.

    MCP dynamic tools are named ``mcp_<server>_<tool>``. Host face tools are the
    closed ``host_class`` roster.
    """
    name = (tool_name or "").strip()
    if name.startswith("mcp_"):
        return True
    from agentcore.tools.registration import host_class_tool_names

    return name in host_class_tool_names()


def execution_tool_auto_passes(
    backend: WorkspaceBackend | None,
    tool_name: str,
    *,
    permission_axes: PermissionAxes | None = None,
) -> bool:
    """True when the tool should skip the approval prompt via sandbox / command=auto.

    Covers the whole ``execution_class`` roster (``code_execute`` / ``test_run`` /
    ``terminal`` / ``browser``) plus low-risk ``desktop_notify`` under
    ``command=auto``. Host / MCP never enter here.

    Cloud gVisor → auto-pass execution_class (sandbox isolation).
    ``command=auto`` → auto-pass execution_class + ``desktop_notify`` even on local.
    FORCE / circuit-breaker still bypass this in ``tool_exec`` (``force_breaker``).
    """
    from agentcore.tools.builtin.desktop_notify import DESKTOP_NOTIFY_TOOL_NAME
    from agentcore.tools.registration import execution_class_tool_names

    name = (tool_name or "").strip()
    is_execution = name in execution_class_tool_names()
    is_desktop_notify = name == DESKTOP_NOTIFY_TOOL_NAME
    if not is_execution and not is_desktop_notify:
        return False
    if permission_axes is not None and permission_axes.auto_executes:
        return True
    # gVisor AUTO_PASS is execution_class only (desktop_notify is a local client tool).
    if is_execution:
        return execution_approval_posture(backend) is ExecutionApprovalPosture.AUTO_PASS
    return False
