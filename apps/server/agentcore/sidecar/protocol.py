"""JSON-RPC 2.0 framing for the sidecar's stdio transport (pure, unit-testable).

The desktop ↔ sidecar channel is **line-delimited JSON**: exactly one JSON value
per physical line, ``\\n``-terminated. ``json.dumps`` escapes any newline inside a
string as ``\\n``, so a single ``dumps`` never spans lines — one ``readline`` on the
other end is therefore always exactly one message. (LSP-style ``Content-Length``
framing is overkill here; line framing is simpler and just as robust given that
invariant.)

This module is intentionally free of I/O and engine imports: it only builds and
parses message dicts, so it can be exercised in isolation. Transport (stdin/
stdout) lives in ``sidecar.server`` / ``sidecar.__main__``.

Shapes (a loose subset of JSON-RPC 2.0):

- request:      ``{"jsonrpc":"2.0","id":<id>,"method":<str>,"params":<obj>}``
- response ok:  ``{"jsonrpc":"2.0","id":<id>,"result":<obj>}``
- response err: ``{"jsonrpc":"2.0","id":<id>,"error":{"code":<int>,"message":<str>}}``
- notification: ``{"jsonrpc":"2.0","method":<str>,"params":<obj>}`` (no ``id``)
"""

from __future__ import annotations

import json
from typing import Any

#: Bumped when the request/notification contract changes in a breaking way; the
#: desktop checks it in ``initialize`` so a stale pairing fails loudly, not subtly.
PROTOCOL_VERSION = "0.1"

JSONRPC_VERSION = "2.0"

# JSON-RPC reserved error codes (https://www.jsonrpc.org/specification#error_object).
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Server-defined codes (the -32000..-32099 implementation-defined band).
TURN_CANCELLED = -32001
NOT_INITIALIZED = -32002
# A ``resume`` whose durable frame is gone (already resumed, or never persisted) —
# the desktop drops the stale resume card, mirroring the cloud route's 404.
PAUSED_TURN_NOT_FOUND = -32003
# Resume failed after claim but the frame was rolled back — the desktop keeps the
# resume card and may retry (non-destructive claim).
RESUME_RETRYABLE = -32004
# ``deliverMessage`` while this sidecar has no occupying live turn.
NO_LIVE_TURN = -32005
# Hot pending (approval / escalation) — same condition as HTTP 409.
PENDING_INTERACTIONS = -32006
# ``cancelQueuedTurn``: unknown or already started.
QUEUED_TURN_NOT_FOUND = -32007


class ProtocolError(ValueError):
    """A line could not be parsed as a JSON-RPC message."""


def encode_line(message: dict[str, Any]) -> str:
    """Serialize one message to its wire line (compact JSON + trailing newline).

    ``ensure_ascii=False`` keeps UTF-8 text intact (the stream is configured for
    UTF-8 in ``__main__``); ``StrEnum`` payload values (``EventType`` /
    ``FinishReason``) serialize as their string value since they subclass ``str``.
    """
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"


def decode_line(line: str) -> dict[str, Any]:
    """Parse one wire line into a message dict.

    Tolerates a leading UTF-8 BOM (``\ufeff``): some text producers prepend one to
    the first line, and ``json.loads`` rejects it outright. Node's framed writer
    never emits a BOM, so this only ever matters for stray non-Node producers
    (manual stdio debugging, a piped file) — stripping it at the one decode point
    keeps the protocol robust without special-casing elsewhere.

    Raises :class:`ProtocolError` on malformed JSON or a non-object top level, so
    the server can reply with a ``PARSE_ERROR`` instead of crashing the loop.
    """
    try:
        value = json.loads(line.lstrip("\ufeff"))
    except json.JSONDecodeError as e:
        raise ProtocolError(f"invalid JSON: {e}") from e
    if not isinstance(value, dict):
        raise ProtocolError("message must be a JSON object")
    return value


def make_result(request_id: Any, result: Any) -> dict[str, Any]:
    """Build a success response for ``request_id``."""
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def make_error(request_id: Any, code: int, message: str, *, data: Any = None) -> dict[str, Any]:
    """Build an error response for ``request_id`` (``data`` is optional detail)."""
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def make_notification(method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build a server→client notification (no ``id`` — no response expected)."""
    return {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
