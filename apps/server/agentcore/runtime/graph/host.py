"""Host surface the graph runtime reads. The tool class stays outside this package."""

from __future__ import annotations

from typing import Any, Protocol


class GraphHost(Protocol):
    """Attributes and methods drive, admit, and facing actually touch.

    Heavy types stay ``Any`` so importing this module cannot load the engine.
    """

    _llm: Any
    _tools: Any
    _captain_run_id: str
    _depth: int
    _base_tool_context: Any
    _sink: Any
    _session_saver: Any
    _session_store: Any
    _acc: Any
    _children: list[Any]
    _calls: int
    _ceo_synthesis_emitted: bool
    _last_graph_execution_id: str | None
    _last_graph_plan: Any
    _last_graph_seed: Any
    _last_drive_results: Any
    _pending_pause: Any
    _pending_boundary: Any
    _supervised: Any
    _approval_gate: Any
    _profile_set: Any
    _system_prompt: str
    _user_message: str
    _registry: Any
    _checkpoint_timeout_seconds: float
    _team_brief: Any
    _auto_grant_pending: Any
    _max_parallel: int
    _checkpoint_enabled: bool
    _conversation_id: str | None
    _local_root_claims: Any
    _user_message_origin: str
    _folder_id: str | None
    _prev_graph_execution_id: str | None
    _permission_axes: Any
    _worker_envelope: str

    def effective_default_target_folder_id(self) -> str | None: ...

    def spawn_lead_subteam(self, captain_run_id: str, captain_depth: int) -> GraphHost: ...

    def note_continuation(self, run_id: str, *, by_user: bool = False) -> None: ...
