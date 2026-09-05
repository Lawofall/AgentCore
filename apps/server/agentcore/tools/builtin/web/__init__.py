"""Web tools: ``web_search`` (via self-hosted SearXNG) + ``web_fetch`` (fetch +
extract) + ``download_url`` (HTTP(S) → workspace bytes). Direct-egress tools
share networking resilience from ``_net`` (per-host circuit breaker, honest
error messages, tuned timeouts) and the same SSRF policy as ``web_fetch``.
"""

from agentcore.tools.builtin.web.download_url import DownloadUrlTool
from agentcore.tools.builtin.web.search import WebSearchTool
from agentcore.tools.builtin.web.web_fetch import WebFetchTool

__all__ = ["DownloadUrlTool", "WebFetchTool", "WebSearchTool"]
