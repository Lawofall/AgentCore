"""Structure-preserving cap for the CEO synthesis package.

Worker bodies already share ``CEO_SYNTHESIS_BUDGET``. This module only
enforces the pathological safety valve (``DELEGATE_OUTPUT_LIMIT``): keep
roster / failure blocks / closing, shrink long worker bodies, never a
silent tail chop. ToolResult and ``ALL_COMPLETED.output`` both use the
same composed string — do not cap twice.
"""

from __future__ import annotations

import re

from agentcore.core.text import truncate_head_tail
from agentcore.runtime.runs.constants import DELEGATE_OUTPUT_LIMIT
from agentcore.runtime.runs.fidelity import allocate

ALL_COMPLETED_OUTPUT_LIMIT = DELEGATE_OUTPUT_LIMIT

_WORKER_HEAD = re.compile(
    r"^### .+（(?P<status>completed|failed|skipped|cancelled|unknown)） · run_id: `"
)
_FAILURE_HEADS = (
    "### tool_failures",
    "### ⚠️ 契约缺口",
    "### ⚠️ 队员升级了",
    "### ✅ 已当场答复的升级",
)
_FAIL_STATUSES = frozenset({"failed", "skipped", "cancelled"})
_ROSTER_HEAD = "### 队员终态名册"
_ELISION_TAG = "[系统视图截断·非磁盘内容]"


def compose_all_completed_output(
    prose: str,
    roster_text: str = "",
    closing_text: str = "",
    *,
    limit: int = ALL_COMPLETED_OUTPUT_LIMIT,
) -> str:
    """Join synthesis parts under ``limit``, keeping roster / failures first.

    Worker long-bodies are water-filled (longest shrink first). Every drop is
    marked with the system-view elision tag — never a silent omit.
    """
    if limit <= 0:
        return ""
    roster = (roster_text or "").strip()
    closing = (closing_text or "").strip()
    body = (prose or "").replace("\r\n", "\n")
    body, peeled_closing = _peel_closing(body)
    if peeled_closing and not closing:
        closing = peeled_closing
    body, peeled_roster = _peel_roster(body)
    if peeled_roster and not roster:
        roster = peeled_roster

    intro, failures, workers, others, blobs = _partition_sections(_split_sections(body))
    intro_text = _join(intro)
    failure_text = _join(failures)
    other_text = _join(others)

    full = _assemble(intro_text, failure_text, roster, other_text, workers + blobs, closing)
    if len(full) <= limit:
        return full

    protected = _assemble(intro_text, failure_text, roster, "", [], closing)
    if len(protected) >= limit:
        dropped = len(workers) + len(blobs)
        extra = (
            _omit_workers_line(dropped)
            if dropped
            else (_omit_others_line(1) if other_text else "")
        )
        return _fit_protected(intro_text, failure_text, roster, closing, limit, extra=extra)

    middle_budget = _middle_budget(intro_text, failure_text, roster, closing, limit)
    middle = _fit_middle(workers, blobs, other_text, middle_budget)
    assembled = _assemble(intro_text, failure_text, roster, middle, [], closing)
    if len(assembled) <= limit:
        return assembled
    overflow = len(assembled) - limit
    tighter = _fit_middle(workers, blobs, other_text, max(0, middle_budget - overflow))
    assembled = _assemble(intro_text, failure_text, roster, tighter, [], closing)
    if len(assembled) <= limit:
        return assembled
    return _trim_middle_only(intro_text, failure_text, roster, assembled, closing, limit)


def cap_all_completed_output(
    text: str, *, limit: int = ALL_COMPLETED_OUTPUT_LIMIT
) -> str:
    """Cap an already-joined synthesis blob (host backfill / leak path)."""
    raw = (text or "").replace("\r\n", "\n").strip()
    if len(raw) <= limit:
        return raw
    body, closing = _peel_closing(raw)
    prose, roster = _peel_roster(body)
    return compose_all_completed_output(prose, roster, closing, limit=limit)


