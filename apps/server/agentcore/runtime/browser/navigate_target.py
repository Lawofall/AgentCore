"""Classify / rewrite ``browser(action=navigate)`` URL targets.

甲：桌面 Local Bridge —— 本会话工作区相对路径 → ``workspace://conv.{conv}/…``
（desk host；与用户「完整预览」同源）；公网 http(s) / 已构 workspace:// 原样通过。
乙：Sandbox / 非 local —— 相对路径与 workspace:// 诚实失败（见 browser 工具），禁止假成功。
不放开任意 ``file://``。
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote, unquote, urlparse

NavigateKind = Literal["http", "workspace", "relative", "invalid"]

WORKSPACE_SCHEME = "workspace"
_DEFAULT_ROOT_LABEL = "workspace"

# 乙：Sandbox / 无桌面预览 —— 相对路径诚实失败文案（引导完整预览）。
RELATIVE_PATH_UNSUPPORTED_MSG = (
    "当前浏览器宿主无法打开本会话工作区 HTML 相对路径"
    "（仅桌面 Local Bridge 支持，与用户「完整预览」同源）。"
    "请指引用户在产物卡或文件横幅点击「完整预览」在右坞「浏览器」中查看；"
    "本环境下本工具仅支持公网 http(s) URL，禁止假装已打开工作区页。"
)


def normalize_workspace_browser_path(pathname: str) -> str | None:
    """Mirror desktop ``normalizePreviewPath``：相对路径守卫，拒穿越 / 盘符 / 空路径。"""
    try:
        decoded = unquote(pathname)
    except Exception:  # noqa: BLE001
        return None
    posix = decoded.replace("\\", "/")
    stripped = _strip_root_label_prefix(posix, _DEFAULT_ROOT_LABEL)
    if stripped == ".":
        return None
    cleaned = stripped.lstrip("/")
    if not cleaned or "\0" in cleaned:
        return None
    parts = [p for p in cleaned.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    if re.match(r"^[a-zA-Z]:", parts[0]):
        return None
    return "/".join(parts)


def _strip_root_label_prefix(posix: str, root_label: str) -> str:
    """Strip leading ``/workspace`` or ``workspace/`` (same rescue as preview paths)."""
    label = (root_label or "").strip().strip("/")
    if not label:
        return posix
    lower = posix.lower()
    prefix_abs = f"/{label.lower()}"
    if lower == prefix_abs or lower == prefix_abs + "/":
        return "."
    if lower.startswith(prefix_abs + "/"):
        return posix[len(prefix_abs) :]  # keep original case of remainder
    # relative form ``workspace/…``
    if lower == label.lower():
        return "."
    if lower.startswith(label.lower() + "/"):
        return posix[len(label) + 1 :]
    return posix


def classify_navigate_target(url: str) -> NavigateKind:
    """Classify a navigate target for Local vs Sandbox gating."""
    raw = (url or "").strip()
    if not raw:
        return "invalid"
    # Explicit schemes with authority separator — never treat as relative.
    if "://" in raw:
        try:
            parsed = urlparse(raw)
        except Exception:  # noqa: BLE001
            return "invalid"
        scheme = (parsed.scheme or "").lower()
        if scheme in ("http", "https"):
            return "http" if parsed.netloc else "invalid"
        if scheme == WORKSPACE_SCHEME:
            return "workspace"
        return "invalid"
    # No :// → workspace-relative candidate (e.g. site/index.html).
    if normalize_workspace_browser_path(raw) is None:
        return "invalid"
    return "relative"


def build_workspace_browser_url(conversation_id: str, path: str) -> str | None:
    """Build ``workspace://conv.{conv}/{encoded_path}`` after path normalize."""
    conv = (conversation_id or "").strip().lower()
    if not conv:
        return None
    rel = normalize_workspace_browser_path(path)
    if not rel:
        return None
    encoded = "/".join(quote(seg, safe="") for seg in rel.split("/"))
    return f"{WORKSPACE_SCHEME}://conv.{conv}/{encoded}"


def rewrite_local_navigate_url(url: str, conversation_id: str) -> str | None:
    """Local Bridge：http(s)/workspace 透传；相对路径改写为 workspace:// desk host。"""
    kind = classify_navigate_target(url)
    if kind == "http" or kind == "workspace":
        return url.strip()
    if kind == "relative":
        return build_workspace_browser_url(conversation_id, url.strip())
    return None
