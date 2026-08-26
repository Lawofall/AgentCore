"""Netns capability-error classification for cloud browser sessions.

Per-session family=browser netns is gone: Chromium execs into the workspace
desk guest. This module only classifies host isolation failures so a turn can
retire ``browser_*`` without a second sticky assembly gate.
"""

from __future__ import annotations


class NetnsError(RuntimeError):
    """A netns / veth setup or teardown step failed."""


# Stable tool ``metadata.code`` when sandbox network isolation cannot be created.
# Permanent for the run: retrying browser_* will hit the same host capability gap.
EGRESS_UNAVAILABLE_CODE = "egress_unavailable"


def is_netns_capability_error(exc: BaseException) -> bool:
    """True when ``exc`` (or its cause chain) is a host netns / veth capability failure.

    Covers :class:`NetnsError` and the common wrapped form
    ``mkdir /run/netns … Permission denied`` that appears after generic Exception
    → BrowserSessionError wrapping.
    """
    cur: BaseException | None = exc
    seen: set[int] = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, NetnsError):
            return True
        text = str(cur)
        if "NetnsError" in text or "mkdir /run/netns" in text:
            return True
        if "ip netns" in text and "Permission denied" in text:
            return True
        cur = cur.__cause__ or cur.__context__
    return False
