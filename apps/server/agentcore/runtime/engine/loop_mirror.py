"""Captain live-loop mirror for G4 turn_paused capture.

Published only while ``react_loop(..., role="captain")`` is running. Public
import path stays ``engine.loop`` (``CaptainLoopMirror``, ``current_captain_loop``,
``sync_captain_loop_mirror``).
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass

from agentcore.runtime.loop_controller import LoopController


@dataclass
class CaptainLoopMirror:
    """Live captain-loop mirror for suspension capture (G4 turn_paused).

    Published only while ``react_loop(..., role="captain")`` is running. Holds a
    reference to the run's :class:`LoopController` plus the two content
    accumulators a suspending face needs (ask_user folded → ``content_before_round``;
    ask_user with its own ``message`` / delegate / team_preview / plan_review →
    ``final_content``). ``ask_user_content_folded`` is set by the tool-round
    prepare so pause capture matches the absorb decision.
    """

    controller: LoopController
    content_before_round: str = ""
    final_content: str = ""
    ask_user_content_folded: bool = False


current_captain_loop: ContextVar[CaptainLoopMirror | None] = ContextVar(
    "current_captain_loop", default=None
)


def sync_captain_loop_mirror(
    *,
    content_before_round: str | None = None,
    final_content: str | None = None,
    ask_user_content_folded: bool | None = None,
) -> None:
    """Update the published captain mirror in place (no-op when unset / non-captain)."""
    mirror = current_captain_loop.get()
    if mirror is None:
        return
    if content_before_round is not None:
        mirror.content_before_round = content_before_round
    if final_content is not None:
        mirror.final_content = final_content
    if ask_user_content_folded is not None:
        mirror.ask_user_content_folded = ask_user_content_folded
