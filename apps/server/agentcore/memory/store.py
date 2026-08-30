"""Long-term memory storage (folder- and scope-addressed).

Long-term memory is the markdown body of the user's `ai_maintained` rule file(s)
(see docs/03-AI核心/Agent记忆与知识系统.md §1.4 / §5.3). The cloud file-tree / Document
subsystem that will ultimately host these files does not exist yet, so the MVP backs
them with a per-user *folder* under the server data dir — one markdown file per memory
note, addressed by a path relative to that folder.

Phase 2 of「记忆文件夹化」(Agent记忆与知识系统 §1.4 / §5.3) adds two axes on top of
the phase-1 single-folder layout, both behind the same ``MemoryStore`` seam:

- **作用域 (scope)**: a note lives either GLOBAL (the user's cloud root — injected into
  every conversation) or under a FOLDER layer keyed by a manual sidebar ``folder_id``
  (injected only when the conversation is in that group — D4 方案 1). ``scope=None`` =
  global (the phase-1 behavior, unchanged → zero migration); ``scope=<folder_id>`` = that
  group's folder memory. 位置即作用域: complements §5.3, no manual switch.
- **偏好/画像 二分**: the always-injected core splits into ``PREFERENCES_MEMORY_FILE``
  (沟通/工作习惯, soft, GLOBAL-only) and ``CORE_MEMORY_FILE`` (技术栈/关于用户的事实, can be
  global OR folder). Different change-reasons → different files → different CAS.

Storage stays hidden behind ``MemoryStore`` so the eventual swap to the cloud file tree is
a one-liner (then folder memory = a folder's ``ai_maintained`` rule files, §5.4 终点形态).

``project_scopes`` / ``_PROJECT_CONTAINER`` keep the old spelling: the former is the
persisted REST + repository contract, the latter an on-disk directory name (双模式工作区 §5.4).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agentcore.core.logging import get_logger

logger = get_logger(__name__)


# A memory note's SCOPE (Agent记忆与知识系统 §1.4): ``None`` = global (the user's cloud root,
# injected into every conversation); a ``str`` is a ``folder_id`` = that manual group's
# folder scope (injected only when the conversation is in that sidebar group — D4 方案 1,
# folder-refactor-design §8). 位置即作用域 — no manual switch.
MemoryScope = str | None

# The always-injected PROFILE core (§四「偏好/画像 二分」): durable facts ABOUT the user —
# 技术栈与工具 + 关于用户的事实. Can be GLOBAL or FOLDER (a folder's facts: "本仓用 Rust").
CORE_MEMORY_FILE = "画像.md"

# The always-injected PREFERENCES core (§四): how to work WITH the user — 沟通偏好 + 工作习惯.
# Soft, occasionally-tuned, universal → GLOBAL-only (never copied into each folder, §二).
PREFERENCES_MEMORY_FILE = "偏好.md"

# Folder-only always-injected short entry (记忆 · 双层文件夹知识): one-line定位 + 任务路由.
# Not in ALWAYS_MEMORY_FILES — global layer has no 导航; folder inject appends after 画像.
NAVIGATION_MEMORY_FILE = "导航.md"

# The two always-injected GLOBAL core files, in stable injection order (preferences then profile):
# both ride every prompt's <设定>; order here is content-layer determinism (assembly places
# the block at SectionOrder.MEMORY — see runtime/context).
ALWAYS_MEMORY_FILES = (PREFERENCES_MEMORY_FILE, CORE_MEMORY_FILE)

# On-demand topic notes (§三 / §六): ``<scope>/主题/<slug>.md`` — procedural /
# folder knowledge the agent pulls only when relevant (vs the always-injected core).
TOPIC_DIR = "主题"

# Session-summary (episodic) layer historically lived under ``情景/<id>.md`` in the
# memory folder; it now uses ``memory_episodes`` (see ``memory/episode_store.py``).
# Path helpers remain for backfill of leftover document rows.
EPISODIC_DIR = "情景"

# Legacy per-scope JSON sidecar filename (now ``memory_scope_states`` table).
MEMORY_META_FILE = "_memory_meta.json"


def is_episodic_path(path: str) -> bool:
    """Whether ``path`` addresses an episodic session-summary note (under ``情景/``)."""
    return path.startswith(f"{EPISODIC_DIR}/")


def episodic_path(episode_id: str) -> str:
    """Relative path for one episodic summary file."""
    return f"{EPISODIC_DIR}/{episode_id}.md"

# Reserved subdir under a user's global folder that holds the FOLDER-scoped layers
# (``<user>/_folders/<folder_id>/…``). Leading underscore + ``_safe_segment`` keep it from
# ever colliding with a real note (core files are 偏好.md/画像.md; topics live under 主题/),
# and the global ``list`` skips it so folder notes never leak into the global layer.
_PROJECT_CONTAINER = "_folders"


def topic_path(slug: str) -> str:
    """The relative memory path for a topic note (``主题/<slug>.md``)."""
    return f"{TOPIC_DIR}/{slug}.md"


def is_topic_path(path: str) -> bool:
    """Whether ``path`` addresses an on-demand topic note (under ``主题/``)."""
    return path.startswith(f"{TOPIC_DIR}/")


def topic_slug(path: str) -> str:
    """The bare slug of a topic note path (``主题/部署.md`` → ``部署``)."""
    return path[len(TOPIC_DIR) + 1 :].removesuffix(".md")


_SEGMENT_SPLIT = re.compile(r"[\\/]+")


def _frontmatter_description(markdown: str) -> str:
    """A note's frontmatter ``description`` ("" when absent or unparseable)."""
    from agentcore.documents.frontmatter import FrontmatterError, parse_entry_frontmatter

    parsed = parse_entry_frontmatter(markdown)
    if isinstance(parsed, FrontmatterError):
        return ""
    return parsed.description.strip()


