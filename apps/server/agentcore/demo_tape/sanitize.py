"""Ingest-path sanitization for demo tapes / conformance cut fixtures (offline only).

Two defenses shared by tape export and recording_cut:

1. **Sanitize** — strip real long-term user memory from ``run_context`` system-channel
   bodies (the ``<rules>`` block), replacing it with a synthetic placeholder that keeps
   the block structure so the frontend「收到的上下文」dialog still renders cleanly.
2. **Scan** — pattern-scan the finished artifact; hit ⇒ refuse commit (catches sanitize
   misses plus emails / phone shapes).

Pure offline read→write; never touches runtime semantics.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from agentcore.demo_tape.schema import event_type

# Distinctive demo placeholder — scan allowlists text that only contains this marker
# after the memory preamble (never real ``<!-- ts:… -->`` bullets).
DEMO_MEMORY_PLACEHOLDER = "（演示占位 · 非真实用户记忆）"

# Keep the real preamble so the UI still reads as a system「常驻约定」block; only the
# body bullets are synthetic. Shape mirrors ``runtime/resolve/prompt._RULES_TEMPLATE``.
SYNTHETIC_MEMORY_RULES = (
    "<rules>\n"
    "以下条目请一并遵循。\n"
    "硬约束：题材/领域偏好与历史任务不得改变本回合路由"
    "（直答/委派/调研/辩论以用户当前话为准）。\n"
    "\n"
    "## 沟通偏好\n"
    f"- {DEMO_MEMORY_PLACEHOLDER}\n"
    "\n"
    "## 关于用户的事实\n"
    f"- {DEMO_MEMORY_PLACEHOLDER}\n"
    "</rules>"
)

# Real injection preamble (memory_rules.py); used to locate the always-on <rules> block.
_MEMORY_PREAMBLE = "以下条目请一并遵循"

_RULES_BLOCK_RE = re.compile(
    r"<rules>\s*" + re.escape(_MEMORY_PREAMBLE) + r".*?</rules>",
    re.DOTALL,
)

# Second-defense residue patterns (after synthetic blocks are stripped for matching).
# Email / CN-mobile shapes are scoped to ``run_context`` system bodies only — tool
# results and debate content routinely cite public web contacts / URL id digits.
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<![0-9A-Za-z/=_&])(?:\+?86[-\s]?)?1[3-9]\d{9}(?![0-9A-Za-z])"
)
_MEMORY_TS_RE = re.compile(r"<!--\s*ts:\d{4}-\d{2}-\d{2}\s*-->")
_MEMORY_SECTION_MARKERS = (
    "## 沟通偏好",
    "## 工作习惯",
    "## 关于用户的事实",
    "## 技术栈与工具",
    "## 纠正记录",
    "## 项目约束",
    _MEMORY_PREAMBLE,
)


class IngestScanError(ValueError):
    """Ingest artifact failed the second-defense pattern scan."""

    def __init__(self, hits: list[str]) -> None:
        self.hits = hits
        super().__init__(
            "ingest scan refused — residual sensitive patterns:\n  - " + "\n  - ".join(hits)
        )


def sanitize_memory_in_text(text: str) -> str:
    """Replace long-term-memory ``<rules>`` blocks with the synthetic placeholder.

    Non-memory ``<rules>`` (if any) and all other prompt sections are left intact.
    """
    if not text or _MEMORY_PREAMBLE not in text:
        return text
    return _RULES_BLOCK_RE.sub(SYNTHETIC_MEMORY_RULES, text)


def _sanitize_run_context_payload(payload: dict[str, Any]) -> dict[str, Any]:
    blocks = payload.get("blocks")
    if not isinstance(blocks, list):
        return payload
    out_blocks: list[Any] = []
    changed = False
    for block in blocks:
        if not isinstance(block, dict):
            out_blocks.append(block)
            continue
        if block.get("channel") != "system":
            out_blocks.append(block)
            continue
        body = block.get("body")
        if not isinstance(body, str):
            out_blocks.append(block)
            continue
        new_body = sanitize_memory_in_text(body)
        if new_body is body:
            out_blocks.append(block)
            continue
        changed = True
        out_blocks.append({**block, "body": new_body})
    if not changed:
        return payload
    return {**payload, "blocks": out_blocks}


def sanitize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a new event list with ``run_context`` system bodies sanitized.

    Deep-copies only payloads that change so callers can pass shared fixtures safely.
    """
    out: list[dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict) or event_type(ev) != "run_context":
            out.append(ev)
            continue
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            out.append(ev)
            continue
        new_payload = _sanitize_run_context_payload(payload)
        if new_payload is payload:
            out.append(ev)
            continue
        out.append({**ev, "payload": copy.deepcopy(new_payload)})
    return out


