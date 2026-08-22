"""Cross-surface DebateForm member-set ratchet.

``DebateForm`` is the single authority for member values. Derived surfaces must
stay identical:

- tool schema ``form`` enum (``DEBATE_PARAMETERS`` ← ``DEBATE_FORM_VALUES``)
- wire fields annotated as ``DebateForm`` (not a hand-copied ``Literal[...]``)
- ``FORM_LABELS`` keys (import-time completeness + this snapshot)

Optional: desktop ``FORM_LABEL`` Record keys (same member set as ``DebateForm``).
Adding a form without updating every surface fails this test — not a behavior vector.
"""

from __future__ import annotations

import re
import types
from enum import StrEnum
from pathlib import Path
from typing import Literal, Union, get_args, get_origin

from agentcore.runtime.debate.constants import DEBATE_FORM_VALUES, FORM_LABELS
from agentcore.runtime.debate.types import DebateForm
from agentcore.runtime.events.payloads.debate import DebateResultPayload
from agentcore.runtime.events.payloads.interaction import StageCardRequiredPayload
from agentcore.runtime.events.payloads.shared import MotionCard
from agentcore.tools.builtin.debate.schema import DEBATE_PARAMETERS

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DESKTOP_FORM_LABEL_FILES = (
    _REPO_ROOT
    / "apps"
    / "desktop"
    / "src"
    / "renderer"
    / "components"
    / "chat"
    / "debate"
    / "debateEntryCopy.ts",
    _REPO_ROOT
    / "apps"
    / "desktop"
    / "src"
    / "renderer"
    / "components"
    / "chat"
    / "StageCard.tsx",
    _REPO_ROOT
    / "apps"
    / "desktop"
    / "src"
    / "renderer"
    / "components"
    / "chat"
    / "detail"
    / "sections"
    / "RunDebrief.tsx",
)


def _member_strings(annotation: object) -> frozenset[str]:
    """Extract string members from ``Literal[...]`` or a ``StrEnum`` field annotation."""
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return frozenset(m.value for m in annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return frozenset(str(a) for a in get_args(annotation))
    if origin is Union or isinstance(annotation, types.UnionType):
        out: set[str] = set()
        for arg in get_args(annotation):
            if arg is type(None):
                continue
            out |= _member_strings(arg)
        return frozenset(out)
    raise AssertionError(f"expected Literal[...] or StrEnum, got {annotation!r}")


def _wire_form(model: type, field: str = "form") -> frozenset[str]:
    return _member_strings(model.model_fields[field].annotation)


def _assert_wire_uses_debate_form(model: type, field: str = "form") -> None:
    ann = model.model_fields[field].annotation
    if get_origin(ann) in (Union, types.UnionType) or isinstance(ann, types.UnionType):
        non_none = [a for a in get_args(ann) if a is not type(None)]
        assert non_none == [DebateForm], f"{model.__name__}.{field} must be DebateForm | None"
        return
    assert ann is DebateForm, f"{model.__name__}.{field} must be annotated as DebateForm"


def test_debate_form_member_set_aligned_across_surfaces():
    enum_vals = frozenset(m.value for m in DebateForm)
    schema_vals = frozenset(DEBATE_PARAMETERS["properties"]["form"]["enum"])
    label_keys = frozenset(f.value for f in FORM_LABELS)
    derived = frozenset(DEBATE_FORM_VALUES)
    wire_result = _wire_form(DebateResultPayload)
    wire_motion = _wire_form(MotionCard)
    wire_stage = _wire_form(StageCardRequiredPayload)

    assert enum_vals == schema_vals == label_keys == derived == wire_result == wire_motion == wire_stage
    assert set(FORM_LABELS) == set(DebateForm)
    assert list(DEBATE_FORM_VALUES) == [m.value for m in DebateForm]
    assert len(enum_vals) >= 3  # ratchet: never silently empty

    _assert_wire_uses_debate_form(DebateResultPayload)
    _assert_wire_uses_debate_form(MotionCard)
    _assert_wire_uses_debate_form(StageCardRequiredPayload)


def test_desktop_form_label_keys_cover_debate_form():
    """Desktop FORM_LABEL maps must cover DebateForm (text may differ)."""
    enum_vals = frozenset(m.value for m in DebateForm)
    for path in _DESKTOP_FORM_LABEL_FILES:
        src = path.read_text(encoding="utf-8")
        block = re.search(
            r"const FORM_LABEL:\s*Record<[^>]+>\s*=\s*\{([^}]+)\}",
            src,
        )
        assert block is not None, f"FORM_LABEL map not found in {path.name}"
        keys = frozenset(re.findall(r"^\s*([a-z_]+)\s*:", block.group(1), flags=re.M))
        assert keys == enum_vals, f"{path.name} FORM_LABEL keys {sorted(keys)} != {sorted(enum_vals)}"
