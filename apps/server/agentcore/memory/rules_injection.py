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
from agentcore.memory.injection import (
    _ANCESTOR_MEMORY_LABEL,
    _FOLDER_MEMORY_LABEL,
    _FOLDER_NAV_LABEL,
    disputed_memory_paths,
)
from agentcore.memory.scope_chain import (
    ancestor_scopes,
    db_scope_chain,
    own_scope_chain,
    snapshot_scope_chain,
)
from agentcore.memory.store import (
    ALWAYS_MEMORY_FILES,
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    MemoryStore,
)
from agentcore.memory.user_memory import strip_memory_chrome

if TYPE_CHECKING:
    from agentcore.memory.account_prepare_cache import AccountPrepareSnapshot

logger = get_logger(__name__)

# Labels the folder-layer user rules inside the shared block (mirrors the memory folder label).
_USER_RULE_FOLDER_LABEL = "（以下为「当前文件夹」专属规则，仅在本文件夹内适用）"

# Labels an ANCESTOR folder's user rules (双模式工作区 §5.4 沿树继承). Nesting has no
# hard-override structure either — proximity is expressed by order (outer first) and by
# saying so in the label, same as the global-vs-folder seam.
_USER_RULE_ANCESTOR_LABEL = (
    "（以下为「上层文件夹」的规则，其下所有文件夹一并适用；"
    "与更靠近当前文件夹的规则冲突时，以更近的为准）"
)

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


async def _memory_fragments(
    store: MemoryStore, user_id: str, *, scope_chain: Sequence[str]
) -> list[RuleFragment]:
    """The AI-memory core as fragments, rendered exactly as the legacy memory concatenation.

    GLOBAL 偏好.md + 画像.md (in ``ALWAYS_MEMORY_FILES`` order, chrome-stripped), then every
    ANCESTOR folder's 画像.md outermost-first (§5.4 沿树继承), then the current folder's
    画像.md and 导航.md (skip missing).

    ``导航.md`` does **not** inherit: it is a route table of workspace-root-relative paths, and
    an outer folder's routes do not resolve from an inner folder's root. Re-basing them is a
    product decision nobody has made — routing the model at broken paths is worse than not
    routing it at all.

    A note the user marked wrong (纠错通道) is skipped in whatever layer it was marked — one
    listing per touched scope answers that, and an unreadable listing degrades to「not
    disputed」rather than dropping the layer.
    """
    frags: list[RuleFragment] = []
    global_disputed = await disputed_memory_paths(store, user_id, None)
    for file in ALWAYS_MEMORY_FILES:
        if file in global_disputed:
            continue
        body = _injectable_body(await store.load(user_id, file), chrome=True)
        if body:
            frags.append(RuleFragment(body=body))
    for scope in ancestor_scopes(scope_chain):
        if CORE_MEMORY_FILE in await disputed_memory_paths(store, user_id, scope):
            continue
        body = _injectable_body(
            await store.load(user_id, CORE_MEMORY_FILE, scope=scope), chrome=True
        )
        if body:
            frags.append(RuleFragment(body=f"{_ANCESTOR_MEMORY_LABEL}\n{body}"))
    if scope_chain:
        folder_id = scope_chain[-1]
        folder_disputed = await disputed_memory_paths(store, user_id, folder_id)
        if CORE_MEMORY_FILE not in folder_disputed:
            folder_body = _injectable_body(
                await store.load(user_id, CORE_MEMORY_FILE, scope=folder_id), chrome=True
            )
            if folder_body:
                frags.append(RuleFragment(body=f"{_FOLDER_MEMORY_LABEL}\n{folder_body}"))
        if NAVIGATION_MEMORY_FILE not in folder_disputed:
            nav_body = _injectable_body(
                await store.load(user_id, NAVIGATION_MEMORY_FILE, scope=folder_id),
                chrome=True,
            )
            if nav_body:
                frags.append(RuleFragment(body=f"{_FOLDER_NAV_LABEL}\n{nav_body}"))
    return frags


async def _user_rule_fragments(
    repo: DocumentRepository, user_id: str, *, scope_chain: Sequence[str]
) -> list[RuleFragment]:
    """The user's own always-injected rule docs (``ai_maintained=false``) as fragments.

    GLOBAL rules first, then each ANCESTOR folder outermost-first, then the current folder
    (§5.4 沿树继承 — 近的排在后面). Frontmatter stripped; unclosed fence omits the entry.
    """
    frags: list[RuleFragment] = []
    for doc in await repo.list_injectable_rules(user_id, None, ai_maintained=False):
        body = _injectable_body(doc.content, chrome=False)
        if body:
            frags.append(RuleFragment(body=body))
    for scope in ancestor_scopes(scope_chain):
        for doc in await repo.list_injectable_rules(user_id, scope, ai_maintained=False):
            body = _injectable_body(doc.content, chrome=False)
            if body:
                frags.append(RuleFragment(body=f"{_USER_RULE_ANCESTOR_LABEL}\n{body}"))
    if scope_chain:
        for doc in await repo.list_injectable_rules(
            user_id, scope_chain[-1], ai_maintained=False
        ):
            body = _injectable_body(doc.content, chrome=False)
            if body:
                frags.append(
                    RuleFragment(body=f"{_USER_RULE_FOLDER_LABEL}\n{body}")
                )
    return frags


