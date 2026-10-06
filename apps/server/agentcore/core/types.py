"""Shared enumerations and base types used across all modules."""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


def new_id() -> str:
    return str(uuid4())


def is_uuid_id(value: str | None) -> bool:
    """True when ``value`` is safe to bind as a PG UUID ``messages.id``."""
    if not value:
        return False
    try:
        UUID(str(value))
    except ValueError:
        return False
    return True


# --- Core Enumerations ---


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolApproval(StrEnum):
    """Tool approval requirement levels (可逆性 × 副作用).

    Two live levels: ``NEVER`` (silent) and ``GRANTABLE`` (may still prompt for
    irreversible shapes). Boundary decides whether the tool is assembled;
    in-boundary writes and ``run`` do not prompt. Irreversible external
    actions stay on the always-confirm / breaker path.
    """

    NEVER = "never"
    GRANTABLE = "grantable"


class WorkspaceBoundary(StrEnum):
    """What this conversation may do. One value, not three axes.

    ``read`` — look only. ``folder`` — change this folder and run inside it
    (default). ``computer`` — folder plus the local Host face. Stored as
    ``permission_axes.boundary``. Checkpoints, breakers, and always-confirm
    commands are orthogonal.
    """

    READ = "read"
    FOLDER = "folder"
    COMPUTER = "computer"

    @property
    def allows_write(self) -> bool:
        return self is not WorkspaceBoundary.READ

    @property
    def allows_execution(self) -> bool:
        return self is not WorkspaceBoundary.READ

    @property
    def allows_host(self) -> bool:
        return self is WorkspaceBoundary.COMPUTER

    def to_dict(self) -> dict[str, str]:
        return {"boundary": self.value}

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> WorkspaceBoundary:
        """Parse stored / wire JSON. Missing or unknown ``boundary`` raises."""
        if not isinstance(raw, Mapping) or "boundary" not in raw:
            raise ValueError("boundary required")
        return cls(str(raw["boundary"]))


DEFAULT_PERMISSION_AXES = WorkspaceBoundary.FOLDER


class ToolFace(StrEnum):
    """Capability face for the shared tool catalog (human 图鉴 + AI 按需目录).

    Grouping only — the engine acts on ToolResult / explicit name sets, not on face.
    Display face ≠ registration surface: ``ceo_orchestration`` is how CEO tools
    are wired, not a dumpster for this enum.
    """

    FILE = "file"
    FOLDER = "folder"
    SEARCH = "search"
    WEB = "web"
    EXECUTION = "execution"
    HOST_BROWSER = "host_browser"
    ORCHESTRATION = "orchestration"


TOOL_FACE_LABELS: dict[ToolFace, str] = {
    ToolFace.FILE: "文件",
    ToolFace.FOLDER: "文件夹",
    ToolFace.SEARCH: "检索",
    ToolFace.WEB: "网络",
    ToolFace.EXECUTION: "执行",
    ToolFace.HOST_BROWSER: "本机 · 浏览器",
    ToolFace.ORCHESTRATION: "编排",
}

TOOL_FACE_ORDER: tuple[ToolFace, ...] = (
    ToolFace.FILE,
    ToolFace.FOLDER,
    ToolFace.SEARCH,
    ToolFace.WEB,
    ToolFace.EXECUTION,
    ToolFace.HOST_BROWSER,
    ToolFace.ORCHESTRATION,
)


class ToolEffect(StrEnum):
    """How a tool result steers the ReAct loop.

    The engine acts on THIS effect — never on a tool's name or category (引擎纯化,
    设计 §8.5). The default ``CONTINUE`` feeds the tool output back and loops; a
    terminal effect ends the turn in-band, surfacing the result's ``final_text``
    instead of letting the model generate a second, duplicate reply.
    """

    # Default: feed the tool output back to the model and keep looping.
    CONTINUE = "continue"
    # The tool already produced AND streamed the turn's final user-facing answer
    # itself, so the loop must stop. Reserved: no current built-in sets it (the
    # legacy answer-streaming handoff was retired) — kept as the effect a future
    # streaming-handoff tool would declare.
    HANDOFF = "handoff"
    # The tool drove a user interaction that ended the turn, and its text is the
    # final answer: the CEO ``ask_user`` checkpoint on a "stop" decision (its closing
    # note is the reply). A "submit" answer instead resumes the loop (CONTINUE), so
    # only stop is terminal here.
    INTERACT = "interact"
    # 挂起即收口 (②): the tool hit a durable checkpoint and persisted a resume frame, so
    # the loop must END the turn awaiting ``POST .../resume`` — NOT because an answer was
    # produced. Unlike INTERACT/HANDOFF it carries NO ``final_text`` (there is no reply
    # yet) and the suspended tool_call is left PENDING (no tool result recorded), so the
    # resumed window ends exactly at the assistant. The engine maps it to
    # FinishReason.PAUSED. Returned by any durable checkpoint whose frame was persisted
    # (D11: un-persistable runtime failure terminates the turn — no in-memory wait).
    SUSPEND = "suspend"
