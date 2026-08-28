"""Atomic temp+replace with Windows lock retry (and optional in-place fallback).

``os.replace`` on Windows needs DELETE-share on the destination. Antivirus /
search indexers often hold a file with read sharing only, so replace raises
WinError 5/32 while an in-place ``write_bytes`` still succeeds. Outbox already
retried this; workspace ``str_replace`` did not — same-directory edits then
failed after a successful ``file_write``.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.workspace._paths import is_access_denied_oserror

logger = get_logger(__name__)

REPLACE_RETRY_DELAYS_S = (0.0, 0.05, 0.15, 0.35, 0.75)

_TMP_PREFIX = ".tmp_str_replace_"


def is_transient_replace_error(exc: BaseException) -> bool:
    """True for brief Windows locks / POSIX cousins that may clear on retry."""
    return is_access_denied_oserror(exc)


def replace_with_retry(
    tmp: Path | str,
    target: Path | str,
    *,
    family: Literal["workspace", "outbox"] = "workspace",
    delays: Sequence[float] | None = None,
) -> None:
    """``os.replace`` with limited retry for transient locks; re-raises if exhausted.

    ``family`` selects catalogued event names (literals — log registry scan).
    """
    src = Path(tmp)
    dest = Path(target)
    last: OSError | None = None
    wait = tuple(delays) if delays is not None else REPLACE_RETRY_DELAYS_S
    for attempt, delay in enumerate(wait):
        if delay:
            time.sleep(delay)
        try:
            os.replace(src, dest)
            if attempt > 0:
                if family == "outbox":
                    logger.warning(
                        "sidecar.outbox_replace_recovered",
                        target=str(dest),
                        attempts=attempt + 1,
                    )
                else:
                    logger.warning(
                        "workspace.atomic_replace_recovered",
                        target=str(dest),
                        attempts=attempt + 1,
                    )
            return
        except OSError as e:
            if not is_transient_replace_error(e):
                raise
            last = e
            if family == "outbox":
                logger.warning(
                    "sidecar.outbox_replace_retry",
                    target=str(dest),
                    attempt=attempt + 1,
                    max_attempts=len(wait),
                    error=str(e),
                )
            else:
                logger.warning(
                    "workspace.atomic_replace_retry",
                    target=str(dest),
                    attempt=attempt + 1,
                    max_attempts=len(wait),
                    error=str(e),
                )
    assert last is not None
    raise last


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    inplace_fallback: bool = True,
) -> None:
    """Write ``data`` via temp file + replace; optionally fall back to in-place.

    Fallback matches ``file_write`` durability (truncating write) and is only
    used after replace retries exhaust on a transient lock. Crash mid-fallback
    can truncate; that is the same contract ``ServerWorkspace.write`` already
    accepted.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=_TMP_PREFIX, suffix=".tmp")
    tmp_path = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            replace_with_retry(tmp_path, path)
        except OSError as e:
            if not (inplace_fallback and is_transient_replace_error(e)):
                raise
            logger.warning(
                "workspace.atomic_write_inplace_fallback",
                target=str(path),
                error=str(e),
            )
            path.write_bytes(data)
            with contextlib.suppress(OSError):
                tmp_path.unlink()
    except Exception:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise
