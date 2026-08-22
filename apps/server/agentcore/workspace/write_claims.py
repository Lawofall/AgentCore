"""File ownership ledger (C3 · 较强文件归属 / 交接式写权).

One :class:`WriteCoordinator` per coordination ``execution_id`` (session
authority, snapshotted). **交接式写权**：

  - **Dispatch ``declare``** claims a free path; does **not** steal from an
    ancestor holder (downstream only records intent via plan artifacts). Nested
    lead→child drives opt into declare-time handoff explicitly. Cross-wave:
    declaring a path whose holder is already in ``completed_run_ids`` auto-
    transfers at dispatch (reviser after writer). ``ended_owners`` (ledger bypass,
    **not** progress ``completed_run_ids``) is treated the same for declare/claim.
  - **Write ``claim``** may hand off from an ancestor (downstream consolidates)
    or from an ``ended_owners`` holder (nested terminal ghost-lock fix).
  - **Completion handoff** moves owned paths to the unique dependent that listed
    the same artifact.
  - Explicit transfer: ``replaces_run_id`` / ``continue_from_run_id`` / ``force`` /
    ``resolve_escalation(transfer_ownership=true)`` / user structured裁决
    (user ownership card **only** when lock owner is still ``running``;
    completed/ended holders use same-seat replaces / declare·claim handoff —
    never NL「移交写权」).
  - Write-time ``claim`` refusals are remembered per refused run so escalate can
    attach ``ownership_paths`` even when the model paraphrases the conflict.

  Write tools consult the same book (``str_replace`` / ``write_section`` /
  delete / move included). Write ancestors = plan ``depends_on`` closure ∪
  nested ``parent_run_id``. Non-coordination batches still get a batch-local
  ledger for intra-batch mutual exclusion.

Ledger keys are **desk × rel_path** (``target_folder_id or session birth desk``).
Same relative path on different desks does not collide; same desk still hard-
rejects. User-facing conflict text / escalate paths stay **bare** ``rel_path``.

``code_execute`` workspace write-back is **not** hard-gated this period — only
observable; do not route it through :meth:`claim` without an explicit follow-up.

→ 见设计: docs/03-AI核心/Agent协作模式.md §三
"""

from __future__ import annotations

from posixpath import normpath
from typing import Any

# Composite ownership key: desk_id + SEP + rel_path.
# Must be Postgres/JSONB-safe (NUL ``\\x00`` is rejected by text/JSONB and used to
# break ``coordination_snapshot`` journal appends). ASCII Unit Separator is absent
# from desk UUIDs and normalized POSIX rel_paths.
OWNERSHIP_KEY_SEP = "\x1f"
# Migrate / bare-chat unknown desk — only collides with the same sentinel.
# Must NOT contain OWNERSHIP_KEY_SEP (would break split_ownership_key).
LEGACY_DESK_SENTINEL = "__legacy_desk__"


def _normalize(path: str) -> str:
    """Canonical key for a workspace-relative path so ``a/b``, ``./a/b`` and ``a//b``
    collide on one owner. POSIX separators, collapsed, leading slashes stripped; case
    is preserved (the server filesystem is case-sensitive). ``..`` traversal is left to
    the backend's own guard — this only needs a stable key, not a safe path."""
    return normpath((path or "").strip().replace("\\", "/")).lstrip("/")


def normalize_ownership_path(path: str) -> str:
    """Public alias for bare rel_path normalization (display / artifact lists)."""
    return _normalize(path)


def resolve_ownership_desk(
    *,
    target_folder_id: str | None = None,
    birth_desk_id: str | None = None,
) -> str:
    """Effective desk for ledger keys: ``target_folder_id or session birth``.

    Empty → :data:`LEGACY_DESK_SENTINEL` (migrate / no-desk stubs only). Never
    invents the product string ``\"default\"``. Non-str values (e.g. test mocks)
    are ignored.
    """
    for candidate in (target_folder_id, birth_desk_id):
        if not isinstance(candidate, str):
            continue
        desk = candidate.strip()
        if desk:
            return desk
    return LEGACY_DESK_SENTINEL


