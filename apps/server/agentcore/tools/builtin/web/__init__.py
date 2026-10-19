"""Web tools: ``web_search`` (via self-hosted SearXNG) + ``web_fetch`` (fetch +
extract). Direct-egress tools share networking resilience from ``_net``
(per-host circuit breaker, honest error messages, tuned timeouts) and the same
SSRF policy as ``web_fetch``.

Eager re-exports would import ``search.py`` as soon as any sibling (``deepseek``,
``cloud_fallback``) is loaded, which cycles with ``agentcore.search``.
"""

from typing import Any

__all__ = ["WebFetchTool", "WebSearchTool"]


def __getattr__(name: str) -> Any:
    if name == "WebSearchTool":
        from agentcore.tools.builtin.web.search import WebSearchTool

        return WebSearchTool
    if name == "WebFetchTool":
        from agentcore.tools.builtin.web.web_fetch import WebFetchTool

        return WebFetchTool
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