def memory_version(markdown: str) -> str:
    """A content-addressed version tag for a memory file (the editor's CAS baseline).

    A SHA-256 of the exact bytes, so it is store-agnostic (works the same once the
    file tree backs it) and stable: the same content always yields the same tag, so a
    write whose ``baseline`` still matches the current tag is safe, while a tag drift
    means the offline consolidation (or another device) changed the file underneath —
    the write reports a conflict instead of clobbering it. Empty body has its own
    stable tag, so a first write (baseline = the empty tag) is conflict-free. The tag
    is now per file: a manual edit of one note and an offline pass over another no
    longer share a single CAS baseline (§五「每文件 CAS」).
    """
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MemoryFileMeta:
    """One file in a user's memory folder: its relative path + per-file CAS tag.

    ``description`` / ``disputed`` are entry metadata the document-backed store carries
    alongside the body (the legacy file store has neither and leaves the defaults). They
    ride the meta so the on-demand catalog can be built from ONE listing — no per-note
    body load to guess a summary, and no second query to learn what the user disputed.
    """

    path: str  # relative to the user's memory folder, e.g. "画像.md"
    version: str  # per-file CAS = ``memory_version`` of the file's bytes
    # Frontmatter ``description`` — the retrieval summary the 按需目录 shows ("" = name only).
    description: str = ""
    # User marked this note wrong (纠错通道): still readable / editable, never injected.
    disputed: bool = False


