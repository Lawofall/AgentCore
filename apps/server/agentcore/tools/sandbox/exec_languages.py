"""Probe + resolve which ``code_execute`` languages to advertise this turn.

Local / sidecar: probe launchers (same truth as SubprocessSandbox /
desktop ``execCodec``) and trim the tool schema. Cloud ``location=server``:
fixed full surface (gVisor image honesty) — do not pretend host PATH applies.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal

from agentcore.core.logging import get_logger

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

ExecLanguage = Literal["python", "javascript", "bash"]

ALL_EXEC_LANGUAGES: tuple[ExecLanguage, ...] = ("python", "javascript", "bash")

_LANG_LABELS: dict[str, str] = {
    "python": "Python",
    "javascript": "JavaScript",
    "bash": "Bash",
}


def language_labels(languages: Sequence[str]) -> str:
    """Human-readable join for descriptions / workspace_context (e.g. ``Python、Bash``)."""
    parts = [_LANG_LABELS.get(lang, lang) for lang in languages]
    return "、".join(parts) if parts else "无"


def format_interpreters_line(languages: Sequence[str]) -> str:
    """One-line fact for ``<工作区>`` about probed interpreters."""
    available = [lang for lang in ALL_EXEC_LANGUAGES if lang in languages]
    missing = [lang for lang in ALL_EXEC_LANGUAGES if lang not in languages]
    if not missing:
        return f"可用解释器：{language_labels(available)}。"
    if not available:
        return f"可用解释器：无（不可用：{language_labels(missing)}）。"
    return (
        f"可用解释器：{language_labels(available)}"
        f"（不可用：{language_labels(missing)}）。"
    )


def probe_host_languages() -> tuple[ExecLanguage, ...]:
    """Sync PATH probe on this host (sidecar / SubprocessSandbox)."""
    from agentcore.tools.sandbox.subprocess import probe_available_languages

    probed = set(probe_available_languages())
    return tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in probed)


async def resolve_exec_languages(
    backend: WorkspaceBackend | None,
) -> tuple[ExecLanguage, ...]:
    """Languages to put on ``code_execute`` schema + workspace_context this turn.

    Caches the result on ``backend._exec_languages`` so registry + context share
    one probe. Cloud backends keep the fixed full surface.
    """
    if backend is None or backend.location != "local":
        return ALL_EXEC_LANGUAGES

    cached = getattr(backend, "_exec_languages", None)
    if cached is not None:
        return tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in cached)

    channel = getattr(backend, "_channel", None)
    if channel is not None:
        langs = await _probe_via_desktop(channel)
    else:
        langs = probe_host_languages()

    # Protocol has no typed cache slot; concrete backends accept a dynamic attr.
    object.__setattr__(backend, "_exec_languages", langs)
    return langs


async def _probe_via_desktop(channel: object) -> tuple[ExecLanguage, ...]:
    """Ask the bound desktop to probe launchers on the user's machine."""
    from agentcore.workspace.channel import WorkspaceOp

    request = getattr(channel, "request", None)
    if request is None:
        return ()
    try:
        value = await request(WorkspaceOp.PROBE_EXEC, {}, timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — fail closed on advertise surface
        # Prepare-phase budget: first liveness hang aborts the turn (do not
        # fail-closed advertise and continue into exists/baseline burns).
        from agentcore.runtime.pipeline.errors import (
            prepare_local_io_budget_active,
            reraise_prepare_liveness_timeout,
        )

        if prepare_local_io_budget_active():
            reraise_prepare_liveness_timeout(exc)
        logger.warning(
            "workspace.exec_languages_probe_failed",
            error=str(exc)[:200],
        )
        return ()
    raw = value.get("languages") if isinstance(value, dict) else None
    if not isinstance(raw, list):
        return ()
    allowed = set(ALL_EXEC_LANGUAGES)
    return tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in raw and lang in allowed)
