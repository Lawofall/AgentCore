"""checkpoint_after extra key is ignored: captain loop + delegate DAG runs through.

Leftover plan_review pause is gone. Extra ``checkpoint_after`` on tasks does not
SUSPEND / PAUSED-finish. Both workers run and the captain answers.
"""

import json
from pathlib import Path

from agentcore.llm.provider.protocol import LLMChunk, LLMMessage, ToolCallDelta
from agentcore.runtime.engine import ReactLoopOut, react_loop
from agentcore.runtime.events import EventSink, FinishReason
from agentcore.runtime.facts import TurnFactLog, TurnStartedFact, current_fact_log
from agentcore.runtime.interaction import InteractionRegistry
from agentcore.runtime.suspension import captain_transcript
from agentcore.tools.builtin.graph import GraphTool
from agentcore.tools.protocol import ToolContext
from agentcore.tools.registry import ToolRegistry
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace
from tests.delegate.conftest import _TEST_BIRTH_FOLDER_ID, _upstream_body
from tests.llm_helpers import make_profile_params


class _ScriptedProvider:
    """Yields one pre-scripted chunk list per ``stream`` call (one call per round)."""

    def __init__(self, rounds: list[list[LLMChunk]]) -> None:
        self._rounds = rounds
        self.calls = 0

    async def stream(self, request):  # noqa: ANN001 - duck-typed for the loop
        chunks = self._rounds[self.calls] if self.calls < len(self._rounds) else []
        self.calls += 1
        for chunk in chunks:
            yield chunk


def _context() -> ToolContext:
    return ToolContext.create(
        execution_id="e",
        run_id="cap",
        agent_id="a",
        backend=ServerWorkspace(root=Path("."), sandbox=SubprocessSandbox()),
        user_id="u",
    )
