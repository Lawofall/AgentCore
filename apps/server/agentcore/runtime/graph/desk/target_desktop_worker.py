"""Worker prompt rebuild for a chosen desk. The rebuild runs in ``runtime.runs.desk``."""

from agentcore.runtime.runs.desk.worker import (
    rebuild_worker_prompt_for_target,
    registry_rewire_consult_tools,
)

__all__ = [
    "rebuild_worker_prompt_for_target",
    "registry_rewire_consult_tools",
]
