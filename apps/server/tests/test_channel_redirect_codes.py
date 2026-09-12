"""Desktop shared CHANNEL_REDIRECT_CODE_LIST must match the Python closed set."""

from __future__ import annotations

import re
from pathlib import Path

from agentcore.runtime.engine.tool_channel_redirect import CHANNEL_REDIRECT_CODES

_TS = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "desktop"
    / "src"
    / "shared"
    / "channelRedirectCodes.ts"
)
_LIST_RE = re.compile(
    r"export const CHANNEL_REDIRECT_CODE_LIST = \[([^\]]+)\]",
    re.S,
)


def test_channel_redirect_codes_match_desktop_shared() -> None:
    text = _TS.read_text(encoding="utf-8")
    match = _LIST_RE.search(text)
    assert match, f"CHANNEL_REDIRECT_CODE_LIST missing in {_TS}"
    ts_codes = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert ts_codes == set(CHANNEL_REDIRECT_CODES)
