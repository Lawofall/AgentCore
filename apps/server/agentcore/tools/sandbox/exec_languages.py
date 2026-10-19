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
    """Short ``解释器：`` line when the probed set is not the full surface.

    Full set (cloud guest / complete local PATH) is omitted — the opening
    ``run`` schema is the channel. Incomplete local probes are the unusual fact.
    """
    available = [lang for lang in ALL_EXEC_LANGUAGES if lang in languages]
    missing = [lang for lang in ALL_EXEC_LANGUAGES if lang not in languages]
    if not missing:
        return ""
    if not available:
        return "解释器：无"
    return f"解释器：{language_labels(available)}"


def probe_host_languages() -> tuple[ExecLanguage, ...]:
    """Sync PATH probe on this host (sidecar / SubprocessSandbox)."""
    from agentcore.tools.sandbox.subprocess import probe_available_languages

    probed = set(probe_available_languages())
    return tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in probed)


async def resolve_exec_languages(
    backend: WorkspaceBackend | None,
) -> tuple[ExecLanguage, ...]:
    """Languages to put on ``code_execute`` schema + workspace_context this turn.

    Caches languages on ``backend._exec_languages`` and the ``run`` dialect on
    ``backend._run_shell_fact`` so registry + ``<工作区>`` share one probe.
    Cloud backends keep the fixed full language surface (no host shell fact).
    """
    if backend is None or backend.location != "local":
        return ALL_EXEC_LANGUAGES

    cached = getattr(backend, "_exec_languages", None)
    if cached is not None:
        return tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in cached)

    from agentcore.tools.sandbox.run_shell import (
        parse_run_shell_fact,
        probe_host_run_shell,
    )

    channel = getattr(backend, "_channel", None)
    run_shell = None
    if channel is not None:
        langs, payload = await _probe_via_desktop(channel)
        run_shell = parse_run_shell_fact(payload)
    else:
        langs = probe_host_languages()
        run_shell = probe_host_run_shell()

    # Protocol has no typed cache slot; concrete backends accept a dynamic attr.
    object.__setattr__(backend, "_exec_languages", langs)
    object.__setattr__(backend, "_run_shell_fact", run_shell)
    return langs


async def _probe_via_desktop(
    channel: object,
) -> tuple[tuple[ExecLanguage, ...], object]:
    """Ask the bound desktop to probe launchers + ``run`` shell on the user's machine."""
    from agentcore.workspace.channel import WorkspaceOp

    request = getattr(channel, "request", None)
    if request is None:
        return (), None
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
        return (), None
    raw = value.get("languages") if isinstance(value, dict) else None
    if not isinstance(raw, list):
        return (), value
    allowed = set(ALL_EXEC_LANGUAGES)
    langs = tuple(lang for lang in ALL_EXEC_LANGUAGES if lang in raw and lang in allowed)
    return langs, value
