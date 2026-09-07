"""Start the cloud workspace desk before assembling execution tools.

``run`` / short exec only talk to an already-running guest. Boot lives here
(prepare / resume) and on browser / long-running attach — never inside
``sandbox.execute``.
"""

from __future__ import annotations

from typing import Any

from agentcore.core.logging import get_logger
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


def _exc_code(exc: BaseException) -> str:
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        nested = details.get("code")
        if nested:
            return str(nested).strip()[:80]
    raw = getattr(exc, "code", None)
    return str(raw).strip()[:80] if raw else ""


def desk_provision_log_fields(exc: BaseException) -> dict[str, str]:
    """User-facing ``error`` plus the pre-wrap sandboxd/runsc ``code`` / ``cause``."""
    error = " ".join(str(exc).split())[:200]
    origin: BaseException = exc
    for candidate in (exc.__cause__, exc.__context__):
        if candidate is not None:
            origin = candidate
            break
    code = _exc_code(origin)
    cause = ""
    if origin is not exc:
        cause = " ".join(str(origin).split())[:200]
    if not cause:
        from agentcore.tools.sandbox.cloud_health import cloud_sandbox_health_failure

        failure = cloud_sandbox_health_failure()
        if failure:
            reason, detail = failure
            cause = " ".join(part for part in (reason, detail) if part)[:200]
            code = code or str(reason).strip()[:80]
    fields = {"error": error}
    if code:
        fields["code"] = code
    if cause and cause != error:
        fields["cause"] = cause
    return fields


async def provision_server_desk(
    backend: WorkspaceBackend,
    *,
    conversation_id: str | None = None,
    sink: Any | None = None,
) -> None:
    """Ensure the cloud desk is up. Never raises into the turn.

    When ``sink`` is bound, emit ``desk_provision_wait`` so the air bubble
    shows preparing-cloud rather than Thinking… while boot runs.
    """
    if getattr(backend, "location", None) != "server":
        return
    ensure = getattr(backend, "ensure_workspace_desk", None)
    if not callable(ensure):
        return
    waiting = False
    if sink is not None and conversation_id:
        from agentcore.runtime.events import desk_provision_wait

        sink.emit(desk_provision_wait(conversation_id=conversation_id, waiting=True))
        waiting = True
    try:
        await ensure()
    except Exception as exc:  # noqa: BLE001 — missing desk withholds run, must not abort the turn
        logger.warning("sandbox.desk_provision_failed", **desk_provision_log_fields(exc))
    finally:
        if waiting and sink is not None and conversation_id:
            from agentcore.runtime.events import desk_provision_wait

            sink.emit(desk_provision_wait(conversation_id=conversation_id, waiting=False))
