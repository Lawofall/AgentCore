"""Cold-start explore act — folder ``画像.md`` / ``导航.md`` + fingerprint meta.

Product exception to §1.5 (normally no mid-turn AI write of ``ai_maintained`` profile):
explore-act close-out may write the **folder** layer only. Orthogonal to consolidation
``_is_cold_start`` (global preferences+profile empty). See 编排器 · 冷启动探索幕 /
记忆 §1.5. Optional folder ``主题/<slug>.md`` whole-file replace (soft top 5 / call).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from typing import TYPE_CHECKING, Any

from agentcore.core.logging import get_logger
from agentcore.memory.locks import user_memory_lock
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    MemoryStore,
    is_topic_path,
    memory_version,
    topic_path,
    topic_slug,
)
from agentcore.memory.user_memory import (
    _MAX_TOPIC_SLUG_LEN,
    _MemoryDoc,
    _parse,
    _render,
    _Section,
    strip_memory_chrome,
)

if TYPE_CHECKING:
    from agentcore.workspace.protocol import WorkspaceBackend

logger = get_logger(__name__)

_MAX_CAS_RETRIES = 3
# Soft top per update_folder_profile call (T2): extras → warning, not hard reject.
MAX_EXPLORE_TOPICS = 5
_SLUG_ALLOWED_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,38}$")

# Top-tree + key-manifest fingerprint inputs (记忆 · 探索触发). Not commit-/day-gated.
_KEY_MANIFEST_CANDIDATES = (
    "README.md",
    "README",
    "readme.md",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "pnpm-workspace.yaml",
    "turbo.json",
    "nx.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "AGENTS.md",
    "CLAUDE.md",
)

# Named-refresh hard gate — allow-list substrings only (非意图分类器).
# Synonyms already listed in CEO prompt / 记忆 docs; bare「探索」omitted (too broad).
# 这两张表匹配的是**用户原话**，不是产品术语：容器已统一叫文件夹（双模式工作区 §5.4），
# 但「项目」仍是用户嘴里的常用词，删掉即等于让这批人的点名静默失效——故两种说法并存，
# 不是旧名 alias。
_NAMED_EXPLORE_REFRESH_PHRASES = (
    "先了解",
    "重新了解",
    "刷新文件夹记忆",
    "刷新项目记忆",
)

# Empty-profile + 工程点名 → hard explore-pending（非意图分类器；短允许表）.
# Bare empty profile alone is soft-hint only (不挡 delegate / 不置 pending).
_NAMED_FOLDER_WORK_PHRASES = (
    "继续开发",
    "改这个文件夹",
    "在这个文件夹",
    "摸清这个文件夹",
    "改这个项目",
    "在这个项目",
    "摸清这个项目",
    "全面摸底",
    "先摸仓",
)


def user_named_explore_refresh(user_message: str | None) -> bool:
    """True when user text hits an allow-listed refresh phrase (点名硬闸)."""
    text = (user_message or "").strip()
    if not text:
        return False
    return any(phrase in text for phrase in _NAMED_EXPLORE_REFRESH_PHRASES)


def user_named_folder_work(user_message: str | None) -> bool:
    """True when empty-profile should hard-gate (工程点名短语允许表)."""
    text = (user_message or "").strip()
    if not text:
        return False
    return any(phrase in text for phrase in _NAMED_FOLDER_WORK_PHRASES)


def resolve_hard_explore_reason(
    explore_reason: str | None,
    user_message: str | None,
) -> tuple[str | None, bool]:
    """Named-refresh + soft-empty downgrade (assemble / resume must stay identical).

    Returns ``(hard_reason, folder_profile_empty_soft)``.
    ``hard_reason`` is set for pending/write_scope=explore_memory; soft empty alone
    yields ``(None, True)`` so the request is not blocked.
    Named refresh (先了解 / 重新了解 / …) wins over empty-soft so a cleared 画像
    plus 点名 still hard-opens; rebind stays rebind even if the message also
    contains a refresh phrase.
    """
    soft_empty = False
    if user_named_explore_refresh(user_message) and (
        not explore_reason or explore_reason == "empty"
    ):
        explore_reason = "refresh"
    if explore_reason == "empty" and not user_named_folder_work(user_message):
        soft_empty = True
        explore_reason = None
    return explore_reason, soft_empty


def profile_has_substance(markdown: str | None) -> bool:
    """True when folder ``画像.md`` has real content (not chrome / empty headers only)."""
    raw = markdown or ""
    doc = _parse(raw)
    if any(b.strip() for s in doc.sections for b in s.bullets):
        return True
    body = strip_memory_chrome(raw).strip()
    if not body:
        return False
    # Freeform body with no ## sections still counts (rare hand-edit).
    for line in body.splitlines():
        text = line.strip()
        if not text or text.startswith("##"):
            continue
        return True
    return False


def folder_profile_is_empty(markdown: str | None) -> bool:
    """Inverse of :func:`profile_has_substance` — the explore-act「够用」skip probe."""
    return not profile_has_substance(markdown)


async def load_folder_profile(
    store: MemoryStore, user_id: str, folder_id: str
) -> str:
    """Load folder-layer ``画像.md`` ("" when missing)."""
    return await store.load(user_id, CORE_MEMORY_FILE, scope=folder_id)


def build_workspace_key(*, folder_id: str, binding: Any | None) -> str:
    """Stable workspace identity for explore-act 过期再探.

    Local: ``local:<root_id>:<subpath>``. Cloud (no binding): ``folder:<id>``.
    """
    if binding is not None:
        root_id = getattr(binding, "root_id", None) or ""
        subpath = getattr(binding, "subpath", None) or ""
        if root_id:
            return f"local:{root_id}:{subpath}"
    from agentcore.workspace.locate import format_workspace_id

    return format_workspace_id(folder_id=folder_id, conversation_id="")


def _looks_like_folder_uuid(folder_id: str) -> bool:
    """True when ``folder_id`` parses as a formal UUID (folders PK shape).

    Runtime ``folder_id`` may also be a memory scope string (tests / legacy);
    only UUID-shaped ids are eligible for a ``folders`` table lookup.
    """
    raw = (folder_id or "").strip()
    if not raw:
        return False
    try:
        uuid.UUID(raw)
    except ValueError:
        return False
    return True


def _is_db_data_error(exc: BaseException) -> bool:
    """True when ``exc`` (or cause / ``orig``) is a driver/SQLAlchemy data error.

    Covers asyncpg ``DataError`` (e.g. invalid UUID cast) wrapped as
    ``DBAPIError`` — must degrade like connectivity, never HARD-kill.
    """
    from sqlalchemy.exc import DataError

    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, DataError):
            return True
        mod = type(cur).__module__ or ""
        if type(cur).__name__ == "DataError" and mod.startswith(("asyncpg", "sqlalchemy")):
            return True
        orig = getattr(cur, "orig", None)
        if isinstance(orig, BaseException) and id(orig) not in seen:
            cur = orig
            continue
        cur = cur.__cause__ or cur.__context__
    return False


async def resolve_folder_workspace_key(
    folder_id: str,
    *,
    binding: Any | None = None,
    binding_injected: bool = False,
) -> str | None:
    """Resolve explore workspace identity for ``folder_id``.

    Prefer an injected local bind (sidecar/desktop ``localRootId`` /
    ``localSubpath``) — pure :func:`build_workspace_key`, no DB. When not
    injected:

    - Non-UUID ``folder_id`` (memory scope string) → **no** ``folders`` query;
      same as row miss → ``folder:<id>``.
    - Formal UUID → load the Folder row; miss → ``folder:<id>``.
    - DB connectivity / ``DataError`` (illegal cast, …) → ``None`` + warning.

    Never HARD-kills the turn over key resolution; never silently pretends
    ``folder:<id>`` is a verified *local* key when the DB was unreachable.
    Callers still run empty / named explore gates with an unknown key
    (``None`` → ``\"\"`` sentinel; skip rebind until bind is known).
    """
    if binding_injected:
        return build_workspace_key(folder_id=folder_id, binding=binding)

    # Memory-scope strings (F1 / test_birth / …) are legal folder_id values for
    # consult_memory but are not folders PKs — never send them through ::UUID.
    if not _looks_like_folder_uuid(folder_id):
        return build_workspace_key(folder_id=folder_id, binding=None)

    from agentcore.conversation.scratch import resolve_conversation_local_binding
    from agentcore.db.base import async_session_factory
    from agentcore.db.errors import DatabaseUnavailableError, is_db_connectivity_error
    from agentcore.db.repositories import FolderRepository

    try:
        async with async_session_factory() as session:
            folder = await FolderRepository(session).get_by_id_unscoped(folder_id)
            if not folder:
                return build_workspace_key(folder_id=folder_id, binding=None)
            resolved = resolve_conversation_local_binding(
                local_root_id=folder.local_root_id,
                local_subpath=folder.local_subpath,
                label=folder.name or "workspace",
            )
            return build_workspace_key(folder_id=folder_id, binding=resolved)
    except Exception as e:
        if (
            isinstance(e, DatabaseUnavailableError)
            or is_db_connectivity_error(e)
            or _is_db_data_error(e)
        ):
            logger.warning(
                "memory.explore_workspace_key_db_unavailable",
                folder_id=folder_id,
                error=str(e),
            )
            return None
        raise


async def load_explore_workspace_key(
    store, user_id: str, folder_id: str
) -> str | None:
    """Stored key from last explore-act write (``memory_scope_states``), if any."""
    from agentcore.memory.episode_store import default_episode_store
    from agentcore.memory.episodic import load_scope_meta

    ep = store if hasattr(store, "load_scope_meta") else default_episode_store()
    meta = await load_scope_meta(ep, user_id, scope=folder_id)
    return meta.explore_workspace_key


async def record_explore_workspace_key(
    store,
    user_id: str,
    folder_id: str,
    workspace_key: str,
) -> None:
    """Persist explore-act workspace identity (legacy helper; prefer close-out)."""
    await record_explore_closeout(
        store, user_id, folder_id, workspace_key=workspace_key, fingerprint=None
    )


async def record_explore_closeout(
    store,
    user_id: str,
    folder_id: str,
    *,
    workspace_key: str,
    fingerprint: str | None = None,
) -> None:
    """Persist workspace key + optional fingerprint; clear R2 dirty on successful explore."""
    from agentcore.memory.episode_store import default_episode_store
    from agentcore.memory.episodic import load_scope_meta, save_scope_meta

    ep = store if hasattr(store, "load_scope_meta") else default_episode_store()
    key = (workspace_key or "").strip()
    fp = (fingerprint or "").strip() or None
    if not key and not fp:
        return
    async with user_memory_lock(user_id):
        meta = await load_scope_meta(ep, user_id, scope=folder_id)
        changed = False
        if key and meta.explore_workspace_key != key:
            meta.explore_workspace_key = key
            changed = True
        if fp and meta.explore_fingerprint != fp:
            meta.explore_fingerprint = fp
            changed = True
        if meta.explore_fingerprint_dirty:
            meta.explore_fingerprint_dirty = False
            changed = True
        if not changed:
            return
        await save_scope_meta(ep, user_id, meta, scope=folder_id)
        logger.info(
            "memory.explore_closeout_meta_written",
            user_id=user_id,
            folder_id=folder_id,
            workspace_key=meta.explore_workspace_key,
            fingerprint=meta.explore_fingerprint,
        )


async def compute_workspace_explore_fingerprint(
    backend: WorkspaceBackend | None,
) -> str | None:
    """Hash of top-level tree names + key-manifest content digests. Best-effort."""
    if backend is None:
        return None
    top_names: list[str] = []
    try:
        entries = (await backend.list(".", "*")).entries
    except Exception:  # noqa: BLE001 - fingerprint must never break a turn
        entries = []
    for entry in entries:
        name = (entry.path or "").strip().strip("/").split("/")[0]
        if not name or name.startswith("."):
            continue
        top_names.append(f"{'d' if entry.is_dir else 'f'}:{name}")
    top_names = sorted(set(top_names))

    manifest_lines: list[str] = []
    for path in _KEY_MANIFEST_CANDIDATES:
        try:
            content = await backend.read(path)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(content, str):
            continue
        dig = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()[:16]
        manifest_lines.append(f"{path}:{dig}")
    manifest_lines.sort()

    if not top_names and not manifest_lines:
        return None
    payload = "top\n" + "\n".join(top_names) + "\nmanifests\n" + "\n".join(manifest_lines)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


async def evaluate_explore_fingerprint_drift(
    store,
    user_id: str,
    folder_id: str,
    *,
    live_fingerprint: str | None,
    current_workspace_key: str | None = None,
) -> bool:
    """R2: mark dirty + return True when soft-hint should inject (never blocks).

    Same-binding fingerprint change → dirty. Rebind is owned by explore_reason; skipped here.
    Matching fingerprint clears dirty. No stored fingerprint → no soft hint (legacy).
    """
    from agentcore.memory.episode_store import default_episode_store
    from agentcore.memory.episodic import load_scope_meta, save_scope_meta

    ep = store if hasattr(store, "load_scope_meta") else default_episode_store()
    meta = await load_scope_meta(ep, user_id, scope=folder_id)
    stored_key = meta.explore_workspace_key
    if stored_key and current_workspace_key and stored_key != current_workspace_key:
        return False
    stored_fp = meta.explore_fingerprint
    if not stored_fp:
        return False
    if not live_fingerprint:
        return bool(meta.explore_fingerprint_dirty)

    drifted = live_fingerprint != stored_fp
    if drifted == meta.explore_fingerprint_dirty:
        return drifted

    async with user_memory_lock(user_id):
        meta = await load_scope_meta(ep, user_id, scope=folder_id)
        if meta.explore_fingerprint_dirty != drifted:
            meta.explore_fingerprint_dirty = drifted
            await save_scope_meta(ep, user_id, meta, scope=folder_id)
            logger.info(
                "memory.explore_fingerprint_dirty",
                user_id=user_id,
                folder_id=folder_id,
                dirty=drifted,
            )
    return drifted


async def load_explore_fingerprint(
    store, user_id: str, folder_id: str
) -> str | None:
    from agentcore.memory.episode_store import default_episode_store
    from agentcore.memory.episodic import load_scope_meta

    ep = store if hasattr(store, "load_scope_meta") else default_episode_store()
    meta = await load_scope_meta(ep, user_id, scope=folder_id)
    return meta.explore_fingerprint


async def folder_profile_explore_reason(
    store: MemoryStore,
    user_id: str,
    folder_id: str | None,
    *,
    current_workspace_key: str | None = None,
) -> str | None:
    """Auto-explore gate: ``\"empty\"`` | ``\"rebind\"`` | ``None``.

    Does **not** judge chitchat vs substance (prompt/routing). Bare chat never
    explores. Missing stored key on a non-empty profile → no hard rebind (legacy).
    Named refresh (``\"refresh\"``) is layered in assemble via
    :func:`user_named_explore_refresh` — not returned here.
    """
    if not folder_id:
        return None
    current = await load_folder_profile(store, user_id, folder_id)
    if folder_profile_is_empty(current):
        return "empty"
    stored = await load_explore_workspace_key(store, user_id, folder_id)
    if not stored:
        return None
    live = current_workspace_key
    if live is None:
        live = await resolve_folder_workspace_key(folder_id)
    if live and live != stored:
        return "rebind"
    return None


async def folder_profile_needs_explore(
    store: MemoryStore,
    user_id: str,
    folder_id: str | None,
    *,
    current_workspace_key: str | None = None,
) -> bool:
    """True when auto-explore should inject (empty profile or workspace rebind)."""
    reason = await folder_profile_explore_reason(
        store,
        user_id,
        folder_id,
        current_workspace_key=current_workspace_key,
    )
    return reason is not None


def merge_profile_by_sections(old_md: str, new_md: str) -> str:
    """Section-anchored merge for explore-act writes (定案 §三).

    - Sections present in ``new_md`` with substance → replace that section's body.
    - Sections only in ``old_md`` (or empty in new) → keep old body.
    - Bootstrap when old is empty: render new (retired 用户记忆 chrome dropped).
    - Never wipe the whole file down to a few empty lines when old had content.
    """
    if not (new_md or "").strip():
        return old_md or ""
    if folder_profile_is_empty(old_md):
        return _render(_parse(new_md))

    old_doc = _parse(old_md)
    new_doc = _parse(new_md)
    merged = _MemoryDoc(preamble=old_doc.preamble, sections=[])

    {_normalize_section(s.name): s for s in old_doc.sections}
    new_by = {_normalize_section(s.name): s for s in new_doc.sections}
    # Preserve old section order; append brand-new sections from new.
    seen: set[str] = set()
    for section in old_doc.sections:
        key = _normalize_section(section.name)
        seen.add(key)
        incoming = new_by.get(key)
        if incoming is not None and _section_has_substance(incoming):
            merged.sections.append(
                _Section(name=incoming.name.strip() or section.name, bullets=list(incoming.bullets))
            )
        else:
            merged.sections.append(
                _Section(name=section.name, bullets=list(section.bullets))
            )
    for section in new_doc.sections:
        key = _normalize_section(section.name)
        if key in seen:
            continue
        if _section_has_substance(section):
            merged.sections.append(
                _Section(name=section.name.strip(), bullets=list(section.bullets))
            )
            seen.add(key)
    return _render(merged)


def _normalize_section(name: str) -> str:
    return " ".join((name or "").split()).casefold()


def _section_has_substance(section: _Section) -> bool:
    return any(b.strip() for b in section.bullets)


async def write_folder_profile_cas(
    *,
    store: MemoryStore,
    user_id: str,
    folder_id: str,
    new_markdown: str,
    baseline: str | None = None,
) -> tuple[bool, str, bool]:
    """Merge-write folder ``画像.md`` under the per-user memory lock (CAS + retry).

    Returns ``(ok, resulting_markdown, conflict)``.
    ``conflict=True`` when a caller-supplied ``baseline`` no longer matches after retries.
    """
    if not folder_id:
        raise ValueError("folder_id required for folder profile write")
    if folder_profile_is_empty(new_markdown):
        return False, "", False

    async with user_memory_lock(user_id):
        for attempt in range(_MAX_CAS_RETRIES):
            current = await store.load(user_id, CORE_MEMORY_FILE, scope=folder_id)
            current_ver = memory_version(current)
            if baseline is not None and baseline != current_ver:
                if attempt + 1 < _MAX_CAS_RETRIES:
                    # Stale baseline: re-merge against live content (consolidation-style).
                    baseline = current_ver
                    continue
                return False, current, True
            merged = merge_profile_by_sections(current, new_markdown)
            if folder_profile_is_empty(merged):
                return False, current, False
            if merged == current:
                return True, current, False
            await store.save(user_id, CORE_MEMORY_FILE, merged, scope=folder_id)
            logger.info(
                "memory.explore_profile_written",
                user_id=user_id,
                folder_id=folder_id,
                chars=len(merged),
            )
            return True, merged, False
    return False, "", True


def normalize_explore_topic_slug(raw: str | None) -> str | None:
    """Safe explore-act topic slug (short ASCII id) or None if unusable."""
    text = (raw or "").strip()
    if not text:
        return None
    # Reject path-ish input before stripping (defence in depth).
    if "/" in text or "\\" in text or ".." in text:
        return None
    slug = text.removesuffix(".md").strip().casefold()
    if not slug or len(slug) > _MAX_TOPIC_SLUG_LEN:
        return None
    if not _SLUG_ALLOWED_RE.match(slug):
        return None
    return slug


def parse_explore_topics(
    raw_topics: object, *, max_topics: int = MAX_EXPLORE_TOPICS
) -> tuple[list[tuple[str, str]], list[str]]:
    """Parse tool ``topics`` arg → ``([(slug, content), ...], warnings)``.

    Drops empty/invalid entries; caps at ``max_topics`` (extras → warning, not written).
    """
    if raw_topics is None:
        return [], []
    if not isinstance(raw_topics, list):
        return [], ["topics 须为数组，已忽略"]
    warnings: list[str] = []
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in raw_topics:
        if len(out) >= max_topics:
            warnings.append(f"主题超过 {max_topics} 个，多余未写入（压回画像摘要）")
            break
        if not isinstance(item, dict):
            warnings.append("跳过非对象 topics 项")
            continue
        slug = normalize_explore_topic_slug(str(item.get("slug") or ""))
        content = str(item.get("content") or "").strip()
        if slug is None:
            warnings.append("跳过非法 slug")
            continue
        if not content:
            warnings.append(f"跳过空主题 {slug}")
            continue
        if slug in seen:
            warnings.append(f"重复 slug {slug}，后者覆盖前者")
            out = [(s, c) for s, c in out if s != slug]
        seen.add(slug)
        out.append((slug, content))
    return out, warnings


async def write_folder_topics_replace(
    *,
    store: MemoryStore,
    user_id: str,
    folder_id: str,
    topics: list[tuple[str, str]],
) -> list[str]:
    """Whole-file replace folder ``主题/<slug>.md`` notes (explore-act close-out).

    Returns list of written paths (``主题/<slug>.md``). Empty ``topics`` → no-op.
    """
    if not folder_id:
        raise ValueError("folder_id required for folder topic write")
    if not topics:
        return []
    written: list[str] = []
    async with user_memory_lock(user_id):
        for slug, content in topics:
            path = topic_path(slug)
            await store.save(user_id, path, content.strip() + "\n", scope=folder_id)
            written.append(path)
            logger.info(
                "memory.explore_topic_written",
                user_id=user_id,
                folder_id=folder_id,
                slug=slug,
                chars=len(content),
            )
    return written


async def write_folder_navigation(
    *,
    store: MemoryStore,
    user_id: str,
    folder_id: str,
    markdown: str,
) -> str | None:
    """Whole-file replace folder ``导航.md`` (short always entry). Empty → no-op."""
    if not folder_id:
        raise ValueError("folder_id required for folder navigation write")
    body = (markdown or "").strip()
    if not body:
        return None
    text = body + "\n"
    async with user_memory_lock(user_id):
        await store.save(user_id, NAVIGATION_MEMORY_FILE, text, scope=folder_id)
        logger.info(
            "memory.explore_navigation_written",
            user_id=user_id,
            folder_id=folder_id,
            chars=len(text),
        )
    return NAVIGATION_MEMORY_FILE


async def filter_topics_by_scope_cap(
    store: MemoryStore,
    user_id: str,
    folder_id: str,
    topics: list[tuple[str, str]],
    *,
    max_topic_files: int,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Keep replacements; admit new slugs only while under ``max_topic_files``."""
    if not topics or max_topic_files <= 0:
        return topics, []
    existing_slugs: set[str] = set()
    for meta in await store.list(user_id, scope=folder_id):
        if is_topic_path(meta.path):
            existing_slugs.add(topic_slug(meta.path))
    kept: list[tuple[str, str]] = []
    warnings: list[str] = []
    new_count = 0
    room = max(0, max_topic_files - len(existing_slugs))
    for slug, content in topics:
        if slug in existing_slugs:
            kept.append((slug, content))
            continue
        if new_count >= room:
            warnings.append(
                f"主题总数已达上限 {max_topic_files}，跳过新主题 {slug}"
            )
            continue
        kept.append((slug, content))
        new_count += 1
        existing_slugs.add(slug)
    return kept, warnings
