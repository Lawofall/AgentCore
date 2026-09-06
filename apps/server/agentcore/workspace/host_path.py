"""Classify file-tool paths: workspace-relative vs already-mounted vs host OS.

The API process may run on Linux while the desktop is Windows/macOS, so
absolute-ness uses :func:`is_absolute_os_path`, not ``os.path.isabs``.
Bare names like ``Downloads`` stay workspace-relative — only ``~/Desktop``
(and siblings) plus OS-absolute paths are host.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal

from agentcore.core.paths import is_absolute_os_path
from agentcore.workspace.external_mounts import EXTERNAL_PREFIX, parse_external_path

GrantMode = Literal["readonly", "organize", "attach_rw"]
PathKind = Literal["workspace", "external_ns", "host", "forbidden"]

_MODE_RANK: dict[str, int] = {"readonly": 0, "organize": 1, "attach_rw": 2}


def mode_covers(have: str | None, need: GrantMode) -> bool:
    """True when ``have`` already authorizes ``need`` (readonly < organize < attach_rw)."""
    return _MODE_RANK.get(have or "", -1) >= _MODE_RANK[need]


WELL_KNOWN_KEYS = frozenset({"desktop", "downloads", "documents"})

_HOME_WELL_KNOWN_RE = re.compile(
    r"^(?:~|%(?:USERPROFILE|HOME)%|\$(?:HOME|USERPROFILE))[/\\]+"
    r"(Desktop|Downloads|Documents)(?:[/\\](?P<rest>.*))?$",
    re.IGNORECASE,
)
_HOME_PREFIX_RE = re.compile(
    r"^(?:~|%(?:USERPROFILE|HOME)%|\$(?:HOME|USERPROFILE))(?:[/\\].*)?$",
    re.IGNORECASE,
)
_DRIVE_ROOT_RE = re.compile(r"^[A-Za-z]:/?$")


@dataclass(frozen=True)
class ClassifiedPath:
    kind: PathKind
    original: str
    well_known: str | None = None
    target_name: str | None = None
    abs_path: str | None = None
    remainder: str = ""
    forbidden_reason: str | None = None


def _unify(raw: str) -> str:
    return raw.replace("\\", "/").strip()


def is_forbidden_host_root(path: str) -> bool:
    """True for drive roots and ``/`` — never silently grant the whole volume."""
    u = _unify(path).rstrip("/")
    if u in ("", "/"):
        return True
    return bool(_DRIVE_ROOT_RE.fullmatch(u))


def split_host_parent(abs_path: str) -> tuple[str, str]:
    """Parent directory + basename for a client OS absolute path."""
    s = abs_path.strip()
    windows = PureWindowsPath(s).is_absolute() and (
        (len(s) >= 2 and s[1] == ":") or s.startswith("\\\\") or s.startswith("//")
    )
    if windows:
        win = PureWindowsPath(s)
        return str(win.parent), win.name
    posix = PurePosixPath(_unify(s))
    return str(posix.parent), posix.name


def _split_target_remainder(rest: str) -> tuple[str | None, str]:
    parts = [p for p in rest.replace("\\", "/").split("/") if p and p != "."]
    if not parts:
        return None, ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], "/".join(parts[1:])


def classify_tool_path(raw: str, *, root_label: str | None = None) -> ClassifiedPath:
    """Structured path class for file tools. Not an intent classifier."""
    s = (raw or "").strip()
    if not s or s in (".", "./"):
        return ClassifiedPath(kind="workspace", original=s or ".")

    unified = _unify(s)
    if unified in ("/",):
        return ClassifiedPath(kind="workspace", original=s)
    if parse_external_path(s) is not None or unified == EXTERNAL_PREFIX.rstrip("/"):
        return ClassifiedPath(kind="external_ns", original=s)
    if unified.startswith(EXTERNAL_PREFIX):
        return ClassifiedPath(kind="external_ns", original=s)

    home = _HOME_WELL_KNOWN_RE.match(s)
    if home:
        leaf = home.group(1).lower()
        rest = (home.group("rest") or "").strip()
        target, remainder = _split_target_remainder(rest)
        return ClassifiedPath(
            kind="host",
            original=s,
            well_known=leaf,
            target_name=target,
            remainder=remainder,
        )
    if _HOME_PREFIX_RE.match(s):
        return ClassifiedPath(
            kind="forbidden",
            original=s,
            forbidden_reason="home_not_well_known",
        )

    if is_absolute_os_path(s):
        labels = {
            (root_label or "").strip().replace("\\", "/").strip("/").lower(),
            "workspace",
        }
        labels.discard("")
        first = unified.lstrip("/").split("/")[0].lower() if unified.startswith("/") else ""
        if first in labels:
            return ClassifiedPath(kind="workspace", original=s)
        if is_forbidden_host_root(s):
            return ClassifiedPath(
                kind="forbidden",
                original=s,
                forbidden_reason="sensitive_root",
            )
        return ClassifiedPath(kind="host", original=s, abs_path=s)

    return ClassifiedPath(kind="workspace", original=s)