def make_ownership_key(desk_id: str | None, rel_path: str) -> str:
    """Composite ledger key ``desk × path``. Empty path → ``\"\"``."""
    path = _normalize(rel_path)
    if not path:
        return ""
    desk = (
        desk_id.strip()
        if isinstance(desk_id, str) and desk_id.strip()
        else LEGACY_DESK_SENTINEL
    )
    return f"{desk}{OWNERSHIP_KEY_SEP}{path}"


def split_ownership_key(key: str) -> tuple[str, str]:
    """Split composite key → ``(desk_id, rel_path)``. Bare legacy → sentinel + path."""
    raw = key or ""
    if OWNERSHIP_KEY_SEP in raw:
        desk, _, path = raw.partition(OWNERSHIP_KEY_SEP)
        return desk or LEGACY_DESK_SENTINEL, path
    return LEGACY_DESK_SENTINEL, _normalize(raw)


def ownership_display_path(key_or_path: str) -> str:
    """Bare ``rel_path`` for user-facing messages / escalate (strip desk)."""
    if OWNERSHIP_KEY_SEP in (key_or_path or ""):
        return split_ownership_key(key_or_path)[1]
    return _normalize(key_or_path)


def ownership_conflict_message(
    path: str,
    owner_run_id: str,
    *,
    owner_role: str | None = None,
    ownership_kind: str | None = None,
    owner_status: str | None = None,
) -> str:
    """Guiding refusal: name the owner; do **not** push renaming the final deliverable.

    ``path`` must be the bare ``rel_path`` (never the composite ledger key).

    ``ownership_kind``: ``\"declared\"`` (dispatch reserve, file may be empty/missing) or
    ``\"written\"`` (successful write recorded). ``owner_status``: ``running`` /
    ``completed`` / ``ended`` / ``unknown``.

    Completed / ended / unknown holders are **not** a user「移交写权」path — same-seat
    ``replaces`` / dispatch ``declare`` (and ended ``claim``) auto-handoff is the fix.
    User transfer card is for still-running lock owners only.
    """
    display = ownership_display_path(path)
    who = f"【{owner_role}】（`{owner_run_id}`）" if owner_role else f"`{owner_run_id}`"
    kind_bit = ""
    if ownership_kind == "declared":
        kind_bit = "（仅派发占位、尚未落盘——不是上一 run 残留锁）"
    elif ownership_kind == "written":
        kind_bit = "（锁主已成功写入过该路径）"
    if owner_status in ("completed", "ended"):
        done_label = "已完成" if owner_status == "completed" else "已结束"
        return (
            f"写入冲突：`{display}` 仍记在{done_label}队友 {who} 名下{kind_bit}。"
            f"锁主状态：{done_label}（账本仍记其名；同座续派 / 派发 declare "
            "同 artifact 会自动接手）。"
            "请改写你自己职责下的文件，或等主管用同座位 replan/append"
            "（系统 auto-replaces）接手后再写；"
            "不要 escalate 请用户点「移交写权」，也不要另起同名终稿文件名抢写。"
        )
    if owner_status == "unknown":
        return (
            f"写入冲突：`{display}` 已归队友 {who} 负责{kind_bit}。"
            "锁主状态：未知（批内账本或嵌套会话未对齐）。"
            "请改写你自己职责下的文件，或等主管用同座位 replan/append"
            "（系统 auto-replaces）接手后再写；"
            "不要 escalate 请用户点「移交写权」，也不要另起同名终稿文件名抢写。"
        )
    status_bit = ""
    if owner_status == "running":
        status_bit = "锁主状态：进行中。"
    return (
        f"写入冲突：`{display}` 已归队友 {who} 负责{kind_bit}。"
        f"{status_bit}"
        "请改写你自己职责下的文件，或等待其整合完成；"
        "若需接手该路径：escalate 后用户卡可点「移交写权」，或由主管 "
        "resolve_escalation(..., transfer_ownership=true, paths=[本路径])；"
        "不要另起同名终稿文件名抢写。"
    )