def _join(parts: list[str] | tuple[str, ...]) -> str:
    return "\n".join(p for p in parts if p)


def _assemble(
    intro: str,
    failures: str,
    roster: str,
    middle: str,
    extra_sections: list[str],
    closing: str,
) -> str:
    extras = _join(extra_sections) if extra_sections else ""
    mid = _join([p for p in (middle, extras) if p])
    return _join([intro, failures, roster, mid, closing])


def _middle_budget(intro: str, failures: str, roster: str, closing: str, limit: int) -> int:
    anchors = [p for p in (intro, failures, roster, closing) if p]
    base = sum(len(a) for a in anchors) + max(0, len(anchors) - 1)
    extra_sep = 1 if anchors else 0
    return max(0, limit - base - extra_sep)


def _fit_protected(
    intro: str,
    failures: str,
    roster: str,
    closing: str,
    limit: int,
    *,
    extra: str = "",
) -> str:
    """Roster and failure blocks win; later anchors shrink with a visible marker."""
    packed: list[str] = []
    for block in (roster, failures, extra, closing, intro):
        if not block:
            continue
        used = len(_join(packed))
        sep = 1 if packed else 0
        room = limit - used - sep
        if room <= 0:
            break
        if len(block) <= room:
            packed.append(block)
            continue
        packed.append(truncate_head_tail(block, room))
        break
    out = _join(packed)
    return out if len(out) <= limit else truncate_head_tail(out, limit)


def _fit_middle(
    workers: list[str],
    blobs: list[str],
    other_text: str,
    budget: int,
) -> str:
    units: list[tuple[str, str]] = [_split_heading_body(w) for w in workers]
    units.extend(("", blob) for blob in blobs if blob)
    n_omit = len(workers) + len(blobs)
    if budget <= 0:
        return ""

    other_choices: list[str] = []
    if other_text:
        other_choices.append(other_text)
        other_choices.append(_omit_others_line(1))
    other_choices.append("")

    sizes = [len(body) for _, body in units]
    for other in other_choices:
        other_cost = (len(other) + 1) if other else 0
        body_budget = max(0, budget - other_cost - _heading_overhead(units))
        allowances = allocate(sizes, body_budget) if units else []
        rendered = _render_middle(units, allowances, other)
        if len(rendered) <= budget:
            return rendered
        overflow = len(rendered) - budget
        allowances = allocate(sizes, max(0, body_budget - overflow)) if units else []
        rendered = _render_middle(units, allowances, other)
        if len(rendered) <= budget:
            return rendered

    headings_only = _join([h for h, _ in units if h])
    marker = _omit_workers_line(n_omit) if n_omit else ""
    stub = _join([headings_only, marker])
    if stub and len(stub) <= budget:
        return stub
    if stub:
        return truncate_head_tail(stub, budget)
    if other_text:
        return (
            other_text
            if len(other_text) <= budget
            else truncate_head_tail(other_text, budget)
        )
    return ""


def _heading_overhead(units: list[tuple[str, str]]) -> int:
    """Chars spent on headings + per-unit newline, before bodies are attached."""
    if not units:
        return 0
    cost = 0
    for head, body in units:
        if head:
            cost += len(head) + (1 if body else 0)
        # body-only blob: no heading overhead
    cost += max(0, len(units) - 1)
    return cost


def _render_middle(
    units: list[tuple[str, str]],
    allowances: list[int],
    other: str,
) -> str:
    parts: list[str] = []
    if units and len(allowances) != len(units):
        allowances = [len(body) for _, body in units]
    for (head, body), allow in zip(units, allowances, strict=True):
        parts.append(_render_unit(head, body, allow))
    if other:
        parts.append(other)
    return _join(parts)


def _render_unit(head: str, body: str, allow: int) -> str:
    if not body:
        return head
    if allow >= len(body):
        return f"{head}\n{body}" if head else body
    if allow <= 0:
        if head:
            return f"{head}\n…"
        return "…"
    trimmed = truncate_head_tail(body, allow)
    return f"{head}\n{trimmed}" if head else trimmed


