"""Host port: start this turn / resume this turn.

Queue drain and deferred resume call this port. ``conversation.service``
binds the cloud implementation at import (``stream_chat`` / ``resume_chat``).
Runtime never imports those names. Sidecar FIFO still uses
``set_queue_starter`` and never hits this port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.runtime.checkpoints import CheckpointResponse
    from agentcore.runtime.events import EventSink
    from agentcore.runtime.suspension import TurnSuspension

__all__ = [
    "TurnDriver",
    "bind_turn_driver",
    "get_turn_driver",
    "reset_turn_driver",
]


@runtime_checkable
class TurnDriver(Protocol):
    """Open this conversation turn, or continue a paused one."""

    async def start_turn(
        self,
        *,
        conversation_id: str,
        user_message: str,
        user_id: str,
        sink: EventSink,
        attachments: list[dict[str, Any]] | None = None,
        llm_credentials: LLMCredentials | None = None,
        llm_supports_tools: bool | None = None,
        x_client_platform: str | None = None,
        agent_mentions: list[dict[str, Any]] | None = None,
        existing_user_message_id: str | None = None,
    ) -> None: ...

    async def resume_turn(
        self,
        *,
        suspension: TurnSuspension,
        response: CheckpointResponse,
        sink: EventSink,
        llm_credentials: LLMCredentials | None = None,
        llm_supports_tools: bool | None = None,
        x_client_platform: str | None = None,
    ) -> None: ...


_driver: TurnDriver | None = None


def bind_turn_driver(driver: TurnDriver) -> None:
    """Composition root installs the host implementation (once per process)."""
    global _driver
    _driver = driver


def reset_turn_driver() -> None:
    """Tests / sidecar shutdown: drop the bound implementation."""
    global _driver
    _driver = None


def get_turn_driver() -> TurnDriver:
    """The bound driver. Raises if ``conversation.service`` was never imported."""
    if _driver is None:
        raise RuntimeError(
            "TurnDriver is not bound; import agentcore.conversation.service first"
        )
    return _driver
