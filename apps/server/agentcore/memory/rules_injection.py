"""Always-on rule injection (Agent记忆与知识系统 · 目标形态「读侧全量注入」).

The ``<设定>`` block carries BOTH the user's own rules (``ai_maintained=false``) and the
AI-maintained long-term memory core (``ai_maintained=true``) — same carrier. Read side injects
**every** always-on entry in display order as one equal-authority join (no greedy pack /
keep-rank / silent drop / user-vs-AI wording split); the write-side quota gate owns
"常驻满了". ``ai_maintained`` stays a write-side / UI flag only. Frontmatter is stripped
before the model sees the body via the **storage-layer parser**
(``agentcore.documents.frontmatter``) — one definition of "what is frontmatter", so read
and write cannot drift; a parse failure omits that entry.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentcore.core.logging import get_logger
from agentcore.db.repositories import DocumentRepository
from agentcore.db.repositories.documents import USER_RULES_DOC_NAME
from agentcore.documents.frontmatter import set_entry_frontmatter, strip_entry_frontmatter
from agentcore.memory.always_join import (
    ancestor_rule_bodies_by_scope,
    join_always_layers,
)
from agentcore.memory.injection import (
    _ANCESTOR_SETTINGS_LABEL,
    _FOLDER_NAV_LABEL,
    _FOLDER_SETTINGS_LABEL,
    disputed_memory_paths,
)
from agentcore.memory.scope_chain import (
    ancestor_scopes,
    cloud_scope_chain,
    db_scope_chain,
    own_scope_chain,
    snapshot_scope_chain,
)
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    MemoryStore,
)
from agentcore.memory.user_memory import strip_memory_chrome

if TYPE_CHECKING:
    from agentcore.memory.account_prepare_cache import AccountPrepareSnapshot

logger = get_logger(__name__)

_RULE_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*)$")


_REMEMBER_ACTIONS = frozenset({"add", "replace", "forget", "list"})


def _normalize_rule(text: str) -> str:
    """Whitespace-collapsed, casefolded key for user-rule dedup."""
    return re.sub(r"\s+", " ", text).strip().casefold()


def _collapse_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _rebuild_rule_markdown(lines: Sequence[str]) -> str:
    body = "\n".join(lines).rstrip()
    return f"{body}\n" if body else ""


def _line_rule_text(line: str) -> str:
    match = _RULE_BULLET_RE.match(line)
    return match.group(1) if match else line


def _remove_matching_bullets(current_markdown: str, key: str) -> tuple[str, list[str]]:
    """Drop every line whose normalized rule text equals ``key``.

    Returns ``(md, removed_texts)``.
    """
    if not key:
        return current_markdown, []
    kept: list[str] = []
    removed: list[str] = []
    for line in current_markdown.splitlines():
        existing = _line_rule_text(line)
        if _normalize_rule(existing) == key:
            text = _collapse_ws(existing)
            if text:
                removed.append(text)
            continue
        kept.append(line)
    if not removed:
        return current_markdown, []
    return _rebuild_rule_markdown(kept), removed


@dataclass(frozen=True)
class UserRuleMutationResult:
    """Shared mutate outcome for ``remember`` tool + account ``/rules/remember``."""

    action: str
    changed: bool
    message: str
    markdown: str = ""
    removed: tuple[str, ...] = ()
    content: str | None = None

    @property
    def rules_markdown(self) -> str | None:
        """List action exposes the current rules body; others leave this unset."""
        return self.markdown if self.action == "list" else None


def append_user_rule_bullet(current_markdown: str, content: str) -> tuple[str, bool]:
    """Append ``content`` as a rule bullet with normalized dedup. Returns ``(new_md, changed)``.

    User rules are a plain bullet list with NO AI-maintained chrome (they are user-owned, §5.2).
    A normalized duplicate is a no-op — re-remembering the same rule does not grow the doc.
    """
    text = _collapse_ws(content)
    if not text:
        return current_markdown, False
    key = _normalize_rule(text)
    for line in current_markdown.splitlines():
        if _normalize_rule(_line_rule_text(line)) == key:
            return current_markdown, False
    body = current_markdown.rstrip()
    return (f"{body}\n" if body else "") + f"- {text}\n", True


def mutate_user_rule_markdown(
    current_markdown: str,
    *,
    action: str = "add",
    content: str | None = None,
    replaces: str | None = None,
) -> UserRuleMutationResult:
    """Pure user-rule mutate: ``add`` / ``replace`` / ``forget`` / ``list``.

    Matching uses :func:`_normalize_rule` (whitespace fold + casefold). ``forget`` / ``replace``
    remove *all* bullets sharing the matched key. ``replace`` with a missing old bullet appends
    only and reports that honestly (never claims「已替换」).
    """
    action_key = (action or "add").strip().lower() or "add"
    if action_key not in _REMEMBER_ACTIONS:
        return UserRuleMutationResult(
            action=action_key,
            changed=False,
            message=f"不支持的 action：{action_key}。",
            markdown=current_markdown,
        )

    if action_key == "list":
        body = current_markdown if current_markdown.strip() else ""
        message = (
            f"当前用户规则：\n{body.rstrip()}"
            if body.strip()
            else "当前暂无用户规则。"
        )
        return UserRuleMutationResult(
            action="list",
            changed=False,
            message=message,
            markdown=body,
        )

    text = _collapse_ws(content or "")
    if not text:
        return UserRuleMutationResult(
            action=action_key,
            changed=False,
            message="缺少 content。",
            markdown=current_markdown,
        )

    if action_key == "add":
        new_md, changed = append_user_rule_bullet(current_markdown, text)
        if not changed:
            return UserRuleMutationResult(
                action="add",
                changed=False,
                message="这条规则已经记过了（未重复写入）。",
                markdown=current_markdown,
                content=text,
            )
        return UserRuleMutationResult(
            action="add",
            changed=True,
            message=f"已追加规则：{text}",
            markdown=new_md,
            content=text,
        )

    if action_key == "forget":
        new_md, removed = _remove_matching_bullets(current_markdown, _normalize_rule(text))
        if not removed:
            return UserRuleMutationResult(
                action="forget",
                changed=False,
                message=f"未找到要忘掉的规则：{text}",
                markdown=current_markdown,
                content=text,
            )
        removed_label = "；".join(removed)
        return UserRuleMutationResult(
            action="forget",
            changed=True,
            message=f"已删除规则：{removed_label}",
            markdown=new_md,
            removed=tuple(removed),
            content=text,
        )

    # replace
    old_text = _collapse_ws(replaces or "")
    if not old_text:
        return UserRuleMutationResult(
            action="replace",
            changed=False,
            message="replace 需要 replaces（要替换掉的旧规则）。",
            markdown=current_markdown,
            content=text,
        )
    after_remove, removed = _remove_matching_bullets(
        current_markdown, _normalize_rule(old_text)
    )
    new_md, appended = append_user_rule_bullet(after_remove, text)
    if removed:
        removed_label = "；".join(removed)
        return UserRuleMutationResult(
            action="replace",
            changed=True,
            message=f"已替换规则：去掉「{removed_label}」，写入「{text}」",
            markdown=new_md,
            removed=tuple(removed),
            content=text,
        )
    if appended:
        return UserRuleMutationResult(
            action="replace",
            changed=True,
            message=f"未找到旧条「{old_text}」，已追加新规则：{text}",
            markdown=new_md,
            content=text,
        )
    return UserRuleMutationResult(
        action="replace",
        changed=False,
        message=f"未找到旧条「{old_text}」，且新规则已存在（未重复写入）。",
        markdown=current_markdown,
        content=text,
    )


async def append_user_rule(
    repo: DocumentRepository, user_id: str, *, folder_id: str | None, content: str
) -> bool:
    """Append a user rule to the scope's canonical user-rule doc (``remember`` directive path).

    Create-on-write; normalized dedup; returns whether anything changed. This is the「用户明确
    下指令 → 落用户规则」half of the ``remember`` split (§5.7 用户规则入口①) — a ``rule`` doc
    with ``ai_maintained=false``, so the offline consolidation never rewrites it.
    """
    result = await mutate_user_rule(
        repo, user_id, folder_id=folder_id, action="add", content=content
    )
    return result.changed


async def mutate_user_rule(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    action: str = "add",
    content: str | None = None,
    replaces: str | None = None,
) -> UserRuleMutationResult:
    """Persist a user-rule mutate for the scope's canonical rule doc (tool + account shared).

    Write-side always quota uses ``writer=ai`` (same gate as consolidation): net growth
    past the cap raises :class:`~agentcore.memory.always_quota.AlwaysQuotaExceededError`;
    shrink / list / unchanged skip the gate.
    """
    doc = await repo.get_user_rules_doc(user_id, folder_id)
    current = doc.content if doc is not None else ""
    result = mutate_user_rule_markdown(
        current, action=action, content=content, replaces=replaces
    )
    if result.action == "list" or not result.changed:
        return result
    from agentcore.memory.always_quota import (
        AlwaysQuotaExceededError,
        always_entry_chars,
        check_always_write,
        notify_always_quota_exceeded,
    )

    body = set_entry_frontmatter(result.markdown, apply="always")
    existing_always = (
        doc is not None
        and getattr(doc, "role", "rule") == "rule"
        and doc.apply_mode == "always"
    )
    decision = await check_always_write(
        repo,
        user_id,
        folder_id=folder_id,
        writer="ai",
        editing_existing_always=existing_always,
        exclude_id=doc.id if doc is not None else None,
        new_content=body,
        new_is_always=True,
    )
    if not decision.allowed:
        usage = decision.usage
        assert usage is not None
        exc = AlwaysQuotaExceededError(
            usage,
            decision.message,
            file=USER_RULES_DOC_NAME,
            scope=folder_id,
            attempted_chars=always_entry_chars(body),
        )
        await notify_always_quota_exceeded(user_id, exc)
        raise exc
    await repo.upsert_user_rules_doc(user_id, folder_id, result.markdown)
    return result


def _injectable_body(raw: str, *, chrome: bool) -> str | None:
    """Frontmatter-strip (+ optional human chrome); ``None`` means skip this entry."""
    stripped = strip_entry_frontmatter(raw)
    if stripped is None:
        return None
    body = strip_memory_chrome(stripped) if chrome else stripped.strip()
    return body or None


@dataclass(frozen=True)
class RuleFragment:
    """One always-injected rule doc, ready to place in ``<设定>``.

    ``body`` is fully rendered (frontmatter/chrome stripped, folder-labeled when
    folder-scoped). Fragments are equal on the read side — no authority tier.
    """

    body: str


def compose_injected_rules(fragments: Sequence[RuleFragment]) -> str:
    """Join all always-on fragments in display order into one ``<设定>`` body.

    No doc/char budget, no keep-rank, no silent drop, no user/AI split — write side
    owns the quota gate; prompt wording is a single equal-authority block.
    """
    return "\n\n".join(f.body for f in fragments)


def _join_frags(**kwargs: object) -> list[RuleFragment]:
    return [
        RuleFragment(body=item.body)
        for item in join_always_layers(
            folder_settings_label=_FOLDER_SETTINGS_LABEL,
            ancestor_settings_label=_ANCESTOR_SETTINGS_LABEL,
            folder_nav_label=_FOLDER_NAV_LABEL,
            **kwargs,  # type: ignore[arg-type]
        )
    ]


async def _slot_body(
    store: MemoryStore,
    user_id: str,
    path: str,
    scope: str | None,
    disputed: frozenset[str],
) -> str | None:
    if path in disputed:
        return None
    return _injectable_body(await store.load(user_id, path, scope=scope), chrome=True)


async def _rule_bodies(
    repo: DocumentRepository, user_id: str, scope: str | None
) -> list[str]:
    out: list[str] = []
    for doc in await repo.list_injectable_rules(user_id, scope, ai_maintained=False):
        body = _injectable_body(doc.content, chrome=False)
        if body:
            out.append(body)
    return out


def _cloud_rule_bodies(payload: Mapping[str, object], key: str) -> list[str]:
    out: list[str] = []
    for doc in _iter_cloud_rule_docs(payload, key):
        body = _injectable_body(str(doc.get("content") or ""), chrome=False)
        if body:
            out.append(body)
    return out


def _cloud_doc_body(doc: Mapping[str, object]) -> str | None:
    return _injectable_body(str(doc.get("content") or ""), chrome=False)


def _snapshot_slot(
    snapshot: AccountPrepareSnapshot, path: str, scope: str | None
) -> str | None:
    from agentcore.memory.account_prepare_cache import memory_body_from_snapshot

    return _injectable_body(
        memory_body_from_snapshot(snapshot, path, scope=scope), chrome=True
    )


async def _memory_fragments(
    store: MemoryStore, user_id: str, *, scope_chain: Sequence[str]
) -> list[RuleFragment]:
    """Memory slots only (tests). Production assemble interleaves rules in the same layers."""
    global_disputed = await disputed_memory_paths(store, user_id, None)
    ancestor_layers: list[tuple[str | None, Sequence[str]]] = []
    for scope in ancestor_scopes(scope_chain):
        disputed = await disputed_memory_paths(store, user_id, scope)
        profile = await _slot_body(
            store, user_id, CORE_MEMORY_FILE, scope, disputed
        )
        ancestor_layers.append((profile, ()))
    current_profile = current_nav = None
    if scope_chain:
        folder_id = scope_chain[-1]
        disputed = await disputed_memory_paths(store, user_id, folder_id)
        current_profile = await _slot_body(
            store, user_id, CORE_MEMORY_FILE, folder_id, disputed
        )
        current_nav = await _slot_body(
            store, user_id, NAVIGATION_MEMORY_FILE, folder_id, disputed
        )
    return _join_frags(
        global_pref=await _slot_body(
            store, user_id, PREFERENCES_MEMORY_FILE, None, global_disputed
        ),
        global_profile=await _slot_body(
            store, user_id, CORE_MEMORY_FILE, None, global_disputed
        ),
        ancestor_layers=ancestor_layers,
        current_profile=current_profile,
        current_nav=current_nav,
        include_current=bool(scope_chain),
    )


async def _user_rule_fragments(
    repo: DocumentRepository, user_id: str, *, scope_chain: Sequence[str]
) -> list[RuleFragment]:
    """User always-rules only (tests / enabled=False). Same scope labels as the mixed join."""
    ancestor_layers = [
        (None, await _rule_bodies(repo, user_id, scope))
        for scope in ancestor_scopes(scope_chain)
    ]
    current_rules: list[str] = []
    if scope_chain:
        current_rules = await _rule_bodies(repo, user_id, scope_chain[-1])
    return _join_frags(
        global_rules=await _rule_bodies(repo, user_id, None),
        ancestor_layers=ancestor_layers,
        current_rules=current_rules,
        include_current=bool(scope_chain),
    )


def _user_rule_fragments_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[RuleFragment]:
    """Map ``POST /v1/account/rules/list`` into scope layers (rules only)."""
    chain = cloud_scope_chain(payload, folder_id)
    if folder_id and not chain:
        return _join_frags(global_rules=_cloud_rule_bodies(payload, "global_rules"))
    ancestors = ancestor_scopes(chain)
    if folder_id and not ancestors and _iter_cloud_rule_docs(payload, "ancestor_rules"):
        ancestor_layers: list[tuple[str | None, Sequence[str]]] = [
            (None, _cloud_rule_bodies(payload, "ancestor_rules"))
        ]
    else:
        ancestor_layers = [
            (None, rules)
            for rules in ancestor_rule_bodies_by_scope(
                _iter_cloud_rule_docs(payload, "ancestor_rules"),
                ancestors,
                body_of=_cloud_doc_body,
            )
        ]
    current_rules = _cloud_rule_bodies(payload, "project_rules") if chain else []
    return _join_frags(
        global_rules=_cloud_rule_bodies(payload, "global_rules"),
        ancestor_layers=ancestor_layers,
        current_rules=current_rules,
        include_current=bool(chain),
    )


async def assemble_injected_rules(
    store: MemoryStore,
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    enabled: bool,
    scope_chain: Sequence[str] | None = None,
    folder_user_id: str | None = None,
) -> str:
    """Load + compose this turn's ``<设定>`` body (read-side full injection).

    Equal-authority markdown for ``assemble_system_prompt``. AI memory is gated by
    ``enabled`` (False ⇒ slots omitted); user always-rules still inject. Display
    order is global → ancestors → current; inside a layer, protocol slots then
    that layer's user-written always md.

    ``scope_chain`` (outermost-first, current last) is resolved by the caller —
    omitting it injects the current folder only. Production entry:
    :func:`assemble_turn_rules`.
    """
    chain = tuple(scope_chain) if scope_chain is not None else own_scope_chain(folder_id)
    folder_actor = folder_user_id or user_id
    global_disputed = (
        await disputed_memory_paths(store, user_id, None) if enabled else frozenset()
    )
    ancestor_layers: list[tuple[str | None, Sequence[str]]] = []
    for scope in ancestor_scopes(chain):
        disputed = (
            await disputed_memory_paths(store, folder_actor, scope) if enabled else frozenset()
        )
        profile = (
            await _slot_body(store, folder_actor, CORE_MEMORY_FILE, scope, disputed)
            if enabled
            else None
        )
        ancestor_layers.append((profile, await _rule_bodies(repo, folder_actor, scope)))
    current_profile = current_nav = None
    current_rules: list[str] = []
    if chain:
        current_id = chain[-1]
        disputed = (
            await disputed_memory_paths(store, folder_actor, current_id)
            if enabled
            else frozenset()
        )
        if enabled:
            current_profile = await _slot_body(
                store, folder_actor, CORE_MEMORY_FILE, current_id, disputed
            )
            current_nav = await _slot_body(
                store, folder_actor, NAVIGATION_MEMORY_FILE, current_id, disputed
            )
        current_rules = await _rule_bodies(repo, folder_actor, current_id)
    return compose_injected_rules(
        _join_frags(
            global_pref=(
                await _slot_body(
                    store, user_id, PREFERENCES_MEMORY_FILE, None, global_disputed
                )
                if enabled
                else None
            ),
            global_profile=(
                await _slot_body(
                    store, user_id, CORE_MEMORY_FILE, None, global_disputed
                )
                if enabled
                else None
            ),
            global_rules=await _rule_bodies(repo, user_id, None),
            ancestor_layers=ancestor_layers,
            current_profile=current_profile,
            current_nav=current_nav,
            current_rules=current_rules,
            include_current=bool(chain),
        )
    )


def _fragments_from_snapshot(
    snapshot: AccountPrepareSnapshot, *, folder_id: str | None, enabled: bool
) -> list[RuleFragment]:
    payload = snapshot.rules_payload
    chain = snapshot_scope_chain(snapshot, folder_id)
    raw_chain = payload.get("folder_chain") if payload else None
    if isinstance(raw_chain, list) and not raw_chain:
        chain = ()
    ancestors = ancestor_scopes(chain)
    rule_lists = ancestor_rule_bodies_by_scope(
        _iter_cloud_rule_docs(payload, "ancestor_rules"),
        ancestors,
        body_of=_cloud_doc_body,
    )
    ancestor_layers: list[tuple[str | None, Sequence[str]]] = []
    for i, scope in enumerate(ancestors):
        profile = (
            _snapshot_slot(snapshot, CORE_MEMORY_FILE, scope) if enabled else None
        )
        ancestor_layers.append((profile, rule_lists[i]))
    current_profile = current_nav = None
    current_rules: list[str] = []
    current_id = chain[-1] if chain else None
    if current_id:
        if enabled:
            current_profile = _snapshot_slot(
                snapshot, CORE_MEMORY_FILE, current_id
            )
            current_nav = _snapshot_slot(
                snapshot, NAVIGATION_MEMORY_FILE, current_id
            )
        current_rules = _cloud_rule_bodies(payload, "project_rules")
    return _join_frags(
        global_pref=(
            _snapshot_slot(snapshot, PREFERENCES_MEMORY_FILE, None) if enabled else None
        ),
        global_profile=(
            _snapshot_slot(snapshot, CORE_MEMORY_FILE, None) if enabled else None
        ),
        global_rules=_cloud_rule_bodies(payload, "global_rules"),
        ancestor_layers=ancestor_layers,
        current_profile=current_profile,
        current_nav=current_nav,
        current_rules=current_rules,
        include_current=bool(chain),
    )


def _memory_fragments_from_snapshot(
    snapshot: AccountPrepareSnapshot, *, folder_id: str | None
) -> list[RuleFragment]:
    """Memory slots from a warm snapshot (tests). Turn path uses mixed join."""
    return _fragments_from_snapshot(snapshot, folder_id=folder_id, enabled=True)


async def assemble_turn_rules(
    store: MemoryStore,
    user_id: str,
    *,
    folder_id: str | None,
    enabled: bool,
    folder_user_id: str | None = None,
) -> str:
    """Turn-time convenience over :func:`assemble_injected_rules` (the pipeline entry point).

    AI memory is read through the given ``store`` (the patchable pipeline seam) when no
    account ticket is bound. With account creds, prepare reads the process snapshot
    cache only (warm seeds it); miss → empty injection — never await cloud HTTP on the
    turn hot path. User-rule loading degrades to「no rules」on ANY error (missing DB in a
    unit test, transient / offline failure) so memory injection can never break a turn —
    matching the rest of the memory system's defensive posture.

    Nested folders inherit outside-in (§5.4): the ancestor chain comes from the warm
    snapshot on the ticketed path and from ``folders.rel_path`` otherwise.
    """
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot

    try:
        folder_actor = folder_user_id or user_id
        creds = get_account_credentials()
        if creds is not None and folder_actor == user_id:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return ""
            return compose_injected_rules(
                _fragments_from_snapshot(snap, folder_id=folder_id, enabled=enabled)
            )
        async with async_session_factory() as session:
            chain = await db_scope_chain(folder_actor, folder_id, session=session)
            return await assemble_injected_rules(
                store,
                DocumentRepository(session),
                user_id,
                folder_id=folder_id,
                enabled=enabled,
                scope_chain=chain,
                folder_user_id=folder_actor,
            )
    except Exception as e:  # noqa: BLE001 - user rules must never break a turn's assembly
        logger.warning("memory.user_rules_load_failed", user_id=user_id, error=str(e))
        return ""


# --- on-demand user rules (规则目录 + consult_rule; NOT memory topics) ----------------------


@dataclass(frozen=True)
class OnDemandUserRule:
    """One entry in the「规则目录」: consult name + optional one-line summary.

    Separate from :class:`~agentcore.memory.injection.MemoryTopic` — on_demand rules are
    constraint appendices (应遵守); topics are thick facts (供查阅). Do not merge the two.
    """

    name: str
    summary: str = ""


def rule_consult_name(doc_name: str) -> str:
    """Normalize a rule document filename to the name models pass to ``consult_rule``."""
    return doc_name.removesuffix(".md").strip()


async def _scope_on_demand_user_rules(
    repo: DocumentRepository, user_id: str, folder_id: str | None
) -> list[tuple[str, str]]:
    """``(consult_name, description)`` pairs for one scope's live on_demand user rules.

    The summary is the entry's ``description`` — written for retrieval — never its first
    content line; the repo already drops user-disputed entries.
    """
    out: list[tuple[str, str]] = []
    for doc in await repo.list_on_demand_user_rules(user_id, folder_id):
        name = rule_consult_name(doc.name)
        if not name:
            continue
        out.append((name, doc.description or ""))
    return out


def _iter_cloud_rule_docs(
    payload: Mapping[str, object], key: str
) -> list[Mapping[str, object]]:
    """Normalize ``payload[key]`` to a list of mapping docs (skip junk)."""
    raw = payload.get(key) or []
    if not isinstance(raw, list):
        return []
    return [doc for doc in raw if isinstance(doc, Mapping)]


def _collect_cloud_on_demand(
    summaries: dict[str, str], payload: Mapping[str, object], key: str
) -> None:
    for doc in _iter_cloud_rule_docs(payload, key):
        name = rule_consult_name(str(doc.get("name") or ""))
        if not name:
            continue
        summaries.setdefault(name, str(doc.get("description") or ""))


def on_demand_user_rules_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[OnDemandUserRule]:
    """Map account ``/rules/list`` on_demand fields into the「规则目录」entries.

    Merge matches the local-DB path: global, then ancestors outermost-first, then the
    current folder, all via ``setdefault`` (the outer summary wins a name collision, as it
    has since the global-vs-folder split). Older clouds omitting the keys → [].
    """
    summaries: dict[str, str] = {}
    _collect_cloud_on_demand(summaries, payload, "global_on_demand_rules")
    chain = cloud_scope_chain(payload, folder_id)
    if chain:
        _collect_cloud_on_demand(summaries, payload, "ancestor_on_demand_rules")
        _collect_cloud_on_demand(summaries, payload, "project_on_demand_rules")
    return [
        OnDemandUserRule(name=name, summary=summaries[name]) for name in sorted(summaries)
    ]


def lookup_on_demand_rule_body_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None, name: str
) -> str | None:
    """Nearest-layer-first body lookup on a ``/rules/list`` payload (consult_rule).

    Current folder → ancestors innermost-first → global: 近的覆盖远的, so the layer the
    user is standing in answers even when an outer folder defines the same rule name.
    """
    key = rule_consult_name(name)
    if not key:
        return None

    def _body_in(scope_key: str, *, innermost_first: bool = False) -> str | None:
        docs = _iter_cloud_rule_docs(payload, scope_key)
        # Ancestors arrive as one flat outermost-first list; reading it backwards is what
        # makes the nearest ancestor answer.
        for doc in reversed(docs) if innermost_first else docs:
            if rule_consult_name(str(doc.get("name") or "")) != key:
                continue
            body = str(doc.get("content") or "")
            return body if body.strip() else None
        return None

    if cloud_scope_chain(payload, folder_id):
        hit = _body_in("project_on_demand_rules")
        if hit is None:
            hit = _body_in("ancestor_on_demand_rules", innermost_first=True)
        if hit is not None:
            return hit
    return _body_in("global_on_demand_rules")


async def load_on_demand_user_rules(
    user_id: str, *, folder_id: str | None
) -> list[OnDemandUserRule]:
    """Merge global + the folder chain's on_demand user rules for the「规则目录」(or []).

    Degrades to [] on any error (same defensive posture as always-rule loading).
    Account-ticketed turns read the process prepare snapshot only (warm seeds it;
    miss → []); local / server turns read the document session.
    """
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot

    try:
        creds = get_account_credentials()
        if creds is not None:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return []
            return on_demand_user_rules_from_cloud(
                snap.rules_payload, folder_id=folder_id
            )
        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            summaries: dict[str, str] = {}
            for name, summary in await _scope_on_demand_user_rules(repo, user_id, None):
                summaries.setdefault(name, summary)
            for scope in await db_scope_chain(user_id, folder_id, session=session):
                for name, summary in await _scope_on_demand_user_rules(
                    repo, user_id, scope
                ):
                    summaries.setdefault(name, summary)
            return [
                OnDemandUserRule(name=name, summary=summaries[name])
                for name in sorted(summaries)
            ]
    except Exception as e:  # noqa: BLE001 - must never break turn assembly
        logger.warning("memory.on_demand_rules_load_failed", user_id=user_id, error=str(e))
        return []
