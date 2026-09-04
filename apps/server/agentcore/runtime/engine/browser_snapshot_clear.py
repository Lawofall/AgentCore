"""回合内 browser 大树投影：折叠旧 snapshot 的 elements / accessibility_tree。

Within one ReAct turn, repeated ``browser(action=snapshot)`` (and any browser result whose
``untrusted_web_content`` carries an elements list or accessibility tree) re-pays those
large trees every round. This module keeps only the most recent ``keep_recent`` results
verbatim and strips the bulky tree fields from older ones — a PURE projection at
request-assembly time (``build_request_window``), like ``tool_clear`` / ``write_args_clear``.

When an older tree is folded, the projection also attaches ``ref_delta: {added, removed}``
comparing that tree's refs to the next newer browser tree in the window — so the model can
see structural change without retaining a second full tree (``keep_recent=1`` stays).

Canonical ``messages`` / Turn Journal keep the full output; resume rebuilds then
re-applies. Prefix-cache safe: the omitted stub is a pure function of the original
JSON plus the next tree's refs (stable ``sort_keys`` dump); once a result falls out of
the keep-window its bytes stay fixed across rounds because its successor tree is fixed.
"""

from __future__ import annotations

import json
import re

from agentcore.llm.provider.protocol import LLMMessage, llm_content_text
from agentcore.runtime.browser.call_identity import is_browser_tool_name

_TREE_KEYS = ("elements", "accessibility_tree")
_REF_RE = re.compile(r"\[(e\d+)\]")
# Hard cap so folded stubs cannot balloon the context with huge ref churn.
_REF_DELTA_MAX = 80


def _call_info_map(messages: list[LLMMessage]) -> dict[str, tuple[str, str]]:
    call_info: dict[str, tuple[str, str]] = {}
    for message in messages:
        if message.role == "assistant" and message.tool_calls:
            for call in message.tool_calls:
                call_info[call.id] = (call.function.name, call.function.arguments or "")
    return call_info


def _parse_payload(content: str | None) -> dict | None:
    if not content:
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def extract_element_refs(elements: str | None) -> list[str]:
    """Ordered unique refs from an elements table string (``[e1] …`` lines)."""
    if not elements:
        return []
    seen: list[str] = []
    found: set[str] = set()
    for match in _REF_RE.finditer(elements):
        ref = match.group(1)
        if ref not in found:
            found.add(ref)
            seen.append(ref)
    return seen


def compute_ref_delta(
    before_elements: str | None,
    after_elements: str | None,
    *,
    max_refs: int = _REF_DELTA_MAX,
) -> dict[str, object]:
    """Diff ref sets; lists are capped (order preserved from appearance)."""
    before = extract_element_refs(before_elements)
    after = extract_element_refs(after_elements)
    before_set = set(before)
    after_set = set(after)
    added = [ref for ref in after if ref not in before_set]
    removed = [ref for ref in before if ref not in after_set]
    truncated = len(added) > max_refs or len(removed) > max_refs
    delta: dict[str, object] = {
        "added": added[:max_refs],
        "removed": removed[:max_refs],
    }
    if truncated:
        delta["truncated"] = True
    return delta


def has_browser_tree_fields(content: str | None) -> bool:
    """True when tool output JSON carries elements and/or accessibility_tree."""
    data = _parse_payload(content)
    if data is None:
        return False
    uw = data.get("untrusted_web_content")
    if not isinstance(uw, dict):
        return False
    return any(key in uw for key in _TREE_KEYS)


def _elements_from_content(content: str | None) -> str | None:
    data = _parse_payload(content)
    if data is None:
        return None
    uw = data.get("untrusted_web_content")
    if not isinstance(uw, dict):
        return None
    elements = uw.get("elements")
    return elements if isinstance(elements, str) else None


def omit_browser_tree_fields(
    content: str,
    *,
    ref_delta: dict[str, object] | None = None,
) -> str:
    """Strip tree fields and mark ``omitted: true``; stable for the same original.

    Preserves small payload fields (action / final_url / snapshot_version / keyframe / …)
    and non-tree ``untrusted_web_content`` keys (source_url / title / visible_text / …).
    When provided, attaches top-level ``ref_delta`` (projection artifact).
    """
    data = _parse_payload(content)
    if data is None:
        return content
    uw = data.get("untrusted_web_content")
    if isinstance(uw, dict):
        for key in _TREE_KEYS:
            uw.pop(key, None)
        uw["omitted"] = True
    if ref_delta is not None:
        data["ref_delta"] = ref_delta
    # sort_keys → byte-stable across rounds for the same original payload + delta.
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def project_omitted_browser_snapshots(
    messages: list[LLMMessage],
    *,
    keep_recent: int = 1,
) -> list[LLMMessage]:
    """Keep the newest ``keep_recent`` browser tree results; omit trees on older ones.

    Candidates: browser tool results whose output JSON contains ``elements`` or
    ``accessibility_tree`` under ``untrusted_web_content`` (typically snapshot).
    Dual-recognizes live ``browser`` and pre-merge ``browser_*`` names.

    Each omitted stub gets ``ref_delta`` vs the chronologically next tree result
    (added/removed refs), so the model can compare without a second full tree.

    Returns the same list object when nothing qualifies. Idempotent: already-omitted
    stubs lack tree keys and are never re-selected.
    """
    if keep_recent < 0:
        return messages

    call_info = _call_info_map(messages)
    tree_indices: list[int] = []
    for index, message in enumerate(messages):
        if message.role != "tool" or message.tool_call_id is None:
            continue
        info = call_info.get(message.tool_call_id)
        if info is None:
            continue
        name, _arguments = info
        if not is_browser_tool_name(name):
            continue
        if not has_browser_tree_fields(llm_content_text(message.content)):
            continue
        tree_indices.append(index)

    if len(tree_indices) <= keep_recent:
        return messages

    to_omit = set(tree_indices[: len(tree_indices) - keep_recent])
    # Precompute elements for delta: each omitted tree vs the next tree in order.
    elements_by_index = {
        idx: _elements_from_content(llm_content_text(messages[idx].content)) for idx in tree_indices
    }
    projected: list[LLMMessage] = []
    for index, message in enumerate(messages):
        if index in to_omit:
            # Next newer tree in the chronological candidate list.
            pos = tree_indices.index(index)
            next_idx = tree_indices[pos + 1] if pos + 1 < len(tree_indices) else None
            next_elements = elements_by_index.get(next_idx) if next_idx is not None else None
            delta = compute_ref_delta(elements_by_index.get(index), next_elements)
            projected.append(
                LLMMessage(
                    role="tool",
                    content=omit_browser_tree_fields(
                        llm_content_text(message.content),
                        ref_delta=delta,
                    ),
                    tool_call_id=message.tool_call_id,
                )
            )
        else:
            projected.append(message)
    return projected
