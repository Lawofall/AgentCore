"""Ticketed desktop sidecar must not open local Postgres.

Cloud API processes never bind folders/account ContextVars. Desktop injects
narrow tickets for the turn tree (双模式工作区 · 岔路 B). Opening a session
while either ticket is bound is a contract leak — fail immediately instead of
hanging on ``localhost:5432``.
"""

from __future__ import annotations


def sidecar_narrow_tickets_bound() -> bool:
    """True when this task is a desktop sidecar turn with folders and/or account tickets."""
    from agentcore.account.credentials import get_account_credentials
    from agentcore.folders.credentials import get_folders_credentials

    return get_folders_credentials() is not None or get_account_credentials() is not None


def raise_if_sidecar_local_db_forbidden() -> None:
    if not sidecar_narrow_tickets_bound():
        return
    from agentcore.db.errors import SidecarLocalDbForbiddenError

    raise SidecarLocalDbForbiddenError(
        "ticketed sidecar must not open local Postgres"
    )