def sanitize_event_document(document: dict[str, Any]) -> dict[str, Any]:
    """Sanitize ``events`` on a tape / fixture document (shallow copy of the envelope)."""
    events = document.get("events")
    if not isinstance(events, list):
        return document
    return {**document, "events": sanitize_events(events)}


def _strip_synthetic_for_scan(text: str) -> str:
    """Remove allowlisted synthetic memory so residue checks only see leftovers."""
    return text.replace(SYNTHETIC_MEMORY_RULES, "").replace(DEMO_MEMORY_PLACEHOLDER, "")


def scan_text_for_memory_residue(text: str) -> list[str]:
    """Memory-marker hits (empty ⇒ none). Synthetic placeholder blocks are ignored."""
    if not text:
        return []
    probed = _strip_synthetic_for_scan(text)
    hits: list[str] = []
    if _MEMORY_TS_RE.search(probed):
        hits.append("user-memory timestamp marker (<!-- ts:YYYY-MM-DD -->)")
    for marker in _MEMORY_SECTION_MARKERS:
        if marker in probed:
            hits.append(f"user-memory marker {marker!r}")
            break  # one memory-family hit is enough signal
    return hits


def scan_text_for_contact_residue(text: str) -> list[str]:
    """Email / CN-mobile hits inside a system-channel body (empty ⇒ none)."""
    if not text:
        return []
    probed = _strip_synthetic_for_scan(text)
    hits: list[str] = []
    if _EMAIL_RE.search(probed):
        hits.append("email-shaped token in run_context system body")
    if _PHONE_RE.search(probed):
        hits.append("phone-shaped token in run_context system body")
    return hits


def _iter_payload_strings(payload: Any) -> list[str]:
    blobs: list[str] = []
    stack: list[Any] = [payload]
    while stack:
        cur = stack.pop()
        if isinstance(cur, str):
            if cur:
                blobs.append(cur)
        elif isinstance(cur, dict):
            stack.extend(cur.values())
        elif isinstance(cur, list):
            stack.extend(cur)
    return blobs


def scan_events_for_ingest_residue(events: list[Any]) -> list[str]:
    """Second-defense scan; de-dupe hit labels.

    * Memory markers — every string leaf under event payloads (sanitize miss anywhere).
    * Email / phone — only ``run_context`` ``channel=system`` bodies (memory / prompt
      PII surface). Tool results and streamed content often cite public web contacts.
    """
    seen: list[str] = []

    def _add(hits: list[str]) -> None:
        for hit in hits:
            if hit not in seen:
                seen.append(hit)

    for ev in events:
        if not isinstance(ev, dict):
            continue
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        for blob in _iter_payload_strings(payload):
            _add(scan_text_for_memory_residue(blob))
        if event_type(ev) != "run_context":
            continue
        blocks = payload.get("blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get("channel") != "system":
                continue
            body = block.get("body")
            if isinstance(body, str):
                _add(scan_text_for_contact_residue(body))
    return seen


def assert_ingest_clean(events: list[Any]) -> None:
    """Raise :class:`IngestScanError` when the second-defense scan hits."""
    hits = scan_events_for_ingest_residue(events)
    if hits:
        raise IngestScanError(hits)


def sanitize_and_scan_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize then hard-scan — the shared ingest pipeline step."""
    cleaned = sanitize_events(events)
    assert_ingest_clean(cleaned)
    return cleaned