def _trim_middle_only(
    intro: str,
    failures: str,
    roster: str,
    assembled: str,
    closing: str,
    limit: int,
) -> str:
    """Last resort: shrink only the middle slice so roster / failures stay intact."""
    prefix = _join([intro, failures, roster])
    suffix = closing
    if prefix and assembled.startswith(prefix):
        rest = assembled[len(prefix) :]
        if rest.startswith("\n"):
            rest = rest[1:]
        if suffix and rest.endswith(suffix):
            mid = rest[: len(rest) - len(suffix)].rstrip("\n")
        else:
            mid = rest
        room = _middle_budget(intro, failures, roster, closing, limit)
        if room <= 0:
            capped_mid = ""
        elif len(mid) <= room:
            capped_mid = mid
        else:
            capped_mid = truncate_head_tail(mid, room)
        out = _assemble(intro, failures, roster, capped_mid, [], closing)
        return out if len(out) <= limit else truncate_head_tail(out, limit)
    return truncate_head_tail(assembled, limit)


def _omit_workers_line(n: int) -> str:
    return f"{_ELISION_TAG} 已省略 {n} 名队员正文"


def _omit_others_line(n: int) -> str:
    return f"{_ELISION_TAG} 已省略 {n} 节队员建议"


def _split_sections(text: str) -> list[str]:
    raw = (text or "").strip("\n")
    if not raw.strip():
        return []
    parts = re.split(r"(?=^### )", raw, flags=re.M)
    return [p.strip("\n") for p in parts if p.strip()]


def _first_line(section: str) -> str:
    return section.lstrip().split("\n", 1)[0]


def _split_heading_body(section: str) -> tuple[str, str]:
    raw = section.strip("\n")
    if "\n" not in raw:
        return raw, ""
    head, body = raw.split("\n", 1)
    return head, body.lstrip("\n")


def _partition_sections(
    sections: list[str],
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    intro: list[str] = []
    failures: list[str] = []
    workers: list[str] = []
    others: list[str] = []
    blobs: list[str] = []
    for section in sections:
        kind = _section_kind(section)
        if kind == "intro":
            intro.append(section)
        elif kind in {"failure", "worker_fail"}:
            failures.append(section)
        elif kind == "worker":
            workers.append(section)
        elif kind == "other":
            others.append(section)
        else:
            blobs.append(section)
    return intro, failures, workers, others, blobs


def _section_kind(section: str) -> str:
    first = _first_line(section)
    if first.startswith(_ROSTER_HEAD):
        return "roster"
    if first.startswith(_FAILURE_HEADS):
        return "failure"
    matched = _WORKER_HEAD.match(first)
    if matched:
        status = matched.group("status")
        return "worker_fail" if status in _FAIL_STATUSES else "worker"
    if first.startswith("## 团队执行结果"):
        return "intro"
    if first.startswith("###"):
        return "other"
    return "blob"


def _peel_closing(text: str) -> tuple[str, str]:
    raw = text or ""
    for mark in ("\n---\n以上为团队产出", "\n---\n**有队员失败"):
        idx = raw.find(mark)
        if idx >= 0:
            return raw[:idx].rstrip(), raw[idx + 1 :].strip()
    idx = raw.rfind("\n---\n")
    if idx >= 0 and "【终稿纪律】" in raw[idx:]:
        return raw[:idx].rstrip(), raw[idx + 1 :].strip()
    return raw, ""


def _peel_roster(text: str) -> tuple[str, str]:
    rest: list[str] = []
    roster_parts: list[str] = []
    for section in _split_sections(text):
        if _first_line(section).startswith(_ROSTER_HEAD):
            roster_parts.append(section.strip())
        else:
            rest.append(section)
    if not roster_parts:
        return text, ""
    return _join(rest), _join(roster_parts)