class WriteCoordinator:
    """Owning run_id per desk×path key (batch-local or session-shared).

    Holds no async state and no lock — every method is synchronous, relying on the
    single-threaded event loop for atomicity (claim/declare before the awaited write).
    """

    def __init__(
        self,
        owners: dict[str, str] | None = None,
        *,
        written: set[str] | frozenset[str] | None = None,
        ended_owners: set[str] | frozenset[str] | None = None,
    ) -> None:
        # composite key (desk\\x1fpath) -> run_id；构造时可传裸 path（→ legacy 哨兵）
        self._owner: dict[str, str] = {}
        for p, rid in (owners or {}).items():
            key = self._coerce_key(p)
            if key and rid:
                self._owner[key] = str(rid)
        self._written: set[str] = set()
        for p in written or ():
            key = self._coerce_key(p)
            if key:
                self._written.add(key)
        self._ended: set[str] = {
            str(x).strip() for x in (ended_owners or ()) if str(x).strip()
        }
        # Turn-local: run_id -> ordered bare rel_paths refused on ``claim``.
        self._denied: dict[str, list[str]] = {}

    @staticmethod
    def _coerce_key(path_or_key: str, *, desk_id: str | None = None) -> str:
        raw = path_or_key or ""
        if OWNERSHIP_KEY_SEP in raw:
            desk, path = split_ownership_key(raw)
            return make_ownership_key(desk, path)
        if desk_id is not None:
            return make_ownership_key(desk_id, raw)
        # Explicit None desk_id omitted → legacy sentinel (unit tests / migrate).
        return make_ownership_key(None, raw)

    def _keys_for_rel(
        self,
        path: str,
        *,
        desk_id: str | None = None,
        create: bool = False,
    ) -> list[str]:
        """Resolve ledger key(s) for a bare path or composite key."""
        raw = path or ""
        if OWNERSHIP_KEY_SEP in raw:
            key = self._coerce_key(raw)
            return [key] if key else []
        rel = _normalize(raw)
        if not rel:
            return []
        if desk_id is not None:
            key = make_ownership_key(desk_id, rel)
            return [key] if key else []
        # desk omitted: prefer sentinel (compat), else cross-desk matches.
        sentinel = make_ownership_key(None, rel)
        if sentinel in self._owner or sentinel in self._written or create:
            return [sentinel]
        matches = [
            k
            for k in set(self._owner) | set(self._written)
            if ownership_display_path(k) == rel
        ]
        if matches:
            return matches
        return [sentinel] if create else []
    def to_dict(self) -> dict[str, Any]:
        """Snapshot: v3 nested ``{owners, written, ended}`` with desk×path keys."""
        payload: dict[str, Any] = {
            "_v": 3,
            "owners": dict(self._owner),
            "written": sorted(self._written),
        }
        if self._ended:
            payload["ended"] = sorted(self._ended)
        return payload

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
        *,
        birth_desk_id: str | None = None,
        run_target_folder_ids: dict[str, str | None] | None = None,
    ) -> WriteCoordinator:
        if not data or not isinstance(data, dict):
            return cls()
        ver = data.get("_v")
        if ver == 3:
            raw_owners = data.get("owners") or {}
            owners = {
                str(k): str(v)
                for k, v in raw_owners.items()
                if isinstance(k, str) and v is not None and str(v).strip()
            }
            raw_written = data.get("written") or []
            written = {
                str(p) for p in raw_written if isinstance(p, str) and str(p).strip()
            }
            raw_ended = data.get("ended") or []
            ended = {
                str(x).strip()
                for x in raw_ended
                if isinstance(x, str) and str(x).strip()
            }
            return cls(owners, written=written, ended_owners=ended)

        # v≤2 / nested owners / legacy flat → lazy migrate to desk×path.
        if ver == 2 or isinstance(data.get("owners"), dict):
            raw_owners = data.get("owners") or {}
            flat_owners = {
                str(k): str(v)
                for k, v in raw_owners.items()
                if isinstance(k, str) and v is not None and str(v).strip()
            }
            raw_written = data.get("written") or []
            flat_written = {
                str(p) for p in raw_written if isinstance(p, str) and str(p).strip()
            }
            raw_ended = data.get("ended") or []
            ended = {
                str(x).strip()
                for x in raw_ended
                if isinstance(x, str) and str(x).strip()
            }
        else:
            # Legacy flat path → owner.
            flat_owners = {
                str(k): str(v)
                for k, v in data.items()
                if isinstance(k, str) and k != "_v" and v is not None and str(v).strip()
            }
            flat_written = set()
            ended = set()

        run_desks = run_target_folder_ids or {}
        migrated_owners: dict[str, str] = {}
        for path, rid in flat_owners.items():
            if OWNERSHIP_KEY_SEP in path:
                migrated_owners[path] = rid
                continue
            desk = resolve_ownership_desk(
                target_folder_id=run_desks.get(rid),
                birth_desk_id=birth_desk_id,
            )
            key = make_ownership_key(desk, path)
            if key:
                migrated_owners[key] = rid
        migrated_written: set[str] = set()
        for path in flat_written:
            if OWNERSHIP_KEY_SEP in path:
                migrated_written.add(path)
                continue
            # Prefer desk of current owner when known; else birth / sentinel.
            owner = flat_owners.get(path)
            desk = resolve_ownership_desk(
                target_folder_id=run_desks.get(owner) if owner else None,
                birth_desk_id=birth_desk_id,
            )
            key = make_ownership_key(desk, path)
            if key:
                migrated_written.add(key)
        return cls(migrated_owners, written=migrated_written, ended_owners=ended)

    def owner_of(self, path: str, *, desk_id: str | None = None) -> str | None:
        keys = self._keys_for_rel(path, desk_id=desk_id, create=False)
        if desk_id is not None:
            key = make_ownership_key(desk_id, path)
            return self._owner.get(key)
        if not keys:
            # Compat: bare path under sentinel when nothing else matched.
            return self._owner.get(make_ownership_key(None, path))
        if len(keys) == 1:
            return self._owner.get(keys[0])
        # Ambiguous multi-desk: no single owner.
        owners = {self._owner[k] for k in keys if k in self._owner}
        if len(owners) == 1:
            return next(iter(owners))
        return None

    def is_written(self, path: str, *, desk_id: str | None = None) -> bool:
        if desk_id is not None:
            key = make_ownership_key(desk_id, path)
            return bool(key) and key in self._written
        keys = self._keys_for_rel(path, desk_id=None, create=False)
        if not keys:
            key = make_ownership_key(None, path)
            return bool(key) and key in self._written
        return any(k in self._written for k in keys)

    def mark_written(self, path: str, *, desk_id: str | None = None) -> None:
        if desk_id is not None:
            key = make_ownership_key(desk_id, path)
        elif OWNERSHIP_KEY_SEP in (path or ""):
            key = self._coerce_key(path)
        else:
            keys = self._keys_for_rel(path, desk_id=None, create=True)
            key = keys[0] if keys else make_ownership_key(None, path)
        if key:
            self._written.add(key)

    def owned_paths(self, run_id: str) -> list[str]:
        """Bare rel_paths owned by ``run_id`` (user-facing / handoff display)."""
        rid = (run_id or "").strip()
        if not rid:
            return []
        return sorted(
            {
                ownership_display_path(p)
                for p, owner in self._owner.items()
                if owner == rid
            }
        )

    def owned_keys(self, run_id: str) -> list[str]:
        """Composite ledger keys owned by ``run_id`` (handoff / transfer)."""
        rid = (run_id or "").strip()
        if not rid:
            return []
        return sorted(p for p, owner in self._owner.items() if owner == rid)

    def mark_ended(self, run_id: str) -> None:
        """Record a terminal lock-holder for declare/claim handoff (not progress)."""
        rid = (run_id or "").strip()
        if rid:
            self._ended.add(rid)

    def is_ended(self, run_id: str) -> bool:
        rid = (run_id or "").strip()
        return bool(rid) and rid in self._ended

    @property
    def ended_owners(self) -> set[str]:
        return set(self._ended)

    def record_denied(self, path: str, run_id: str) -> None:
        """Remember a write-time refusal so escalate can attach ``ownership_paths``."""
        bare = ownership_display_path(path)
        rid = (run_id or "").strip()
        if not bare or not rid:
            return
        bucket = self._denied.setdefault(rid, [])
        if bare not in bucket:
            bucket.append(bare)

    def denied_paths_for(self, run_id: str) -> list[str]:
        """Ordered bare paths this run was refused on; empty when unknown / never denied."""
        rid = (run_id or "").strip()
        if not rid:
            return []
        return list(self._denied.get(rid) or ())

    def claim(
        self,
        path: str,
        run_id: str,
        ancestors: frozenset[str],
        *,
        force: bool = False,
        desk_id: str | None = None,
    ) -> str | None:
        """Try to record ``run_id`` as the writer of ``path`` (write-time).

        Returns ``None`` when the write may proceed — unclaimed, already owned by
        ``run_id``, owned by an ancestor (write-time handoff), or ``force`` — and
        records ownership. Returns the conflicting owner's run_id (leaving
        ownership untouched unless ``force``) when an unrelated peer already holds it.
        """
        key = make_ownership_key(desk_id, path)
        if not key:
            return None
        rid = (run_id or "").strip() or "unknown"
        owner = self._owner.get(key)
        if owner is not None and owner != rid and owner not in ancestors and not force:
            if owner in self._ended:
                self._owner[key] = rid
                return None
            self.record_denied(path, rid)
            return owner
        self._owner[key] = rid
        return None

    def declare(
        self,
        path: str,
        run_id: str,
        ancestors: frozenset[str],
        *,
        force: bool = False,
        allow_ancestor_handoff: bool = False,
        desk_id: str | None = None,
    ) -> str | None:
        """Dispatch-time reserve — does **not** steal from an ancestor by default.

        Free path → become holder. Same run → ok. Ancestor already holds → keep
        ancestor (downstream intent only) unless ``allow_ancestor_handoff`` (nested
        lead→child declare) or ``force``. Ended unrelated holder → handoff.
        Still-running unrelated holder → return conflict owner.
        """
        key = make_ownership_key(desk_id, path)
        if not key:
            return None
        rid = (run_id or "").strip() or "unknown"
        owner = self._owner.get(key)
        if owner is None or owner == rid or force:
            self._owner[key] = rid
            return None
        if owner in ancestors:
            if allow_ancestor_handoff:
                self._owner[key] = rid
            return None
        if owner in self._ended:
            self._owner[key] = rid
            return None
        return owner

    def transfer(
        self,
        path: str,
        new_owner: str,
        *,
        desk_id: str | None = None,
    ) -> None:
        """Force path → ``new_owner`` (replaces / continue_from / force handoff).

        Written-bit stays with the path (content may already exist on disk).
        Bare ``path`` without ``desk_id`` transfers every matching desk key (escalate).
        """
        rid = (new_owner or "").strip()
        if not rid:
            return
        if desk_id is not None or OWNERSHIP_KEY_SEP in (path or ""):
            key = (
                self._coerce_key(path)
                if OWNERSHIP_KEY_SEP in (path or "")
                else make_ownership_key(desk_id, path)
            )
            if key:
                self._owner[key] = rid
            return
        rel = _normalize(path)
        if not rel:
            return
        keys = [
            k for k in self._owner if ownership_display_path(k) == rel
        ]
        if not keys:
            self._owner[make_ownership_key(None, rel)] = rid
            return
        for key in keys:
            self._owner[key] = rid

    def transfer_all_from(self, old_owner: str, new_owner: str) -> list[str]:
        """Move every key owned by ``old_owner`` to ``new_owner``.

        Returns moved **bare** rel_paths (deduped) for user-facing logs.
        """
        old = (old_owner or "").strip()
        new = (new_owner or "").strip()
        if not old or not new or old == new:
            return []
        moved: list[str] = []
        seen: set[str] = set()
        for path, owner in list(self._owner.items()):
            if owner == old:
                self._owner[path] = new
                bare = ownership_display_path(path)
                if bare and bare not in seen:
                    seen.add(bare)
                    moved.append(bare)
        return moved

    def release(self, path: str, run_id: str, *, desk_id: str | None = None) -> None:
        """Drop ``run_id``'s ownership of ``path`` (only if it still holds it).

        Called when a claimed write then FAILS, so a path the run never actually created
        doesn't spuriously block a sibling. Does **not** release dispatch-time declares
        that were never followed by a failed write of a brand-new path — callers only
        release after a claim that preceded a failed I/O.
        """
        rid = (run_id or "").strip()
        if not rid:
            return
        if desk_id is not None or OWNERSHIP_KEY_SEP in (path or ""):
            key = (
                self._coerce_key(path)
                if OWNERSHIP_KEY_SEP in (path or "")
                else make_ownership_key(desk_id, path)
            )
            if key and self._owner.get(key) == rid:
                del self._owner[key]
                self._written.discard(key)
            return
        rel = _normalize(path)
        if not rel:
            return
        for key, owner in list(self._owner.items()):
            if owner == rid and ownership_display_path(key) == rel:
                del self._owner[key]
                self._written.discard(key)


