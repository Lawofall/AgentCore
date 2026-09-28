"""Run one resolved search target. Platform calls are admitted and counted here."""

from __future__ import annotations

from dataclasses import dataclass

from agentcore.tools.builtin.web.cleversee import CleverSeeBackend
from agentcore.tools.builtin.web.search_backend import (
    SearchBackend,
    SearchResult,
    SearXNGBackend,
    get_search_backend,
)
from agentcore.tools.builtin.web.search_choice import CLEVERSEE, SearchTarget
from agentcore.tools.builtin.web.search_quota import admit_platform_search, note_platform_search


@dataclass
class SearchDispatch:
    results: list[SearchResult]
    backend: SearchBackend | None


async def dispatch_search(
    target: SearchTarget,
    query: str,
    *,
    max_results: int,
    on_phase,
    language: str | None,
    user_id: str,
) -> SearchDispatch:
    """Execute ``target`` in this process. Proxy targets belong on the inference hop."""
    if target.kind == "platform":
        await admit_platform_search(user_id)
        backend = get_search_backend()
        results = await backend.search(
            query, max_results=max_results, on_phase=on_phase, language=language
        )
        await note_platform_search(user_id)
        return SearchDispatch(results, backend)

    if target.protocol == CLEVERSEE:
        owned: SearchBackend = CleverSeeBackend(target.api_key, target.base_url or None)
    else:
        owned = SearXNGBackend(target.base_url or None, api_key=target.api_key or None)
    try:
        results = await owned.search(
            query, max_results=max_results, on_phase=on_phase, language=language
        )
    finally:
        await owned.aclose()
    return SearchDispatch(results, owned)
