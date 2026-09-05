"""Browser Session API schemas — multi ``session_id`` + input injection.

Co-owned with the desktop BrowserPanel: list/create/close sessions; input is a batch of
frame-pixel-space events. NO frame / key / text content is ever persisted or echoed
back (D17).
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, WithJsonSchema

# DOM MouseEvent.button (0|1|2) and Playwright/CDP names both arrive on the wire;
# we always store/emit the CDP names so injection never sees a bare digit.
_MOUSE_BUTTON_WIRE: dict[object, Literal["left", "right", "middle"]] = {
    0: "left",
    1: "middle",
    2: "right",
    "0": "left",
    "1": "middle",
    "2": "right",
    "left": "left",
    "middle": "middle",
    "right": "right",
}


def _normalize_mouse_button_wire(v: Any) -> Literal["left", "right", "middle"] | None:
    if v is None:
        return None
    # bool is an int subclass — reject True/False masquerading as 0/1.
    if isinstance(v, bool):
        raise ValueError("button must be 0|1|2 or left|right|middle")
    if isinstance(v, int):
        mapped = _MOUSE_BUTTON_WIRE.get(v)
        if mapped is None:
            raise ValueError("button must be 0|1|2 or left|right|middle")
        return mapped
    if isinstance(v, str):
        mapped = _MOUSE_BUTTON_WIRE.get(v) or _MOUSE_BUTTON_WIRE.get(v.lower())
        if mapped is None:
            raise ValueError("button must be 0|1|2 or left|right|middle")
        return mapped
    raise ValueError("button must be 0|1|2 or left|right|middle")


# Validation accepts DOM ints + names; JSON Schema advertises both (no OpenAPI drift).
MouseButton = Annotated[
    Literal["left", "right", "middle"] | None,
    BeforeValidator(_normalize_mouse_button_wire),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "string", "enum": ["left", "right", "middle"]},
                {"type": "integer", "enum": [0, 1, 2]},
                {"type": "null"},
            ],
            "title": "Button",
            "description": (
                "DOM MouseEvent.button 0|1|2 (desktop/mobile wire) or "
                "Playwright/CDP left|right|middle; server normalizes to the name form."
            ),
        }
    ),
]


class BrowserSessionView(BaseModel):
    """One live browser session entry (list / create response)."""

    session_id: str
    conversation_id: str
    host_kind: Literal["sandbox", "local"] = "sandbox"
    run_id: str | None = None
    control: Literal["agent", "user"] = "agent"
    created_at: float
    last_used: float
    # L7 最小：最近导航 url/title（可选）。
    url: str | None = None
    title: str | None = None


class BrowserSessionNavPatch(BaseModel):
    """PATCH body for L7 idle / Bridge navigate url·title 回写."""

    url: str | None = None
    title: str | None = None


class BrowserSessionNavigateRequest(BaseModel):
    """POST …/browser/sessions/{id}/navigate — owner address-bar navigate."""

    url: str = Field(..., min_length=1)


class BrowserSessionListResponse(BaseModel):
    data: list[BrowserSessionView]
    active_session_id: str | None = None


class BrowserSessionCreateRequest(BaseModel):
    """Create a new browser session tab for the conversation."""

    host_kind: Literal["sandbox", "local"] = "sandbox"
    activate: bool = True


class MouseInputEvent(BaseModel):
    """A pointer event in frame-pixel space (the driver rescales to the viewport).

    ``button`` accepts DOM integers ``0|1|2`` (desktop/mobile wire) or Playwright
    names ``left|right|middle``; validation normalizes to the name form.
    """

    kind: Literal["mouse"]
    type: Literal["down", "up", "move", "wheel"]
    x: float
    y: float
    button: MouseButton = None
    delta_x: float | None = None
    delta_y: float | None = None
    click_count: int | None = None

class KeyInputEvent(BaseModel):
    """A key event. ``modifiers`` is a CDP bitmask or a list of names (alt/ctrl/meta/shift).

    Use this for non-text keys (Enter / Backspace / arrows / shortcuts); actual typed text
    should ride a ``text`` event so it inserts verbatim (and never lands in any log — D17).
    """

    kind: Literal["key"]
    type: Literal["down", "up"]
    key: str
    code: str | None = None
    modifiers: int | list[str] | None = None


class TextInputEvent(BaseModel):
    """Verbatim text insertion (IME-style). Content is never logged/persisted (D17)."""

    kind: Literal["text"]
    text: str


BrowserInputEvent = Annotated[
    MouseInputEvent | KeyInputEvent | TextInputEvent, Field(discriminator="kind")
]


class BrowserInputRequest(BaseModel):
    """A batch of input events (owner + live session; else 409)."""

    events: list[BrowserInputEvent]
    session_id: str | None = None


class BrowserInputResponse(BaseModel):
    """Result of an input batch: how many events were dispatched (no content echoed)."""

    injected: int
