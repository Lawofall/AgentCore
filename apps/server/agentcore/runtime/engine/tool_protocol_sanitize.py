"""Strip vendor tool-call protocol tags leaking into tool names / args / prose.

Some OpenAI-compatible providers (e.g. LongCat) occasionally emit residual XML-like
markers such as ``</longcat_arg_key>`` or ``<longcat_tool_call>`` inside tool names,
argument strings, or handoff summary text. Clean at the tool-exec / harvest seam so
illegal names become retryable and briefs stay readable — no provider-specific adapter.

DeepSeek-family models may also paste DSML tool-call markup
(``<｜DSML｜tool_calls>`` / ``invoke`` / ``parameter``) into ``delta.content``; that
must be stripped from assistant deliverable prose the same way — keep surrounding
natural language, never reject the whole bubble on sight of a marker.

Also used **before** ``json.loads`` on raw tool-call arguments: models sometimes mix
Anthropic-style ``<parameter>`` / ``<object>`` fragments into OpenAI JSON args, which
would otherwise hard-fail as ``args_parse_failed``.

After a successful parse, :func:`unwrap_nested_delegate_arguments` eats one
known protocol fumble: double-wrapping the payload as ``{"arguments"|"parameters"|"input":
"<json>"}`` (wire field name collision / mistaken nesting) — same family as
``coerce_list_arg`` / hoist, not generic JSON repair.

:func:`parse_tool_call_arguments` is the unique post-sanitize parse: ``raw_decode``
accepts a complete value plus trailing junk (lossless — only junk is dropped), and
never closes a truncated value (lossy — see its docstring). No per-tool salvage,
no generic JSON-repair black box. ``handoff`` takes no arguments; a wrap-up in
JSON is not a parse to repair.
"""

from __future__ import annotations

import json
import re
from typing import Any

from agentcore.core.assistant_content import (
    _PROTOCOL_TAG_RE,
    ASSISTANT_CONTENT_MAX_CHARS,
    ASSISTANT_CONTENT_OVERSIZE_FACE,
    prepare_assistant_content,
    sanitize_protocol_text,
    truncate_at_dsml_open,
)

__all__ = [
    "ASSISTANT_CONTENT_MAX_CHARS",
    "ASSISTANT_CONTENT_OVERSIZE_FACE",
    "parse_tool_call_arguments",
    "prepare_assistant_content",
    "sanitize_protocol_text",
    "sanitize_raw_tool_arguments",
    "sanitize_tool_args",
    "sanitize_tool_name",
    "truncate_at_dsml_open",
    "unwrap_nested_delegate_arguments",
]

# Delegate payload carriers — used to decide whether a nested wrapper is the
# sole top-level payload key (narrow unwrap; do not guess other fields).
_DELEGATE_WRAPPER_KEYS = frozenset({"arguments", "parameters", "input"})
_DELEGATE_PAYLOAD_KEYS = frozenset({"tasks", "playbook"}) | _DELEGATE_WRAPPER_KEYS

# Hybrid leak: ``<parameter name="role":`` (XML open tag broken into JSON key colon).
_PARAMETER_NAME_COLON_RE = re.compile(
    r'<parameter\s+name="([^"]+)"\s*:',
    re.IGNORECASE,
)
# After tag strip, top-level keys sometimes keep ``"tasks">`` instead of ``"tasks":``.
_JSON_KEY_GT_RE = re.compile(r'("(?:tasks|arguments|parameters|query|path|content)")\s*>')
# Residual angle-bracket junk stuck to identifiers (defense in depth after tag strip).
_STRAY_ANGLE_RE = re.compile(r"[<>]")


def sanitize_raw_tool_arguments(raw: str) -> str:
    """Clean protocol residue from raw tool-call argument JSON **before** parse.

    Narrow structural repairs only (tag strip + known hybrid key shapes). Does not
    invent missing fields or truncate bodies — if still invalid, ``json.loads`` fails
    honestly as before.
    """
    if not raw:
        return raw
    cleaned = _PARAMETER_NAME_COLON_RE.sub(r'"\1":', raw)
    cleaned = _PROTOCOL_TAG_RE.sub("", cleaned)
    cleaned = _JSON_KEY_GT_RE.sub(r"\1:", cleaned)
    if cleaned != raw:
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned


