"""Browser input schema: DOM button 0|1|2 and Playwright names both validate."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentcore.api.schemas.browser import BrowserInputRequest, MouseInputEvent


def test_mouse_button_dom_zero_normalizes_to_left():
    ev = MouseInputEvent.model_validate(
        {"kind": "mouse", "type": "down", "x": 10, "y": 20, "button": 0, "click_count": 1}
    )
    assert ev.button == "left"
    dumped = ev.model_dump(exclude_none=True)
    assert dumped["button"] == "left"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (0, "left"),
        (1, "middle"),
        (2, "right"),
        ("0", "left"),
        ("1", "middle"),
        ("2", "right"),
        ("left", "left"),
        ("middle", "middle"),
        ("right", "right"),
        ("LEFT", "left"),
    ],
)
def test_mouse_button_accepts_dom_and_names(raw: object, expected: str):
    ev = MouseInputEvent.model_validate(
        {"kind": "mouse", "type": "up", "x": 1, "y": 1, "button": raw}
    )
    assert ev.button == expected


def test_browser_input_request_batch_with_dom_button():
    """End-to-end request body (what POST …/browser/input validates) accepts button=0."""
    body = BrowserInputRequest.model_validate(
        {
            "events": [
                {"kind": "mouse", "type": "down", "x": 10, "y": 20, "button": 0, "click_count": 1},
                {"kind": "key", "type": "down", "key": "a"},
            ]
        }
    )
    events = [ev.model_dump(exclude_none=True) for ev in body.events]
    assert events[0]["button"] == "left"


def test_mouse_button_rejects_unknown():
    with pytest.raises(ValidationError):
        MouseInputEvent.model_validate(
            {"kind": "mouse", "type": "down", "x": 1, "y": 1, "button": 3}
        )
    with pytest.raises(ValidationError):
        MouseInputEvent.model_validate(
            {"kind": "mouse", "type": "down", "x": 1, "y": 1, "button": True}
        )
