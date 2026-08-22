"""In-process tool-approval coordination (MVP, single-worker).

A GRANTABLE tool call is suspended until the user authorizes it: the running
engine task ``await``s a Future, and a separate HTTP request (the resolve
endpoint) sets that Future. State is in-process — the same single-worker posture
the rate limiter already takes (see ``config.py``); front with Redis to scale to
multiple workers.

Scope: the CEO chat path and every delegated worker / debater share this SAME
per-turn gate object whenever the turn has one — a worker must not run code or
mutate files on the user's real machine without the same consent the CEO gives.
Which calls actually raise a card is decided per call at the tool_exec chokepoint
(``sandbox_approval``): cloud workers stay un-gated for server-sandbox tools (the
sandbox is isolated) but still prompt for MCP / Host / 恒确认 / ``file_write=ask``.
A ``None`` gate means the turn has nobody to ask — not「免审」.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.core.types import DEFAULT_PERMISSION_AXES, PermissionAxes, ToolApproval
from agentcore.runtime.always_confirm import is_git_remote_publish, requires_always_confirm
from agentcore.runtime.events import (
    EventSink,
    approval_required,
    approval_resolved,
)
from agentcore.runtime.interaction import InteractionKind
from agentcore.runtime.ports import ClientRequestBridge

logger = get_logger(__name__)

# Argument values longer than this are truncated in the SSE preview so a big
# file_write body does not bloat the approval event.
_PREVIEW_VALUE_MAX = 600
# code_execute's ``code`` is the review surface — users must see enough to approve.
_PREVIEW_CODE_EXECUTE_CODE_MAX = 20_000
_TRUNCATION_SUFFIX = "… [truncated]"


def tool_call_requires_approval(
    tool_name: str, approval: ToolApproval, arguments: dict[str, Any]
) -> bool:
    """Whether a tool call must pass ``ApprovalGate`` before execution.

    GRANTABLE tools always do — except a plan-bound ``file_batch`` whose ops are
    within a confirmed ``organize_plan`` (方案确认即批次授权，不再二次弹卡).
    ``git`` / ``terminal`` / ``host`` are ``NEVER`` at schema level but mutating
    subcommands / Host GRANTABLE actions are gated here — same posture as ``file_write``.
    """
    if tool_name == "file_batch":
        plan_id = str(arguments.get("organize_plan_id") or "").strip()
        if plan_id or bool(arguments.get("organize_undo")):
            # Undo is a user-initiated reverse of an already-confirmed plan.
            if bool(arguments.get("organize_undo")):
                return False
            from agentcore.workspace.organize_plan_store import get_plan, ops_within_plan

            ops = arguments.get("operations")
            if isinstance(ops, list):
                plan = get_plan(plan_id)
                if (
                    plan is not None
                    and plan.active
                    and ops_within_plan(plan, [o for o in ops if isinstance(o, dict)])
                    is None
                ):
                    return False
    if approval is ToolApproval.GRANTABLE:
        return True
    if tool_name == "git":
        from agentcore.tools.builtin.git_ops import git_call_is_write

        return git_call_is_write(arguments)
    if tool_name == "terminal":
        from agentcore.tools.builtin.terminal import terminal_approval_subcommands

        subcommand = str(arguments.get("subcommand", "")).strip().lower()
        return subcommand in terminal_approval_subcommands()
    if tool_name == "host":
        from agentcore.tools.builtin.host import host_call_requires_approval

        return host_call_requires_approval(arguments)
    return False


class ApprovalDecision(StrEnum):
    """How the user (or a timeout / orphan) settled a tool-approval request."""

    APPROVE = "approve"  # allow this one call
    APPROVE_ALWAYS = "approve_always"  # allow this tool for the rest of the turn
    # allow the whole file-mutation class (file_write / str_replace / file_delete /
    # file_move) for the rest of the turn — one click for a multi-file or mixed-op
    # task instead of granting each tool name separately. code_execute is NOT in the
    # class (a higher-risk side effect) and keeps its own per-tool gate (安全权限与
    # 治理 §三 边界2: 信任"这类操作", 不是"随便干").
    APPROVE_ALWAYS_FILES = "approve_always_files"
    DENY = "deny"  # refuse; the model is told and may adapt
    ORPHANED = "orphaned"  # 热路失效（进程/lease 恢复后旧卡不可答）


@dataclass(frozen=True)
class DelegationGrant:
    """An active per-delegation grant keyed by ``execution_id`` (开工卡一次授权)."""

    execution_id: str


def _preview_value_max(tool_name: str, key: str) -> int:
    if tool_name == "code_execute" and key == "code":
        return _PREVIEW_CODE_EXECUTE_CODE_MAX
    return _PREVIEW_VALUE_MAX


def _is_permanent_delete(tool_name: str, arguments: dict[str, Any]) -> bool:
    """True when the call permanently deletes (still requires an approval card)."""
    if tool_name == "file_delete":
        return bool(arguments.get("permanent"))
    if tool_name == "file_batch":
        ops = arguments.get("operations")
        if not isinstance(ops, list):
            return False
        return any(
            isinstance(op, dict)
            and str(op.get("op") or "").strip().lower() == "delete"
            and bool(op.get("permanent"))
            for op in ops
        )
    return False


# Private aliases keep this module's call sites (and older imports) stable; the
# predicate itself lives one layer up so pre-authorize skip paths share it.
_is_git_remote_publish = is_git_remote_publish
_requires_always_confirm = requires_always_confirm
_is_git_push = is_git_remote_publish


def _preview_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Bound large string values so the approval SSE event stays small."""
    preview: dict[str, Any] = {}
    for key, value in arguments.items():
        if isinstance(value, str):
            limit = _preview_value_max(tool_name, key)
            if len(value) > limit:
                preview[key] = value[:limit] + _TRUNCATION_SUFFIX
            else:
                preview[key] = value
        else:
            preview[key] = value
    return preview


