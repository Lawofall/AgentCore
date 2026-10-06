"""Model-facing ``run`` receipt: cap each stream, leave the frame outside the cut.

The default tool-result budget still applies. A single head+tail over the joined
blob can drop stderr between a long stdout and the exit line. Here each body is
cut on its own; labels, the exit line, and a verify header stay in the frame.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from agentcore.core.text import truncate_head_tail
from agentcore.tools.protocol import ToolResult

RUN_RECEIPT_BUDGET = ToolResult._MAX_OUTPUT_LEN


def allocate_stream_budgets(sizes: Sequence[int], room: int) -> list[int]:
    """Split ``room`` across stream bodies.

    A stream that already fits keeps its size. When exactly two streams both
    overflow, the second (stderr) keeps at least two fifths.
    """
    n = len(sizes)
    if n == 0:
        return []
    if room <= 0:
        return [0] * n
    ints = [int(size) for size in sizes]
    if sum(ints) <= room:
        return ints
    present = [i for i, size in enumerate(ints) if size > 0]
    if not present:
        return ints
    if len(present) == 1:
        out = [0] * n
        i = present[0]
        out[i] = min(ints[i], room)
        return out
    if n == 2:
        stdout_size, stderr_size = ints
        err_floor = min(stderr_size, max(1, room * 2 // 5))
        if stderr_size <= err_floor:
            return [min(stdout_size, max(0, room - stderr_size)), stderr_size]
        if stdout_size <= room - err_floor:
            return [stdout_size, min(stderr_size, room - stdout_size)]
        return [max(0, room - err_floor), err_floor]
    share = max(1, room // len(present))
    return [min(size, share) if size else 0 for size in ints]


def cap_inserted_bodies(
    bodies: Sequence[str],
    render: Callable[..., str],
    *,
    budget: int = RUN_RECEIPT_BUDGET,
) -> list[str]:
    """Bodies such that ``render`` fits ``budget`` when the surrounding frame fits.

    ``render`` must insert each body once. Text it adds around the bodies is not
    truncated. When that frame alone exceeds ``budget``, bodies are cleared and
    the caller raises ``output_limit`` so a later chop cannot eat the frame.
    """
    current = [body or "" for body in bodies]
    full = render(*current)
    if len(full) <= budget:
        return current
    reserved = len(full) - sum(len(body) for body in current)
    room = budget - reserved
    if room <= 0:
        return ["" for _ in current]
    budgets = allocate_stream_budgets([len(body) for body in current], room)
    capped: list[str] = []
    for body, limit in zip(current, budgets, strict=True):
        if not body or len(body) <= limit:
            capped.append(body)
        elif limit <= 0:
            capped.append("")
        else:
            capped.append(truncate_head_tail(body, limit))
    return capped


def receipt_output_limit(output: str, *, budget: int = RUN_RECEIPT_BUDGET) -> int | None:
    """Let a frame that itself exceeds ``budget`` through uncut."""
    if len(output) > budget:
        return len(output)
    return None