def _cloud_rule_fragments(
    payload: Mapping[str, object], key: str, *, label: str | None
) -> list[RuleFragment]:
    """One ``/rules/list`` list field → fragments (optionally layer-labeled)."""
    frags: list[RuleFragment] = []
    for doc in _iter_cloud_rule_docs(payload, key):
        body = _injectable_body(str(doc.get("content") or ""), chrome=False)
        if body:
            frags.append(RuleFragment(body=f"{label}\n{body}" if label else body))
    return frags


def _user_rule_fragments_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[RuleFragment]:
    """Map ``POST /v1/account/rules/list`` payload into injection fragments.

    The cloud resolves the ancestor chain (a sidecar has no folders table) and hands back
    ``ancestor_rules`` already ordered outermost-first; older clouds omit the key and simply
    do not inherit.
    """
    frags = _cloud_rule_fragments(payload, "global_rules", label=None)
    if folder_id:
        frags.extend(
            _cloud_rule_fragments(
                payload, "ancestor_rules", label=_USER_RULE_ANCESTOR_LABEL
            )
        )
        frags.extend(
            _cloud_rule_fragments(
                payload, "project_rules", label=_USER_RULE_FOLDER_LABEL
            )
        )
    return frags


async def assemble_injected_rules(
    store: MemoryStore,
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    enabled: bool,
    scope_chain: Sequence[str] | None = None,
) -> str:
    """Load + compose this turn's ``<设定>`` body (read-side full injection).

    Returns one equal-authority markdown string for ``assemble_system_prompt``. AI memory
    is gated by the caller-supplied ``enabled`` flag (product resolve always on / 定案 A;
    False ⇒ no memory fragments); USER rules are the user's own instructions and are injected
    regardless. Display order is global→ancestors→current, user-owned entries then
    AI-maintained core (load order only — not an authority tier).

    ``scope_chain`` (outermost-first, current folder last) is resolved by the caller so this
    stays a pure assembler over the passed repo/store — omitting it injects the current
    folder only. The production entry point is :func:`assemble_turn_rules`.
    """
    chain = tuple(scope_chain) if scope_chain is not None else own_scope_chain(folder_id)
    fragments: list[RuleFragment] = []
    fragments.extend(await _user_rule_fragments(repo, user_id, scope_chain=chain))
    if enabled:
        fragments.extend(await _memory_fragments(store, user_id, scope_chain=chain))
    return compose_injected_rules(fragments)


def _memory_fragments_from_snapshot(
    snapshot: AccountPrepareSnapshot, *, folder_id: str | None
) -> list[RuleFragment]:
    """AI-memory core fragments from a warm :class:`AccountPrepareSnapshot`."""
    from agentcore.memory.account_prepare_cache import memory_body_from_snapshot

    frags: list[RuleFragment] = []
    for file in ALWAYS_MEMORY_FILES:
        body = _injectable_body(
            memory_body_from_snapshot(snapshot, file, scope=None), chrome=True
        )
        if body:
            frags.append(RuleFragment(body=body))
    chain = snapshot_scope_chain(snapshot, folder_id)
    for scope in ancestor_scopes(chain):
        body = _injectable_body(
            memory_body_from_snapshot(snapshot, CORE_MEMORY_FILE, scope=scope),
            chrome=True,
        )
        if body:
            frags.append(RuleFragment(body=f"{_ANCESTOR_MEMORY_LABEL}\n{body}"))
    if folder_id:
        folder_body = _injectable_body(
            memory_body_from_snapshot(snapshot, CORE_MEMORY_FILE, scope=folder_id),
            chrome=True,
        )
        if folder_body:
            frags.append(RuleFragment(body=f"{_FOLDER_MEMORY_LABEL}\n{folder_body}"))
        nav_body = _injectable_body(
            memory_body_from_snapshot(snapshot, NAVIGATION_MEMORY_FILE, scope=folder_id),
            chrome=True,
        )
        if nav_body:
            frags.append(RuleFragment(body=f"{_FOLDER_NAV_LABEL}\n{nav_body}"))
    return frags


async def assemble_turn_rules(
    store: MemoryStore,
    user_id: str,
    *,
    folder_id: str | None,
    enabled: bool,
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

    user_fragments: list[RuleFragment] = []
    memory_frags: list[RuleFragment] = []
    try:
        creds = get_account_credentials()
        if creds is not None:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is not None:
                user_fragments = _user_rule_fragments_from_cloud(
                    snap.rules_payload, folder_id=folder_id
                )
                if enabled:
                    memory_frags = _memory_fragments_from_snapshot(
                        snap, folder_id=folder_id
                    )
            # miss → empty injection (no cloud await)
        else:
            async with async_session_factory() as session:
                chain = await db_scope_chain(user_id, folder_id, session=session)
                user_fragments = await _user_rule_fragments(
                    DocumentRepository(session), user_id, scope_chain=chain
                )
            if enabled:
                memory_frags = await _memory_fragments(
                    store, user_id, scope_chain=chain
                )
    except Exception as e:  # noqa: BLE001 - user rules must never break a turn's assembly
        logger.warning("memory.user_rules_load_failed", user_id=user_id, error=str(e))

    fragments = list(user_fragments)
    fragments.extend(memory_frags)
    return compose_injected_rules(fragments)


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
    if folder_id:
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

    if folder_id:
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