@dataclass
class ApprovalGate:
    """Per-turn gate suspending GRANTABLE tool calls until the user decides.

    One instance per chat turn. ``_granted`` remembers tools the user chose to
    allow for the rest of the turn, so a LATER call to the same tool does not
    re-prompt. ``_denied`` remembers tools the user (or a timeout) refused, so a
    later call to the same tool skips the card and returns DENY immediately —
    denials are a governance signal, not an invitation to re-ask. A grant also
    sweeps the matching calls ALREADY suspended on this gate (parallel workers
    share one gate in local mode), so a single "allow" clears every matching
    pending prompt at once — not just the one the user clicked.

    Two grant scopes: ``APPROVE_ALWAYS`` whitelists the ONE tool of the card;
    ``APPROVE_ALWAYS_FILES`` whitelists the whole file-mutation class
    (``file_op_tools``) so a multi-file / mixed-op task is unblocked with one click.
    ``per_call_tools`` (when non-empty) refuses a turn-wide grant and downgrades to
    one-shot — historically used for execution tools; now empty by default
    (Cursor-aligned turn grant for ``code_execute`` / ``test_run``).
    """

    sink: EventSink
    conversation_id: str
    registry: ClientRequestBridge
    timeout_seconds: float | None
    # Per-tool approval wait ceilings; unset tools use timeout_seconds.
    timeout_overrides: dict[str, float] = field(default_factory=dict)
    # The file-mutation tool class an APPROVE_ALWAYS_FILES grant covers
    # (file_write / str_replace / file_delete / file_move, PLUS git write
    # subcommands). Injected at construction via approval_class_tool_names()
    # (GRANTABLE ∩ FILESYSTEM + git) — see run.py / resume/pipeline.py wiring — so
    # one file-class grant also clears git writes; single source of truth.
    # empty when not wired (the class grant then degrades to granting nothing).
    file_op_tools: frozenset[str] = frozenset()
    # Tools whose turn-wide「本轮内都允许」is refused (downgraded to one-shot).
    # Injected from ``per_call_tool_names()`` — empty by default (Cursor-aligned);
    # non-empty keeps the downgrade path for defense in depth / future re-tighten.
    per_call_tools: frozenset[str] = frozenset()
    # Medium-risk tools a kickoff grant covers (统一授权白名单). Injected from
    # ``delegation_grantable_tool_names`` — see tools.builtin.
    # Host L2/L3 must NOT be in this set (host_class · Host 定案).
    delegation_grantable_tools: frozenset[str] = frozenset()
    # Host-face GRANTABLE tools (host_class ∩ GRANTABLE). When ``host=session``,
    # these skip per-call cards without eating kickoff / command=auto silent grant.
    host_class_tools: frozenset[str] = frozenset()
    # Three-axis session permission (能力授权 / 写文件 / 组团卡). ``command=ask``
    # refuses kickoff grants; ``file_write=session`` trusts reversible mutations;
    # ``command=auto`` auto-passes execution (see sandbox_approval).
    # ``host`` axis is orthogonal: ask = per-call Host GRANTABLE actions
    # (shell / open_settings / set_audio / restart_service);
    # session = trust those via ``_session_host_trust_covers``.
    # ``install_package`` is always-confirm and never covered.
    permission_axes: PermissionAxes = field(default_factory=lambda: DEFAULT_PERMISSION_AXES)
    _granted: set[str] = field(default_factory=set)
    # Tools the user (or timeout→deny) refused this turn — later calls skip the card.
    _denied: set[str] = field(default_factory=set)
    _delegation_grants: dict[str, DelegationGrant] = field(default_factory=dict)

    def _delegation_covers(self, execution_id: str, tool_name: str) -> bool:
        # command=ask: never silently consume a kickoff grant (对齐 observe 执行侧).
        # command=auto: execution auto-passes elsewhere; grant still covers if present.
        if not self.permission_axes.auto_executes:
            return False
        if not execution_id or tool_name not in self.delegation_grantable_tools:
            return False
        return execution_id in self._delegation_grants

    def _session_file_trust_covers(self, tool_name: str, arguments: dict[str, Any]) -> bool:
        """file_write=session: trust reversible file-mutation class without per-call cards.

        Permanent deletes and structured ``git push`` / ``create_pr`` still prompt.
        Execution-class tools are not in ``file_op_tools`` and still need kickoff /
        turn grant / per-call / auto.
        """
        if not self.permission_axes.trusts_file_writes:
            return False
        if tool_name not in self.file_op_tools:
            return False
        if _is_permanent_delete(tool_name, arguments):
            return False
        return not _is_git_remote_publish(tool_name, arguments)

    def _session_host_trust_covers(self, tool_name: str) -> bool:
        """host=session: trust Host GRANTABLE tools without per-call cards."""
        if not self.permission_axes.trusts_host:
            return False
        return tool_name in self.host_class_tools

    def grant_delegation(self, execution_id: str) -> None:
        """Record a kickoff grant so medium-risk tools skip per-call for this delegation."""
        if not execution_id:
            return
        self._delegation_grants[execution_id] = DelegationGrant(execution_id=execution_id)
        logger.info("delegation.grant_issued", execution_id=execution_id)

    def revoke_delegation(self, execution_id: str) -> None:
        """Clear the per-delegation grant when a delegate segment ends."""
        if self._delegation_grants.pop(execution_id, None) is not None:
            logger.info("delegation.grant_revoked", execution_id=execution_id)

    def has_delegation_grant(self, execution_id: str) -> bool:
        return bool(execution_id) and execution_id in self._delegation_grants

    def will_prompt(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        execution_id: str = "",
        force: bool = False,
    ) -> bool:
        """True if :meth:`authorize` would suspend for a human decision.

        Mirrors the opening short-circuits of ``authorize`` (delegation / session
        file / session host / turn ``_granted`` / ``_denied``). ``force=True``
        always prompts so safety-breaker telemetry stays honest under kickoff or
        session trust that would otherwise auto-pass.
        """
        if force:
            return True
        # Always-confirm: never short-circuit via session / kickoff / turn grants.
        if _requires_always_confirm(tool_name, arguments):
            return tool_name not in self._denied
        if self._delegation_covers(execution_id, tool_name):
            return False
        if self._session_file_trust_covers(tool_name, arguments):
            return False
        if self._session_host_trust_covers(tool_name):
            return False
        if tool_name in self._granted:
            return False
        return tool_name not in self._denied

    async def authorize(
        self,
        *,
        tool_name: str,
        tool_call_id: str,
        arguments: dict[str, Any],
        execution_id: str = "",
        force: bool = False,
    ) -> ApprovalDecision:
        """Block until the user authorizes (or denies) this tool call.

        A kickoff grant (``grant_delegation`` / continue on the开工卡) short-circuits
        medium-risk tools for THAT ``execution_id`` before the per-turn grant or
        per-call prompt — unless ``command=ask``. Under ``file_write=session``, the
        file-mutation class is also session-trusted (permanent deletes still prompt).
        ``APPROVE_ALWAYS`` also whitelists ``tool_name`` for the rest of the turn;
        ``APPROVE_ALWAYS_FILES`` whitelists the whole ``file_op_tools`` class.
        A prior ``DENY`` for ``tool_name`` this turn short-circuits without re-prompting.

        ``force=True`` (true safety-breaker one-shot): skip kickoff / turn /
        session-file grants so catastrophic shapes still require a human click even
        under ``command=auto``. Turn-wide grants from a forced card are refused
        (one-shot only) so a single click cannot silently clear sibling destructive
        prompts. Callers pass ``force=False`` for ``sensitive.path_read_ask`` so
        APPROVE_ALWAYS may write a same-tool turn grant while still forcing the
        first card via the breaker entrance (read tools are not kickoff/session
        covered). Structured ``git push`` / ``create_pr`` and
        ``host(action=install_package)``
        likewise always prompt (session / kickoff / turn grants do not cover them).
        """
        always_confirm = _requires_always_confirm(tool_name, arguments)

        if (
            not force
            and not always_confirm
            and self._delegation_covers(execution_id, tool_name)
        ):
            logger.debug(
                "approval.delegation_grant",
                tool=tool_name,
                execution_id=execution_id,
            )
            return ApprovalDecision.APPROVE

        if (
            not force
            and not always_confirm
            and self._session_file_trust_covers(tool_name, arguments)
        ):
            logger.debug("approval.session_file_trust", tool=tool_name)
            return ApprovalDecision.APPROVE

        if (
            not force
            and not always_confirm
            and self._session_host_trust_covers(tool_name)
        ):
            logger.debug("approval.session_host_trust", tool=tool_name)
            return ApprovalDecision.APPROVE

        if not force and not always_confirm and tool_name in self._granted:
            return ApprovalDecision.APPROVE

        # Prior deny (user click or timeout) for this tool this turn: do not re-prompt.
        if tool_name in self._denied:
            logger.info("approval.denied_reuse", tool=tool_name, tool_call_id=tool_call_id)
            return ApprovalDecision.DENY

        approval_id = tool_call_id
        preview = _preview_arguments(tool_name, arguments)
        timeout = self.timeout_overrides.get(tool_name, self.timeout_seconds)
        try:
            decision = await self.registry.suspend(
                approval_id,
                self.conversation_id,
                kind=InteractionKind.APPROVAL,
                payload={
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": preview,
                },
                timeout=timeout,
                on_suspended=lambda: self.sink.emit(
                    approval_required(
                        approval_id=approval_id,
                        conversation_id=self.conversation_id,
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        arguments=preview,
                    )
                ),
            )
        except TimeoutError:
            logger.info("approval.timeout", tool=tool_name, approval_id=approval_id)
            from agentcore.runtime.audit.hooks import on_approval_timeout

            on_approval_timeout(tool_name=tool_name, tool_call_id=tool_call_id)
            decision = ApprovalDecision.DENY

        if decision is ApprovalDecision.DENY:
            self._denied.add(tool_name)
        elif decision is ApprovalDecision.APPROVE_ALWAYS:
            refuse_turn_grant = (
                force
                or always_confirm
                or (
                    tool_name in self.per_call_tools
                    and not self._delegation_covers(execution_id, tool_name)
                )
            )
            if refuse_turn_grant:
                # force / always-confirm / per_call_tools: authorize THIS call only.
                logger.info(
                    "approval.turn_grant_refused",
                    tool=tool_name,
                    approval_id=approval_id,
                    force=force,
                    always_confirm=always_confirm,
                )
                decision = ApprovalDecision.APPROVE
            else:
                self._granted.add(tool_name)
                self._sweep_pending_tools(frozenset({tool_name}))
        elif decision is ApprovalDecision.APPROVE_ALWAYS_FILES:
            if force or always_confirm:
                logger.info(
                    "approval.file_grant_refused",
                    tool=tool_name,
                    approval_id=approval_id,
                    always_confirm=always_confirm,
                )
                decision = ApprovalDecision.APPROVE
            else:
                # Grant the whole file-mutation class for the turn, and sweep every
                # already-suspended file-op call — so one click clears writes, edits,
                # deletes and moves together (code_execute is not in the class;
                # pending always-confirm cards are skipped in ``_sweep_pending_tools``).
                self._granted.update(self.file_op_tools)
                self._sweep_pending_tools(self.file_op_tools)
        self.sink.emit(
            approval_resolved(
                approval_id=approval_id,
                tool_call_id=tool_call_id,
                decision=decision,
            )
        )
        from agentcore.runtime.audit.hooks import on_approval_resolved

        on_approval_resolved(
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            decision=decision.value,
            arguments=preview,
        )
        return decision

    def _sweep_pending_tools(self, tool_names: frozenset[str]) -> None:
        """Retroactively APPROVE every suspended call whose tool is in ``tool_names``.

        A grant whitelists tools via ``_granted``, but that only short-circuits calls
        that reach :meth:`authorize` AFTER the grant. In local mode this gate is
        shared by parallel workers, so several matching calls can already be suspended
        (each past the ``_granted`` check, awaiting its own Future) the instant the
        user clicks "allow for the turn" — without this they would each still need a
        click. The registry is the authoritative pending set, so sweeping it here
        closes the race the client cannot (its view is eventually-consistent over
        SSE). Resolving a sibling wakes its own ``authorize`` (which returns APPROVE
        and emits that call's own ``approval_resolved``); ``resolve`` is a no-op on an
        already-settled request, so this stays idempotent with the client's optimistic
        sibling-approve. The call being resolved right now is already discarded from
        the registry, so it is never in ``list_pending`` here.
        """
        if not tool_names:
            return
        swept: list[dict[str, str]] = []
        for req in self.registry.list_pending(self.conversation_id):
            if req.kind is not InteractionKind.APPROVAL:
                continue
            if req.payload.get("tool_name") not in tool_names:
                continue
            # Never sweep always-confirm calls (git push/create_pr ·
            # host install_package · delete_folder).
            pending_args = req.payload.get("arguments")
            pending_tool = str(req.payload.get("tool_name") or "")
            if _requires_always_confirm(
                pending_tool, pending_args if isinstance(pending_args, dict) else {}
            ):
                continue
            swept.append(
                {
                    "approval_id": req.id,
                    "tool_call_id": str(req.payload.get("tool_call_id") or ""),
                    "tool_name": str(req.payload.get("tool_name") or ""),
                }
            )
            self.registry.resolve(
                req.id, ApprovalDecision.APPROVE, conversation_id=self.conversation_id
            )
        if swept:
            from agentcore.runtime.audit.hooks import on_approval_swept

            on_approval_swept(tool_names=sorted(tool_names), swept=swept)