def resolve_write_coordinator(
    *,
    execution_id: str | None = None,
    fallback: WriteCoordinator | None = None,
) -> WriteCoordinator:
    """Session ledger when coordination is live; else ``fallback`` or a fresh batch book.

    Nested sub-teams share the parent coordination session via
    :data:`current_execution_id` when their own ``execution_id`` has no session.
    """
    from agentcore.runtime.coordination.session import resolve_coordination_session

    session = resolve_coordination_session(execution_id)
    if session is not None:
        return session.ensure_file_ownership()
    if fallback is not None:
        return fallback
    return WriteCoordinator()


def lookup_owner_status(
    owner_run_id: str,
    *,
    execution_id: str | None = None,
) -> tuple[str | None, str]:
    """Return ``(owner_role | None, owner_status)`` from the coordination session.

    Uses the same parent-session fallback as :func:`resolve_write_coordinator`
    (``current_execution_id``) so nested workers do not always see ``unknown``.

    ``owner_status`` is ``running`` / ``completed`` / ``ended`` / ``unknown``.
    """
    rid = (owner_run_id or "").strip()
    if not rid:
        return None, "unknown"
    try:
        from agentcore.runtime.coordination.session import resolve_coordination_session

        session = resolve_coordination_session(execution_id)
    except Exception:  # noqa: BLE001
        return None, "unknown"
    if session is None:
        return None, "unknown"

    role: str | None = None
    running = dict(session.running_workers())
    if rid in running:
        status = "running"
        label = (running.get(rid) or "").strip()
        role = label if label and label != rid else None
    elif rid in session.completed_run_ids:
        status = "completed"
    else:
        ledger = session.file_ownership
        if ledger is not None and getattr(ledger, "is_ended", None) and ledger.is_ended(rid):
            status = "ended"
        else:
            status = "unknown"

    plan = getattr(session, "live_plan", None)
    if plan is not None and hasattr(plan, "by_id"):
        node = plan.by_id(rid)
        if node is not None:
            node_role = (getattr(node, "role", None) or "").strip()
            if node_role:
                role = role or node_role
    return role, status


