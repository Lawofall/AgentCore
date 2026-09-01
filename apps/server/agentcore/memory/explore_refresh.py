"""R1 fingerprint-dirty explore refresh — consolidation-style background bypass.

When the top-tree + key-manifest fingerprint drifts, assemble marks dirty and
schedules a **silent** per-folder refresh: workspace snapshot → memory-tier LLM →
merge-write 导航/画像 (optional topics) → update fingerprint + clear dirty.

Never blocks the user turn. Never runs CEO+delegate+team_preview.
Thick folder dossiers are on-demand ``主题/`` entries; this bypass only
merge-writes the short entry, so it never grows one.

Empty folder 画像 is not "go fill it": skip the LLM and do not write. Named
「先了解」 / 工程短语 still hard-explore in the user turn.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.config import settings
from agentcore.core.log_context import log_context
from agentcore.core.logging import get_logger
from agentcore.memory.explore_profile import (
    _KEY_MANIFEST_CANDIDATES,
    MAX_EXPLORE_TOPICS,
    filter_topics_by_scope_cap,
    folder_profile_is_empty,
    load_folder_profile,
    parse_explore_topics,
    record_explore_closeout,
    write_folder_navigation,
    write_folder_profile_cas,
    write_folder_topics_replace,
)
from agentcore.memory.store import (
    NAVIGATION_MEMORY_FILE,
    MemoryStore,
    default_memory_store,
)

if TYPE_CHECKING:
    from agentcore.llm.provider.protocol import LLMProvider
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

_SNAPSHOT_MAX_CHARS = 10_000
_MANIFEST_BODY_CAP = 1_200
_REFRESH_TIMEOUT_SECONDS = 45.0

_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")

_REFRESH_SYSTEM = """\
You refresh a folder's short memory entry after its workspace structure drifted.
You receive a workspace snapshot (top-level tree + key manifest excerpts) plus the
CURRENT folder 画像.md and 导航.md. Merge-update them so the short entry stays
accurate — do NOT invent long encyclopedic docs.

Output ONLY a JSON object:
{
  "profile": "<FULL folder 画像.md markdown to merge, or null if unchanged>",
  "navigation": "<FULL 导航.md short entry (one-line定位 + 我要…→先读路由), or null>",
  "topics": [{"slug":"<ascii>","content":"<markdown>"}]  // optional; soft top 5; omit or [] if none
}

