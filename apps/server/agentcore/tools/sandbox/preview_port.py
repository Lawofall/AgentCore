"""Parse HTTP listen ports from long-running process ready logs.

User preview (安全 · 五、第二刀) keys off this parse: no port → no product
button. Does not scan the guest's listen table.
"""

from __future__ import annotations

import re

_URL_PORT = re.compile(
    r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::\]|\[::1\])[:/](\d{2,5})",
    re.IGNORECASE,
)
_HOST_PORT = re.compile(
    r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})\b",
    re.IGNORECASE,
)
_V6_PORT = re.compile(r"\[::1?\]:(\d{2,5})\b")
_SERVING_PORT = re.compile(
    r"(?:listening|running|started|serving).{0,80}(?:port\s+|:)(\d{2,5})\b",
    re.IGNORECASE,
)

_PATTERNS = (_URL_PORT, _HOST_PORT, _V6_PORT, _SERVING_PORT)


def parse_preview_http_ports(output: str) -> tuple[int, ...]:
    """Unique HTTP ports in appearance order. Empty when nothing looks like a listen URL."""
    text = output or ""
    hits: list[tuple[int, int]] = []
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            port = int(match.group(1))
            if 1 <= port <= 65535:
                hits.append((match.start(), port))
    hits.sort(key=lambda item: item[0])
    seen: list[int] = []
    found: set[int] = set()
    for _start, port in hits:
        if port in found:
            continue
        found.add(port)
        seen.append(port)
    return tuple(seen)