class MemoryStore(Protocol):
    """Loads/saves the markdown files in one (user, scope) long-term-memory layer.

    Addressed by ``(user_id, path, scope)`` where ``path`` is relative to that scope's
    memory folder (e.g. ``"画像.md"``) and ``scope`` selects the layer: ``None`` = global
    (default → the phase-1 behavior, unchanged), a ``folder_id`` = that folder. ``load``
    of a missing file returns "" so callers never have to branch on existence; ``list`` of
    one scope never returns notes from another (global ``list`` skips the folder layers).
    """

    async def list(self, user_id: str, scope: MemoryScope = None) -> list[MemoryFileMeta]:
        """List one scope's memory files (empty when there are none yet)."""
        ...

    async def load(self, user_id: str, path: str, scope: MemoryScope = None) -> str:
        """Return one memory file's markdown in ``scope``, or "" if it does not exist."""
        ...

    async def save(self, user_id: str, path: str, markdown: str, scope: MemoryScope = None) -> None:
        """Persist one memory file's markdown in ``scope`` (creating the folder if needed)."""
        ...

    async def delete(self, user_id: str, path: str, scope: MemoryScope = None) -> None:
        """Delete one memory file in ``scope`` (no-op if it does not exist)."""
        ...

    async def project_scopes(self, user_id: str) -> list[str]:
        """List manual-group ``folder_id``s whose FOLDER layer holds any memory (editor rail)."""
        ...


