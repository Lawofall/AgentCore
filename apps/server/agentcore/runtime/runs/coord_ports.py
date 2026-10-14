"""Optional coordination callbacks. Session installs them; runs does not import graph."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

active_coordination: Callable[..., Any] | None = None
note_child_cancel_overflow: Callable[..., Any] | None = None
note_coord_worker_busy: Callable[..., Any] | None = None
resolve_coordination_session: Callable[..., Any] | None = None
post_escalation_to_coordination: Callable[..., Any] | None = None
