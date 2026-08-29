"""Turn-level consult reuse (kickoff → resume 同 key 不重复拉全文).

Kickoff 段 ``consult(设计审美)`` 后 pause，resume 段模型常再调一次同一 key。
本模块在回合内缓存已命中主题正文：同 slug 再查直接复用，并记 ``consult.reuse``。

缓存挂 ContextVar，pause 帧可序列化 ``consulted_memory``；resume 优先从帧恢复，
也可从窗口里已有的 consult tool 对回填（兼容旧帧）。
"""

from __future__ import annotations

import json
from contextvars import ContextVar

from agentcore.llm.provider.protocol import LLMMessage, llm_content_text

# Unified ``consult`` plus the pre-split names still present in older frames.
_CONSULT_TOOL_NAMES = ("consult", "consult_memory", "consult_skill", "consult_rule")

_VALID_ORIGIN = frozenset({"system", "user"})


class _ConsultCache(dict[str, str]):
    """slug → body (pause-serializable) plus in-process origin sidecar."""

    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        super().__init__(mapping if mapping is not None else ())
        self.origins: dict[str, str] = {}


# slug → full note body (same turn). Pause frames copy ``dict(cache)`` so origin
# stays process-local and is dropped on resume-from-frame.
consulted_memory_cache: ContextVar[dict[str, str] | None] = ContextVar(
    "consulted_memory_cache", default=None
)


def get_consult_cache() -> dict[str, str]:
    """Return the live cache dict (never None); creates one if unbound."""
    cache = consulted_memory_cache.get()
    if cache is None:
        cache = {}
        consulted_memory_cache.set(cache)
    return cache


def remember_consult(slug: str, body: str, origin: str | None = None) -> None:
    if not slug or not body:
        return
    cache = get_consult_cache()
    cache[slug] = body
    if origin in _VALID_ORIGIN:
        if not isinstance(cache, _ConsultCache):
            wrapped = _ConsultCache(cache)
            consulted_memory_cache.set(wrapped)
            cache = wrapped
        cache.origins[slug] = origin


def lookup_consult(slug: str) -> str | None:
    cache = consulted_memory_cache.get()
    if not cache:
        return None
    return cache.get(slug)


def lookup_consult_origin(slug: str) -> str | None:
    """In-process origin for same-turn reuse; None after frame restore (body only)."""
    cache = consulted_memory_cache.get()
    if not isinstance(cache, _ConsultCache):
        return None
    origin = cache.origins.get(slug)
    return origin if origin in _VALID_ORIGIN else None


def seed_consult_cache_from_window(messages: list[LLMMessage]) -> int:
    """Populate cache from prior consult tool pairs in the CEO window.

    Returns number of topics seeded.
    """
    call_id_to_slug: dict[str, str] = {}
    for message in messages:
        if message.role != "assistant" or not message.tool_calls:
            continue
        for call in message.tool_calls:
            if call.function.name not in _CONSULT_TOOL_NAMES:
                continue
            try:
                data = json.loads(call.function.arguments or "")
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            raw = str(data.get("name") or "").strip()
            topic_slug = raw.removeprefix("主题/").removesuffix(".md").strip()
            if topic_slug:
                call_id_to_slug[call.id] = topic_slug

    seeded = 0
    cache = get_consult_cache()
    for message in messages:
        if message.role != "tool" or not message.tool_call_id:
            continue
        slug = call_id_to_slug.get(message.tool_call_id)
        if not slug:
            continue
        body = llm_content_text(message.content)
        if not body.strip() or body.startswith("没有名为"):
            continue
        if slug not in cache:
            cache[slug] = body
            seeded += 1
    return seeded
