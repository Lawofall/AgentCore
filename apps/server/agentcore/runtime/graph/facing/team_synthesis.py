"""Worker output one-line blurbs for CEO inject / host / drive terminal.

Template, not LLM. Used when formatting worker_completed into the CEO window.
"""

from __future__ import annotations

from agentcore.runtime.runs.types import RunPhase, RunState

# One-line clip for a worker's output blurb (template, not LLM).
_SUMMARY_CHARS = 80


def _clip(text: str, limit: int = _SUMMARY_CHARS) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def worker_output_blurb(state: RunState) -> str:
    """Best-effort one-line product blurb from a completed (or failed) worker."""
    if state.phase is RunPhase.FAILED:
        err = (state.error or "").strip()
        return _clip(f"失败：{err}") if err else "失败"
    if state.phase is RunPhase.CANCELLED:
        return "已停止"
    debrief = state.debrief if isinstance(state.debrief, dict) else None
    if debrief:
        summary = str(debrief.get("summary") or "").strip()
        if summary:
            return _clip(summary)
    content = (state.content or "").strip()
    if content:
        first = next((ln.strip() for ln in content.splitlines() if ln.strip()), "")
        if first:
            return _clip(first)
    return "（无摘要）"