class FileMemoryStore:
    """MVP MemoryStore: a per-(user, scope) folder of markdown files under a base directory.

    Layout (Agent记忆与知识系统 §1.4):
    - GLOBAL (``scope=None``): ``<base>/<user_id>/<path>`` — unchanged from phase 1, so
      existing memory IS the global layer (zero migration).
    - FOLDER (``scope=<folder_id>``): ``<base>/<user_id>/_folders/<folder_id>/<path>`` —
      nested under the reserved ``_PROJECT_CONTAINER`` so the global ``list`` (which skips
      that subdir) never returns a folder's notes, and each folder is isolated.

    File I/O is synchronous but the files are tiny (a few KB), so it runs inline. Failures
    are logged and degrade to empty / no-op so memory never breaks a turn.

    Migration: a user whose memory predates the folder layout has a flat
    ``<base>/<user_id>.md``. The first access migrates it into the GLOBAL ``画像.md`` — same
    bytes, so the CAS tag is unchanged and an in-flight editor baseline still matches —
    then removes the old file. Idempotent (skipped once the folder exists) and best-effort
    (any failure leaves the old file in place: degrade, never lose data). The 偏好/画像 split
    is left to organic re-routing (consolidation + the editor's combine/split), not a
    destructive batch pass — old preference sections in 画像.md keep being injected meanwhile.
    Migration is fully synchronous (no ``await``), so within the single-process async MVP it
    runs atomically and cannot interleave with another access.
    """

    def __init__(self, base_dir: str | Path) -> None:
        self._base = Path(base_dir)

    @staticmethod
    def _safe_segment(segment: str) -> str:
        """Neutralize traversal / separator injection in a single path component."""
        cleaned = segment.replace("/", "_").replace("\\", "_").replace("..", "_").strip()
        return cleaned or "_"

    def _user_dir(self, user_id: str) -> Path:
        # user_id is a server-issued UUID; still neutralize any path separators.
        return self._base / self._safe_segment(user_id)

    def _scope_dir(self, user_id: str, scope: MemoryScope) -> Path:
        """The root folder for one (user, scope) layer.

        Global = the user folder (phase-1 layout); a manual group = nested under the
        reserved container so it stays isolated and invisible to the global ``list``.
        ``scope`` is a user-created sidebar ``folder_id`` UUID, per-segment sanitized.
        """
        base = self._user_dir(user_id)
        if scope is None:
            return base
        return base / _PROJECT_CONTAINER / self._safe_segment(scope)

    def _path(self, user_id: str, rel: str, scope: MemoryScope = None) -> Path:
        # Sanitize every segment so a crafted path (.., absolute, separator injection)
        # can never escape the (user, scope) folder.
        target = self._scope_dir(user_id, scope)
        for part in _SEGMENT_SPLIT.split(rel):
            if part in ("", "."):
                continue
            target = target / self._safe_segment(part)
        return target

    async def list(self, user_id: str, scope: MemoryScope = None) -> list[MemoryFileMeta]:
        scope_dir = self._scope_dir(user_id, scope)
        if not scope_dir.exists():
            return []
        metas: list[MemoryFileMeta] = []
        try:
            for path in sorted(scope_dir.rglob("*.md")):
                if not path.is_file():
                    continue
                rel = path.relative_to(scope_dir).as_posix()
                # Global scope must not surface folder notes nested under the reserved
                # container; folder scopes are already rooted inside their own dir.
                if scope is None and rel.split("/", 1)[0] == _PROJECT_CONTAINER:
                    continue
                body = path.read_text(encoding="utf-8")
                metas.append(
                    MemoryFileMeta(
                        path=rel,
                        version=memory_version(body),
                        description=_frontmatter_description(body),
                    )
                )
        except OSError as e:
            logger.warning("memory.list_failed", user_id=user_id, error=str(e))
        return metas

    async def load(self, user_id: str, path: str, scope: MemoryScope = None) -> str:
        target = self._path(user_id, path, scope)
        try:
            return target.read_text(encoding="utf-8") if target.exists() else ""
        except OSError as e:
            logger.warning("memory.load_failed", user_id=user_id, error=str(e))
            return ""

    async def save(self, user_id: str, path: str, markdown: str, scope: MemoryScope = None) -> None:
        target = self._path(user_id, path, scope)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(markdown, encoding="utf-8")
        except OSError as e:
            logger.warning("memory.save_failed", user_id=user_id, error=str(e))

    async def delete(self, user_id: str, path: str, scope: MemoryScope = None) -> None:
        target = self._path(user_id, path, scope)
        try:
            target.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("memory.delete_failed", user_id=user_id, error=str(e))

    async def project_scopes(self, user_id: str) -> list[str]:
        """List ``folder_id``s whose FOLDER layer holds any memory file (editor rail §P2).

        Scans the reserved ``_folders`` container for subdirs holding ≥1 ``.md`` — i.e. the
        folders the offline consolidation has actually written memory for — so the「文件」
        page surfaces a「本文件夹记忆」node only where there IS something to edit. ``folder_id``
        is a server UUID, so ``_safe_segment`` is a no-op and the dir name IS the id.
        Degrades to [] on any I/O error (memory never breaks the page).
        """
        container = self._user_dir(user_id) / _PROJECT_CONTAINER
        if not container.exists():
            return []
        scopes: list[str] = []
        try:
            for child in sorted(container.iterdir()):
                if not child.is_dir():
                    continue
                # Episodic digests alone must not surface a「本文件夹记忆」editor node —
                # only semantic notes (核心 / 主题) count.
                has_semantic = any(
                    p.is_file() and EPISODIC_DIR not in p.relative_to(child).parts
                    for p in child.rglob("*.md")
                )
                if has_semantic:
                    scopes.append(child.name)
        except OSError as e:
            logger.warning("memory.project_scopes_failed", user_id=user_id, error=str(e))
        return scopes


def default_file_memory_store() -> FileMemoryStore:
    """The legacy file-backed store under ``<settings.data_dir>/memory``.

    Retained only as the SOURCE the one-time file→document migration copies from
    (``memory/migrate_documents.py``) and its tests — no longer the process default.
    """
    from agentcore.config import settings

    return FileMemoryStore(Path(settings.data_dir) / "memory")


def default_memory_store() -> MemoryStore:
    """The process default long-term-memory store: the Document-tree backing (§5.7 换底).

    Opens its own DB session per op — the right shape for background consolidation and the
    per-turn memory tools. Route handlers build a session-bound ``DocumentMemoryStore`` via DI
    (``api.dependencies.get_memory_store``) instead, so their read-compare-write runs inside the
    request transaction (and the integration test schema). Content-hash CAS is unchanged, so the
    editor / consolidation conflict semantics carry over from the file store verbatim.
    """
    from agentcore.memory.document_store import DocumentMemoryStore

    return DocumentMemoryStore()
