"""Start the cloud workspace desk before assembling execution tools.

``run`` / short exec only talk to an already-running guest. Boot lives here
(prepare / resume) and on browser / long-running attach — never inside
``sandbox.execute``.
"""

from __future__ import annotations

from agentcore.core.logging import get_logger
from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)


async def provision_server_desk(backend: WorkspaceBackend) -> None:
    """Ensure the cloud desk is up. Never raises into the turn."""
    if getattr(backend, "location", None) != "server":
        return
    ensure = getattr(backend, "ensure_workspace_desk", None)
    if not callable(ensure):
        return
    try:
        await ensure()
    except Exception as exc:  # noqa: BLE001 — missing desk withholds run, must not abort the turn
        logger.warning(
            "sandbox.desk_provision_failed",
            error=str(exc)[:200],
        )