def parse_ownership_conflict_paths(text: str) -> list[str]:
    """Extract paths from ownership-conflict tool errors embedded in escalate questions."""
    import re

    return list(
        dict.fromkeys(
            m.group(1).strip()
            for m in re.finditer(r"写入冲突：`([^`]+)`", text or "")
            if m.group(1) and m.group(1).strip()
        )
    )


def ownership_escalation_hints(
    *,
    escalator_run_id: str,
    question: str = "",
    execution_id: str | None = None,
    paths: list[str] | None = None,
    write_ancestors: frozenset[str] | set[str] | None = None,
    write_coordinator: WriteCoordinator | None = None,
    desk_id: str | None = None,
) -> dict[str, Any]:
    """Structured ownership hints for CEO escalation inject / resolve transfer.

    Path source order: explicit ``paths`` → this run's recent ``claim`` denials
    on ``write_coordinator`` (still held by someone else) → regex on
    ``写入冲突：`path``` in ``question``. Preferring ledger denials keeps the
    user ownership card wired when the model paraphrases the conflict.

    Returns keys: ownership_paths, lock_owner_run_id, escalator_is_lock_owner_nested_child,
    ownership_kind, owner_status (empty dict when no conflict path resolved).
    ``ownership_paths`` are always bare ``rel_path``.
    """
    hints: dict[str, Any] = {}
    escalator = (escalator_run_id or "").strip()
    path_list = [p for p in (paths or []) if isinstance(p, str) and p.strip()]
    if not path_list and escalator and write_coordinator is not None:
        path_list = [
            p
            for p in write_coordinator.denied_paths_for(escalator)
            if (owner := write_coordinator.owner_of(p, desk_id=desk_id))
            and owner != escalator
        ]
    if not path_list:
        path_list = parse_ownership_conflict_paths(question)
    if not path_list:
        return hints

    # Always expose bare paths to the card / transfer APIs.
    path_list = [ownership_display_path(p) for p in path_list]

    ledger = write_coordinator or resolve_write_coordinator(execution_id=execution_id)
    lock_owners: list[str] = []
    kinds: list[str] = []
    for p in path_list:
        owner = ledger.owner_of(p, desk_id=desk_id)
        if owner:
            lock_owners.append(owner)
            kinds.append("written" if ledger.is_written(p, desk_id=desk_id) else "declared")

    hints["ownership_paths"] = path_list
    if not lock_owners:
        return hints

    lock_owner = lock_owners[0]
    hints["lock_owner_run_id"] = lock_owner
    hints["ownership_kind"] = kinds[0] if kinds else None
    _role, status = lookup_owner_status(lock_owner, execution_id=execution_id)
    hints["owner_status"] = status
    anc = write_ancestors or frozenset()
    nested = lock_owner in anc or _is_nested_child_of(
        escalator, lock_owner, execution_id=execution_id
    )
    hints["escalator_is_lock_owner_nested_child"] = bool(nested)
    return hints


def _is_nested_child_of(
    child_run_id: str,
    parent_run_id: str,
    *,
    execution_id: str | None = None,
) -> bool:
    child = (child_run_id or "").strip()
    parent = (parent_run_id or "").strip()
    if not child or not parent or child == parent:
        return False
    try:
        from agentcore.runtime.coordination.session import resolve_coordination_session

        session = resolve_coordination_session(execution_id)
    except Exception:  # noqa: BLE001
        return False
    if session is None:
        return False
    plan = getattr(session, "live_plan", None)
    if plan is None or not hasattr(plan, "by_id"):
        return False
    node = plan.by_id(child)
    if node is None:
        for n in getattr(plan, "nodes", ()) or ():
            if getattr(n, "run_id", None) == child:
                node = n
                break
    if node is None:
        return False
    return (getattr(node, "parent_run_id", None) or "").strip() == parent
