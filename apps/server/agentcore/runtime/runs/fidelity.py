"""Shared product-fidelity primitives — render a worker's product at a chosen size.

Two layers share one discipline so a teammate's product is sized the same way
wherever it is re-shown:

- ``executor.context._dep_context_blocks`` — an upstream product → a downstream worker's
  prompt (上下文传递, 通道③).
- ``ceo_format.format_for_ceo`` — every worker's product → the CEO's synthesis input
  (CEO 综述输入瘦身). Without this a wide fan-out of long products would balloon the
  CEO's overview pass and hit the ``ToolResult`` truncation net, whose middle-elision of
  a multi-worker aggregate would drop whole workers — better to size + shape it here than
  lean on that last-resort net.

The POLICY (file-producer → pointer; ``summarize`` deps → digest; else pass_through
prose water-filled across a shared budget, head+tail trimmed on overflow) lives at
each call site; this module is just the MECHANISM. All char budgets live in
``constants.py``. Every over-budget cut here is HEAD+TAIL (``core.text.truncate_head_tail``)
so trailing details (金额 / 法条编号 / 收尾) survive — never a head-only chop.
"""

from __future__ import annotations

from agentcore.core.text import truncate_head_tail as _truncate_head_tail
from agentcore.runtime.runs.constants import (
    DEP_POINTER_MAX_FILES,
    DEP_POINTER_SUMMARY_CHARS,
)


def pointer_body(content: str, files: list[str]) -> str:
    """A file-producing product's POINTER body: a tight digest of its prose handoff +
    the artifact paths to ``file_read``.

    The full product lives in the shared workspace (it called ``file_write``), so a
    reader pulls only what it needs rather than carrying the whole artifact in-prompt
    (递指针不递全文). The digest keeps the worker's own orientation note (改了哪些文件 /
    怎么用 / 关键取舍); the path list is the pointer. Both are bounded
    (``DEP_POINTER_SUMMARY_CHARS`` / ``DEP_POINTER_MAX_FILES``)."""
    parts: list[str] = []
    # HEAD+TAIL trim (not head-only): the digest keeps the worker's orientation note whose
    # 关键取舍 / 收尾 often sit at the tail — a head-only chop would silently drop them.
    digest = (
        _truncate_head_tail(content, DEP_POINTER_SUMMARY_CHARS) if content.strip() else ""
    )
    if digest:
        parts.append(digest)
    listed = files[:DEP_POINTER_MAX_FILES]
    lines = "\n".join(f"- {p}" for p in listed)
    more = f"\n……（共 {len(files)} 个文件）" if len(files) > len(listed) else ""
    parts.append(
        "已写入共享工作区的文件（下列是磁盘真实路径；约定文档会把子文件夹压进文件名。"
        "先 file_read 这些路径再写你的交付物，勿用任务书里带一层子目录的旧写法；"
        "不要凭空臆测，也勿全仓 glob / grep 重搜）：\n" + lines + more
    )
    return "\n\n".join(parts)


def allocate(sizes: list[int], budget: int) -> list[int]:
    """Fair-share ``budget`` across items of the given ``sizes`` (water-filling).

    Processing smallest-first, each item gets ``min(its size, an equal split of the
    budget still left)``: a small item claims only what it needs and frees the rest,
    which redistributes to the larger items — so the budget is used fully and a lone
    item gets the whole of it. Returns a per-item char allowance in the INPUT order.
    ``[]`` for no items."""
    n = len(sizes)
    if n == 0:
        return []
    allowances = [0] * n
    remaining = budget
    # Smallest-first so an item that needs less than its equal share frees the
    # remainder for the larger ones (classic water-filling).
    for rank, i in enumerate(sorted(range(n), key=lambda i: sizes[i])):
        share = remaining // (n - rank)
        allowances[i] = min(sizes[i], share)
        remaining -= allowances[i]
    return allowances


def truncate_head_tail(content: str, limit: int) -> str:
    """Trim ``content`` to ``limit`` keeping BOTH ends (so trailing 金额 / 法条编号
    survive). Thin binding of the leaf primitive ``core.text.truncate_head_tail`` with
    this package's default marker, so the runs callers keep one import surface."""
    return _truncate_head_tail(content, limit)