def sanitize_tool_name(name: str) -> str:
    """Normalize a tool name that may carry protocol-tag residue.

    ``web_query</longcat_arg_key>`` → ``web_query``. Empty after clean → ``""``.
    """
    raw = (name or "").strip()
    if not raw:
        return ""
    cleaned = _PROTOCOL_TAG_RE.sub("", raw)
    cleaned = _STRAY_ANGLE_RE.sub("", cleaned)
    cleaned = cleaned.strip().strip("\"'`")
    # Tool names are identifiers — keep only the leading token if junk trailed.
    if cleaned and not cleaned.replace("_", "").isalnum():
        m = re.match(r"^[A-Za-z_][\w]*", cleaned)
        if m:
            cleaned = m.group(0)
    return cleaned


def sanitize_tool_args(args: Any) -> Any:
    """Recursively sanitize string leaves in parsed tool arguments."""
    if isinstance(args, str):
        return sanitize_protocol_text(args)
    if isinstance(args, list):
        return [sanitize_tool_args(x) for x in args]
    if isinstance(args, dict):
        return {k: sanitize_tool_args(v) for k, v in args.items()}
    return args


def _delegate_payload_keys_present(args: dict[str, Any]) -> set[str]:
    """Which of ``_DELEGATE_PAYLOAD_KEYS`` are meaningfully present at this level."""
    present: set[str] = set()
    tasks = args.get("tasks")
    if isinstance(tasks, list) and bool(tasks):
        present.add("tasks")
    playbook = args.get("playbook")
    if isinstance(playbook, str) and playbook.strip():
        present.add("playbook")
    for key in _DELEGATE_WRAPPER_KEYS:
        if key not in args:
            continue
        raw = args.get(key)
        if (isinstance(raw, str) and raw.strip()) or (isinstance(raw, dict) and raw):
            present.add(key)
    return present


def _inner_has_delegate_payload(inner: dict[str, Any]) -> bool:
    tasks = inner.get("tasks")
    if isinstance(tasks, list) and bool(tasks):
        return True
    playbook = inner.get("playbook")
    return isinstance(playbook, str) and bool(playbook.strip())


def unwrap_nested_delegate_arguments(args: Any) -> dict[str, Any] | None:
    """Narrow unwrap of double-wrapped delegate payload.

    Only when the top-level dict's sole meaningful payload key (among
    ``tasks`` / ``playbook`` / ``arguments`` / ``parameters`` /
    ``input``) is exactly one wrapper among ``arguments`` / ``parameters`` /
    ``input``, and that value is a JSON object string or dict whose inner body
    carries non-empty ``tasks`` or a named ``playbook``.
    Returns the inner dict to use as replacement, or ``None`` when the shape
    does not match (including real top-level ``tasks`` plus an unrelated wrapper).
    """
    if not isinstance(args, dict):
        return None
    present = _delegate_payload_keys_present(args)
    if present not in ({"arguments"}, {"parameters"}, {"input"}):
        return None
    wrapper_key = next(iter(present))
    raw = args.get(wrapper_key)
    if isinstance(raw, str):
        try:
            inner: Any = json.loads(raw)
        except json.JSONDecodeError:
            return None
    elif isinstance(raw, dict):
        inner = raw
    else:
        return None
    if not isinstance(inner, dict) or not _inner_has_delegate_payload(inner):
        return None
    return inner


def parse_tool_call_arguments(raw: str, *, tool_name: str = "") -> tuple[Any, str | None]:
    """Unique tool-argument parse after protocol sanitize.

    ``tool_name`` is accepted for call-site stability; it does not change parse
    behaviour (no per-tool salvage).

    1. Empty / whitespace → ``({}, "{}")`` so callers rewrite the OpenAI
       ``function.arguments`` slot. An empty string is not valid JSON; leaving it
       on the assistant message 400s the next upstream turn.
    2. ``JSONDecoder.raw_decode`` — a complete value plus trailing junk is success
       (the Extra-data class: legal object + leftover ``}``).

    A **truncated** payload is deliberately not closed here. ``raw_decode`` succeeding
    means the model finished the value and only junk trailed — nothing is lost by
    dropping the junk. Closing an unfinished value is the opposite: the content the
    model never emitted stays missing, and for the write family that lands a half file
    under a success receipt. Truncation keeps the honest ``args_parse_failed`` face,
    whose model-side copy already teaches segmented writes.

    Returns ``(parsed, repaired_raw)``. ``repaired_raw`` is the accepted prefix
    when trailing junk was dropped; ``None`` when the original ``raw`` already
    decoded cleanly. Raises ``JSONDecodeError`` when unparseable — callers must
    not rewrite arguments (truncated writes must not execute). Empty is not that
    case: callers *should* rewrite the slot to ``"{}"``.
    """
    _ = tool_name
    if not str(raw or "").strip():
        return {}, "{}"

    decoder = json.JSONDecoder()
    obj, end = decoder.raw_decode(raw)
    if raw[end:].strip():
        return obj, raw[:end]
    return obj, None