Rules:
- profile sections (when present): 技术栈与工具, 关于用户的事实, 项目约束
- navigation is a SHORT pointer only — never paste long bodies; point to 主题/
- Prefer null over rewriting when the snapshot does not change durable facts
- Never wipe still-valid bullets; merge updates only
- No CEO/team language; no fake completeness claims
"""


@dataclass
class _PendingRefresh:
    user_id: str
    folder_id: str
    workspace_key: str
    snapshot: str
    live_fingerprint: str | None


Runner = Callable[[_PendingRefresh], Awaitable[object]]


async def build_workspace_explore_snapshot(
    backend: WorkspaceBackend | None,
    *,
    max_chars: int = _SNAPSHOT_MAX_CHARS,
) -> str:
    """Compact top-tree + key-manifest text for the refresh LLM. Best-effort."""
    if backend is None:
        return ""
    lines: list[str] = ["# Top-level"]
    try:
        entries = (await backend.list(".", "*")).entries
    except Exception:  # noqa: BLE001
        entries = []
    names: list[str] = []
    for entry in entries:
        name = (entry.path or "").strip().strip("/").split("/")[0]
        if not name or name.startswith("."):
            continue
        kind = "dir" if entry.is_dir else "file"
        names.append(f"- [{kind}] {name}")
    lines.extend(sorted(set(names)) or ["(empty)"])
    lines.append("")
    lines.append("# Key manifests")
    for path in _KEY_MANIFEST_CANDIDATES:
        try:
            content = await backend.read(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(content, str) or not content.strip():
            continue
        body = content.strip()
        if len(body) > _MANIFEST_BODY_CAP:
            body = body[: _MANIFEST_BODY_CAP - 1].rstrip() + "…"
        lines.append(f"## {path}")
        lines.append(body)
        lines.append("")
    text = "\n".join(lines).strip()
    if max_chars > 0 and len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _normalize_optional_md(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in ("null", "none"):
        return None
    return text


async def refresh_folder_explore_from_snapshot(
    *,
    user_id: str,
    folder_id: str,
    workspace_key: str,
    snapshot: str,
    live_fingerprint: str | None,
    provider: LLMProvider,
    model: str,
    store: MemoryStore | None = None,
) -> bool:
    """Run one refresh pass. Returns True when fingerprint closeout cleared dirty."""
    store = store or default_memory_store()
    if not folder_id or not (snapshot or "").strip():
        return False

    current_profile = await load_folder_profile(store, user_id, folder_id)
    if folder_profile_is_empty(current_profile):
        logger.info(
            "memory.explore_refresh_skip_empty_profile",
            user_id=user_id,
            folder_id=folder_id,
        )
        return False
    current_nav = await store.load(user_id, NAVIGATION_MEMORY_FILE, scope=folder_id)
    user_prompt = (
        f"# Workspace snapshot\n{snapshot.strip()}\n\n"
        f"# CURRENT folder 画像.md\n{current_profile.strip() or '(empty)'}\n\n"
        f"# CURRENT folder 导航.md\n{current_nav.strip() or '(empty)'}\n\n"
        "Produce the refresh JSON now."
    )

    from agentcore.llm.model_selection import build_selected_request, select_call
    from agentcore.llm.provider.protocol import LLMMessage

    request = build_selected_request(
        select_call("memory", model),
        [
            LLMMessage(role="system", content=_REFRESH_SYSTEM),
            LLMMessage(role="user", content=user_prompt),
        ],
        stream=False,
    )
    try:
        response = await asyncio.wait_for(
            provider.complete(request), timeout=_REFRESH_TIMEOUT_SECONDS
        )
    except TimeoutError:
        logger.warning(
            "memory.explore_refresh_timeout",
            user_id=user_id,
            folder_id=folder_id,
        )
        return False
    except Exception as e:  # noqa: BLE001
        from agentcore.core.errors import LLMAuthError

        if isinstance(e, LLMAuthError):
            # Must surface so ``run_background_llm`` can try user BYOK once.
            raise
        logger.warning(
            "memory.explore_refresh_llm_failed",
            user_id=user_id,
            folder_id=folder_id,
            error=str(e),
        )
        return False

    payload = _extract_json_object(response.content or "")
    if payload is None:
        logger.warning(
            "memory.explore_refresh_parse_failed",
            user_id=user_id,
            folder_id=folder_id,
        )
        return False

    profile_md = _normalize_optional_md(payload.get("profile"))
    nav_md = _normalize_optional_md(payload.get("navigation"))
    topics, topic_warnings = parse_explore_topics(
        payload.get("topics"), max_topics=MAX_EXPLORE_TOPICS
    )
    for warning in topic_warnings:
        logger.info(
            "memory.explore_refresh_topic_warning",
            user_id=user_id,
            folder_id=folder_id,
            warning=warning,
        )

    wrote = False
    if profile_md:
        ok, _, conflict = await write_folder_profile_cas(
            store=store,
            user_id=user_id,
            folder_id=folder_id,
            new_markdown=profile_md,
        )
        if ok and not conflict:
            wrote = True
        elif conflict:
            logger.warning(
                "memory.explore_refresh_profile_conflict",
                user_id=user_id,
                folder_id=folder_id,
            )
    if nav_md:
        path = await write_folder_navigation(
            store=store, user_id=user_id, folder_id=folder_id, markdown=nav_md
        )
        if path:
            wrote = True
    if topics:
        capped, cap_warnings = await filter_topics_by_scope_cap(
            store,
            user_id,
            folder_id,
            topics,
            max_topic_files=settings.memory_max_topic_files,
        )
        for warning in cap_warnings:
            logger.info(
                "memory.explore_refresh_topic_warning",
                user_id=user_id,
                folder_id=folder_id,
                warning=warning,
            )
        if capped:
            written = await write_folder_topics_replace(
                store=store, user_id=user_id, folder_id=folder_id, topics=capped
            )
            if written:
                wrote = True

    fp = (live_fingerprint or "").strip() or None
    key = (workspace_key or "").strip()
    if not key:
        return wrote
    await record_explore_closeout(
        store, user_id, folder_id, workspace_key=key, fingerprint=fp
    )
    logger.info(
        "memory.explore_refresh_done",
        user_id=user_id,
        folder_id=folder_id,
        wrote=wrote,
        fingerprint=fp,
    )
    return True


async def _default_refresh_runner(pending: _PendingRefresh) -> bool:
    from agentcore.billing.gate import BackgroundLlmResult, run_background_llm
    from agentcore.llm.credentials import LLMCredentials
    from agentcore.llm.factory import build_provider
    from agentcore.llm.resolve import resolve_turn_model as resolve_user_model

    async def _runner(credentials: LLMCredentials) -> bool:
        model = resolve_user_model(credentials)
        provider = build_provider(credentials, purpose="platform_internal")
        try:
            return await refresh_folder_explore_from_snapshot(
                user_id=pending.user_id,
                folder_id=pending.folder_id,
                workspace_key=pending.workspace_key,
                snapshot=pending.snapshot,
                live_fingerprint=pending.live_fingerprint,
                provider=provider,
                model=model,
            )
        finally:
            await provider.close()

    bg = await run_background_llm(pending.user_id, purpose="memory", runner=_runner)
    if not isinstance(bg, BackgroundLlmResult):
        logger.info(
            "memory.explore_refresh_skipped_no_credentials",
            user_id=pending.user_id,
            folder_id=pending.folder_id,
        )
        return False
    return bg.value


class ExploreRefreshScheduler:
    """Per-folder debounce + mutual exclusion for R1 explore refresh."""

    def __init__(self, *, idle_seconds: float, runner: Runner) -> None:
        self._idle = idle_seconds
        self._runner = runner
        self._timers: dict[str, asyncio.TimerHandle] = {}
        self._pending: dict[str, _PendingRefresh] = {}
        self._running: set[str] = set()
        self._rerun: set[str] = set()
        self._tasks: set[asyncio.Task] = set()

    def schedule(self, pending: _PendingRefresh) -> None:
        folder_id = pending.folder_id
        self._pending[folder_id] = pending
        if folder_id in self._running:
            self._rerun.add(folder_id)
            self._cancel_timer(folder_id)
            return
        self._cancel_timer(folder_id)
        loop = asyncio.get_running_loop()
        self._timers[folder_id] = loop.call_later(self._idle, self._fire, folder_id)

    def _fire(self, folder_id: str) -> None:
        self._cancel_timer(folder_id)
        pending = self._pending.pop(folder_id, None)
        if pending is None:
            return
        task = asyncio.ensure_future(self._run(folder_id, pending))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, folder_id: str, pending: _PendingRefresh) -> None:
        self._running.add(folder_id)
        try:
            with log_context(folder_id=folder_id, user_id=pending.user_id):
                await self._runner(pending)
        except Exception as e:  # noqa: BLE001 — never break user turns
            logger.warning(
                "memory.explore_refresh_run_failed",
                folder_id=folder_id,
                error=str(e),
            )
        finally:
            self._running.discard(folder_id)
            if folder_id in self._rerun:
                self._rerun.discard(folder_id)
                again = self._pending.get(folder_id)
                if again is not None:
                    loop = asyncio.get_running_loop()
                    self._cancel_timer(folder_id)
                    self._timers[folder_id] = loop.call_later(
                        self._idle, self._fire, folder_id
                    )

    def _cancel_timer(self, folder_id: str) -> None:
        timer = self._timers.pop(folder_id, None)
        if timer is not None:
            timer.cancel()

    async def shutdown(self) -> None:
        for timer in list(self._timers.values()):
            timer.cancel()
        self._timers.clear()
        self._pending.clear()
        self._rerun.clear()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)


_default_scheduler: ExploreRefreshScheduler | None = None


def get_explore_refresh_scheduler() -> ExploreRefreshScheduler:
    global _default_scheduler
    if _default_scheduler is None:
        _default_scheduler = ExploreRefreshScheduler(
            idle_seconds=settings.memory_explore_refresh_idle_seconds,
            runner=_default_refresh_runner,
        )
    return _default_scheduler


def schedule_explore_refresh(
    *,
    user_id: str,
    folder_id: str,
    workspace_key: str,
    snapshot: str,
    live_fingerprint: str | None = None,
) -> None:
    """Arm per-folder debounce for a dirty fingerprint (no-op when disabled)."""
    if not settings.memory_explore_refresh_enabled:
        return
    if not folder_id or not user_id:
        return
    if not (snapshot or "").strip():
        return
    get_explore_refresh_scheduler().schedule(
        _PendingRefresh(
            user_id=user_id,
            folder_id=folder_id,
            workspace_key=workspace_key or "",
            snapshot=snapshot,
            live_fingerprint=live_fingerprint,
        )
    )
    logger.info(
        "memory.explore_refresh_scheduled",
        user_id=user_id,
        folder_id=folder_id,
    )


async def shutdown_explore_refresh_scheduler() -> None:
    if _default_scheduler is not None:
        await _default_scheduler.shutdown()


__all__ = [
    "ExploreRefreshScheduler",
    "build_workspace_explore_snapshot",
    "get_explore_refresh_scheduler",
    "refresh_folder_explore_from_snapshot",
    "schedule_explore_refresh",
    "shutdown_explore_refresh_scheduler",
]
