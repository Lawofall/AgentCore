"""Live stdout/stderr for a desktop EXECUTE op that has not settled yet.

The sandbox calls ``ExecutionRequest.on_output`` directly. A desktop op is one
round-trip, so the same callback is bound here for the op's ``request_id`` and
fed by output chunks until ``WorkspaceChannel.request`` returns. Chunks are not
journaled; ``tool_use_end.display`` stays the authority after the call ends.
"""

from __future__ import annotations

from collections.abc import Callable

_sinks: dict[str, Callable[[str, str], None]] = {}

_STREAMS = frozenset({"stdout", "stderr"})
_MAX_CHUNK = 8192


def bind_exec_output(request_id: str, sink: Callable[[str, str], None]) -> None:
    _sinks[request_id] = sink


def unbind_exec_output(request_id: str) -> None:
    _sinks.pop(request_id, None)


def feed_exec_output(request_id: str, stream: str, chunk: str) -> bool:
    """Forward one chunk to the bound tool callback. False when nothing is waiting."""
    if stream not in _STREAMS or not chunk:
        return False
    sink = _sinks.get(request_id)
    if sink is None:
        return False
    sink(stream, chunk[:_MAX_CHUNK])
    return True
