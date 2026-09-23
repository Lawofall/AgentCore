"""Always-on user-rule injection (Agent记忆与知识系统 · 读侧全量注入).

``<设定>`` carries the user's own rules (``ai_maintained=false``) only. AI-maintained
notes (偏好 / 画像 / 导航 / 主题) stay on disk and are not injected. Read side injects
every always-on **user** entry in display order as one equal-authority join (no greedy
pack / keep-rank / silent drop). The write-side quota gate owns「常驻满了」.
Frontmatter is stripped before the model sees the body via the **storage-layer parser**
(``agentcore.documents.frontmatter``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.db.repositories import DocumentRepository
from agentcore.documents.description import maybe_schedule_description_fill
from agentcore.documents.frontmatter import (
    ApplyMode,
    FrontmatterEditError,
    FrontmatterError,
    parse_entry_frontmatter,
    set_entry_frontmatter,
    strip_entry_frontmatter,
)
from agentcore.memory.always_join import join_always_layers
from agentcore.memory.scope_chain import (
    ancestor_scopes,
    cloud_scope_chain,
    db_scope_chain,
    own_scope_chain,
)
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
    MemoryStore,
)

logger = get_logger(__name__)

# Layer labels inside the shared <设定> block (scope, not author).
_FOLDER_SETTINGS_LABEL = "（以下为「当前文件夹」专属设定，仅在本文件夹内适用）"
_ANCESTOR_SETTINGS_LABEL = (
    "（以下为「上层文件夹」的设定，其下所有文件夹一并适用）"
)

_RULE_MUTATE_ACTIONS = frozenset({"write", "read", "delete", "list"})
_MAX_RULE_NAME_CHARS = 80
_SKIP_ALWAYS_CONSULT_FILES = frozenset(
    {
        PREFERENCES_MEMORY_FILE,
        CORE_MEMORY_FILE,
        NAVIGATION_MEMORY_FILE,
    }
)


def normalize_rule_filename(raw: str) -> str | None:
    """Basename ``*.md`` for a rule document, or None when the name is not usable."""
    text = (raw or "").strip()
    if not text or text in {".", ".."} or "/" in text or "\\" in text:
        return None
    name = text if text.lower().endswith(".md") else f"{text}.md"
    if name == ".md" or len(name) > _MAX_RULE_NAME_CHARS:
        return None
    if name in _SKIP_ALWAYS_CONSULT_FILES:
        return None
    return name


def _fail(
    action: str, message: str, *, name: str = "", apply: str = ""
) -> UserRuleMutationResult:
    return UserRuleMutationResult(
        action=action,
        changed=False,
        message=message,
        name=name,
        apply=apply,
        ok=False,
    )


def _apply_label(apply_mode: str) -> str:
    if apply_mode == "always":
        return "常驻"
    if apply_mode == "paths":
        return "路径"
    return "按需"


def _resolve_write_apply(
    *,
    apply: str | None,
    content: str,
    existing_apply: str | None,
) -> ApplyMode | FrontmatterError:
    if apply in ("always", "on_demand", "paths"):
        return apply  # type: ignore[return-value]
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return parsed
    if parsed.apply_present:
        return parsed.apply
    if existing_apply in ("always", "on_demand", "paths"):
        return existing_apply  # type: ignore[return-value]
    # 新条目没写 apply：与解析器同一缺省（按需）。
    return "on_demand"


def _format_rule_catalog(docs: Sequence[object]) -> str:
    if not docs:
        return "当前没有用户规则。"
    lines = ["当前用户规则："]
    for doc in docs:
        apply_mode = str(getattr(doc, "apply_mode", "") or "")
        label = _apply_label(apply_mode)
        desc = str(getattr(doc, "description", "") or "").strip()
        extra = f"  {desc}" if desc else ""
        lines.append(f"- {getattr(doc, 'name', '')}  {label}{extra}")
    return "\n".join(lines)


@dataclass(frozen=True)
class UserRuleMutationResult:
    """Shared mutate outcome for file overlay + account ``/rules/write|read|delete``."""

    action: str
    changed: bool
    message: str
    name: str = ""
    apply: str = ""
    body: str = ""
    catalog: tuple[tuple[str, str, str], ...] = ()
    content: str | None = None
    ok: bool = True


async def mutate_user_rule(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    action: str = "write",
    name: str | None = None,
    content: str | None = None,
    apply: str | None = None,
    description: str | None = None,
) -> UserRuleMutationResult:
    """Persist one named user-rule markdown (tool + account shared).

    Write-side always quota uses ``writer=ai``: net growth past the cap raises
    :class:`~agentcore.memory.always_quota.AlwaysQuotaExceededError`; shrink / list /
    read / unchanged skip the gate.
    """
    action_key = (action or "write").strip().lower() or "write"
    if action_key not in _RULE_MUTATE_ACTIONS:
        return _fail(action_key, f"不支持的 action：{action_key}。")

    if action_key == "list":
        docs = await repo.list_user_rule_docs(user_id, folder_id)
        catalog = tuple(
            (
                str(doc.name),
                str(doc.apply_mode or ""),
                str(doc.description or ""),
            )
            for doc in docs
        )
        return UserRuleMutationResult(
            action="list",
            changed=False,
            message=_format_rule_catalog(docs),
            catalog=catalog,
        )

    raw_name = (name or "").strip()
    filename = normalize_rule_filename(raw_name)
    if filename is None:
        if not raw_name:
            return _fail(
                action_key,
                "缺少 name。一个主题一篇文件，例如 回复语言.md。",
            )
        return _fail(
            action_key,
            "name 不可用。请用短文件名（如 回复语言.md），不要路径，也不要占用画像/偏好/导航。",
            name=raw_name,
        )

    if action_key == "read":
        doc = await repo.get_user_rule_doc(user_id, folder_id, filename)
        if doc is None:
            return _fail(
                "read",
                f"没有规则「{filename}」。",
                name=filename,
            )
        body = doc.content or ""
        apply_mode = str(doc.apply_mode or "")
        label = _apply_label(apply_mode)
        return UserRuleMutationResult(
            action="read",
            changed=False,
            message=f"规则「{filename}」（{label}）：\n{body}".rstrip(),
            name=filename,
            apply=apply_mode,
            body=body,
            content=body,
        )

    if action_key == "delete":
        existed = await repo.get_user_rule_doc(user_id, folder_id, filename)
        if existed is None:
            return _fail(
                "delete",
                f"没有规则「{filename}」。",
                name=filename,
            )
        apply_mode = str(existed.apply_mode or "")
        await repo.delete_user_rule_doc(user_id, folder_id, filename)
        return UserRuleMutationResult(
            action="delete",
            changed=True,
            message=f"已删除规则「{filename}」。",
            name=filename,
            apply=apply_mode,
        )

    text = (content or "").strip()
    if not text:
        return _fail("write", "缺少 content。", name=filename)

    existing = await repo.get_user_rule_doc(user_id, folder_id, filename)
    resolved = _resolve_write_apply(
        apply=apply,
        content=text,
        existing_apply=existing.apply_mode if existing is not None else None,
    )
    if isinstance(resolved, FrontmatterError):
        return _fail("write", resolved.message, name=filename)
    apply_mode = resolved
    try:
        body = set_entry_frontmatter(
            text, apply=apply_mode, description=description
        )
    except FrontmatterEditError as e:
        return _fail("write", str(e), name=filename)
    parsed_body = parse_entry_frontmatter(body)
    if isinstance(parsed_body, FrontmatterError):
        return _fail("write", parsed_body.message, name=filename)

    if existing is not None and (existing.content or "") == body:
        label = _apply_label(apply_mode)
        return UserRuleMutationResult(
            action="write",
            changed=False,
            message=f"规则「{filename}」没有变化（{label}）。",
            name=filename,
            apply=apply_mode,
            body=body,
            content=text,
        )

    from agentcore.memory.always_quota import (
        AlwaysQuotaExceededError,
        always_entry_chars,
        check_always_write,
        notify_always_quota_exceeded,
    )
    from agentcore.memory.rule_resolve import counts_as_always_content

    existing_always = existing is not None and counts_as_always_content(
        existing.content or ""
    )
    new_is_always = counts_as_always_content(body)
    if new_is_always:
        decision = await check_always_write(
            repo,
            user_id,
            folder_id=folder_id,
            writer="ai",
            editing_existing_always=existing_always,
            exclude_id=existing.id if existing is not None else None,
            new_content=body,
            new_is_always=True,
        )
        if not decision.allowed:
            usage = decision.usage
            assert usage is not None
            quota_err = AlwaysQuotaExceededError(
                usage,
                decision.message,
                file=filename,
                scope=folder_id,
                attempted_chars=always_entry_chars(body),
            )
            await notify_always_quota_exceeded(user_id, quota_err)
            raise quota_err

    doc = await repo.upsert_user_rule_doc(
        user_id,
        folder_id,
        filename,
        text,
        apply=apply_mode,
        description=description,
    )
    maybe_schedule_description_fill(
        document_id=doc.id,
        user_id=user_id,
        kind=doc.kind,
        description=doc.description or "",
        content=doc.content or "",
    )
    label = _apply_label(apply_mode)
    return UserRuleMutationResult(
        action="write",
        changed=True,
        message=f"已写入规则「{filename}」（{label}）。",
        name=filename,
        apply=apply_mode,
        body=doc.content or body,
        content=text,
    )


def _injectable_body(raw: str) -> str | None:
    """Frontmatter-strip; ``None`` means skip this entry."""
    stripped = strip_entry_frontmatter(raw)
    if stripped is None:
        return None
    body = stripped.strip()
    return body or None


def _labeled_rule_body(name: str, content: str) -> str | None:
    """Injectable body headed by the catalog address so ``<设定>`` names the entry."""
    body = _injectable_body(content)
    if not body:
        return None
    title = (name or "").strip() or "untitled.md"
    from agentcore.memory.rule_files import rule_entry_relpath

    return f"### {rule_entry_relpath(title)}\n{body}"


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
            **kwargs,  # type: ignore[arg-type]
        )
    ]


async def _scope_live_rules(
    repo: DocumentRepository, user_id: str, folder_id: str | None, rank: int
):
    """Always + on_demand + path docs of one scope, tagged with ``rank``."""
    from agentcore.memory.rule_resolve import LiveRule, live_rule_from_doc

    out: list[LiveRule] = []
    loaded = (
        ("always", await repo.list_injectable_rules(user_id, folder_id, ai_maintained=False)),
        ("on_demand", await repo.list_on_demand_user_rules(user_id, folder_id)),
        ("paths", await repo.list_path_user_rules(user_id, folder_id)),
    )
    for column, docs in loaded:
        for doc in docs:
            live = live_rule_from_doc(doc, column_apply=column, rank=rank)
            if live is not None:
                out.append(live)
    return out


def _fragments_from_resolved(
    resolved, *, ancestor_count: int, has_current: bool
) -> list[RuleFragment]:
    """Always-channel winners, outer rank first. Empty layers drop out."""
    by_rank: dict[int, list[str]] = {}
    for rule in resolved.always:
        body = _labeled_rule_body(f"{rule.name}.md", rule.content)
        if body:
            by_rank.setdefault(rule.rank, []).append(body)
    ancestor_layers = [
        (None, by_rank.get(index, [])) for index in range(1, ancestor_count + 1)
    ]
    current_rank = ancestor_count + 1
    return _join_frags(
        global_rules=by_rank.get(0, []),
        ancestor_layers=ancestor_layers,
        current_rules=by_rank.get(current_rank, []) if has_current else [],
        include_current=has_current,
    )


async def _user_rule_fragments(
    repo: DocumentRepository, user_id: str, *, scope_chain: Sequence[str]
) -> list[RuleFragment]:
    """User always-rules. Same name: nearest desk only."""
    from agentcore.memory.rule_resolve import resolve_rules

    ancestors = ancestor_scopes(scope_chain)
    live = await _scope_live_rules(repo, user_id, None, 0)
    for index, scope in enumerate(ancestors, start=1):
        live.extend(await _scope_live_rules(repo, user_id, scope, index))
    if scope_chain:
        live.extend(
            await _scope_live_rules(repo, user_id, scope_chain[-1], len(ancestors) + 1)
        )
    return _fragments_from_resolved(
        resolve_rules(live),
        ancestor_count=len(ancestors),
        has_current=bool(scope_chain),
    )


@dataclass(frozen=True)
class _CloudRules:
    rules: list
    ancestor_count: int
    has_current: bool


def _bucket_ancestor_docs(
    grouped: Sequence[tuple[str, list[Mapping[str, object]]]],
    ancestors: Sequence[str],
) -> list[list[tuple[Mapping[str, object], str]]]:
    """One layer per ancestor, outermost-first. Same split as always-join.

    Tagged ``folder_id`` wins. Untagged lists zip when the count matches that
    list; otherwise the bag sits on the outermost ancestor. No ancestors but a
    non-empty payload → one synthetic outer layer (old clouds).
    """
    flat: list[tuple[Mapping[str, object], str]] = [
        (doc, column) for column, docs in grouped for doc in docs
    ]
    if not flat:
        return []
    if not ancestors:
        return [flat]
    layers: list[list[tuple[Mapping[str, object], str]]] = [[] for _ in ancestors]
    tagged = any(str(doc.get("folder_id") or "") for doc, _ in flat)
    if tagged:
        index = {scope: i for i, scope in enumerate(ancestors)}
        for doc, column in flat:
            slot = index.get(str(doc.get("folder_id") or ""))
            if slot is not None:
                layers[slot].append((doc, column))
        return layers
    for _column, docs in grouped:
        if len(docs) == len(ancestors):
            for i, doc in enumerate(docs):
                layers[i].append((doc, _column))
        else:
            layers[0].extend((doc, _column) for doc in docs)
    return layers


def _cloud_rules(payload: Mapping[str, object], *, folder_id: str | None) -> _CloudRules:
    """Outer-to-inner live rules. Later rows overwrite the same consult name."""
    from agentcore.memory.rule_resolve import live_rule_from_mapping

    chain = cloud_scope_chain(payload, folder_id)
    out: list = []

    def _add(docs: list[Mapping[str, object]], column: str, rank: int) -> None:
        for doc in docs:
            live = live_rule_from_mapping(doc, column_apply=column, rank=rank)
            if live is not None:
                out.append(live)

    _add(_iter_cloud_rule_docs(payload, "global_rules"), "always", 0)
    _add(_iter_cloud_rule_docs(payload, "global_on_demand_rules"), "on_demand", 0)
    _add(_iter_cloud_rule_docs(payload, "global_path_rules"), "paths", 0)
    if not chain:
        return _CloudRules(rules=out, ancestor_count=0, has_current=False)

    ancestors = ancestor_scopes(chain)
    grouped = (
        ("always", _iter_cloud_rule_docs(payload, "ancestor_rules")),
        ("on_demand", _iter_cloud_rule_docs(payload, "ancestor_on_demand_rules")),
        ("paths", _iter_cloud_rule_docs(payload, "ancestor_path_rules")),
    )
    layers = _bucket_ancestor_docs(grouped, ancestors)
    for index, layer in enumerate(layers, start=1):
        for doc, column in layer:
            live = live_rule_from_mapping(doc, column_apply=column, rank=index)
            if live is not None:
                out.append(live)
    current_rank = len(layers) + 1
    _add(_iter_cloud_rule_docs(payload, "project_rules"), "always", current_rank)
    _add(_iter_cloud_rule_docs(payload, "project_on_demand_rules"), "on_demand", current_rank)
    _add(_iter_cloud_rule_docs(payload, "project_path_rules"), "paths", current_rank)
    return _CloudRules(rules=out, ancestor_count=len(layers), has_current=True)


def _user_rule_fragments_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[RuleFragment]:
    """Map ``POST /v1/account/rules/list`` into scope layers. Same name: nearest only."""
    from agentcore.memory.rule_resolve import resolve_rules

    loaded = _cloud_rules(payload, folder_id=folder_id)
    return _fragments_from_resolved(
        resolve_rules(loaded.rules),
        ancestor_count=loaded.ancestor_count,
        has_current=loaded.has_current,
    )


async def assemble_injected_rules(
    store: MemoryStore,
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    scope_chain: Sequence[str] | None = None,
    folder_user_id: str | None = None,
) -> str:
    """Load + compose this turn's ``<设定>`` body (user always-rules only).

    Display order is global → ancestors → current. AI-maintained notes are not
    loaded. ``store`` is unused on the read path (callers still pass the pipeline
    seam). Account-level rules stay on ``user_id``; folder layers use the desk
    owner when ``folder_user_id`` is set.

    ``scope_chain`` (outermost-first, current last) is resolved by the caller —
    omitting it injects the current folder only. Production entry:
    :func:`assemble_turn_rules`.
    """
    del store
    from agentcore.memory.rule_resolve import resolve_rules

    chain = tuple(scope_chain) if scope_chain is not None else own_scope_chain(folder_id)
    folder_actor = folder_user_id or user_id
    ancestors = ancestor_scopes(chain)
    live = await _scope_live_rules(repo, user_id, None, 0)
    for index, scope in enumerate(ancestors, start=1):
        live.extend(await _scope_live_rules(repo, folder_actor, scope, index))
    if chain:
        live.extend(
            await _scope_live_rules(repo, folder_actor, chain[-1], len(ancestors) + 1)
        )
    return compose_injected_rules(
        _fragments_from_resolved(
            resolve_rules(live),
            ancestor_count=len(ancestors),
            has_current=bool(chain),
        )
    )


@dataclass(frozen=True)
class TurnRuleView:
    """常驻正文 plus bounded path rules for this desk."""

    settings: str = ""
    path_rules: tuple = ()

    @property
    def path_index(self) -> str:
        from agentcore.documents.path_rules import render_path_index

        return render_path_index(self.path_rules)


async def load_turn_rule_view(
    store: MemoryStore,
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None = None,
) -> TurnRuleView:
    """常驻 ``<设定>`` body and the path-rule index inputs. Errors → empty."""
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot
    from agentcore.memory.rule_resolve import resolve_rules

    try:
        folder_actor = folder_user_id or user_id
        creds = get_account_credentials()
        if creds is not None and folder_actor == user_id:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return TurnRuleView()
            payload = snap.rules_payload
            loaded = _cloud_rules(payload, folder_id=folder_id)
            resolved = resolve_rules(loaded.rules)
            settings = compose_injected_rules(
                _fragments_from_resolved(
                    resolved,
                    ancestor_count=loaded.ancestor_count,
                    has_current=loaded.has_current,
                )
            )
            return TurnRuleView(settings=settings, path_rules=resolved.path)
        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            chain = await db_scope_chain(folder_actor, folder_id, session=session)
            ancestors = ancestor_scopes(chain)
            live = await _scope_live_rules(repo, user_id, None, 0)
            for index, scope in enumerate(ancestors, start=1):
                live.extend(await _scope_live_rules(repo, folder_actor, scope, index))
            if chain:
                live.extend(
                    await _scope_live_rules(
                        repo, folder_actor, chain[-1], len(ancestors) + 1
                    )
                )
            resolved = resolve_rules(live)
            settings = compose_injected_rules(
                _fragments_from_resolved(
                    resolved,
                    ancestor_count=len(ancestors),
                    has_current=bool(chain),
                )
            )
            return TurnRuleView(settings=settings, path_rules=resolved.path)
    except Exception as e:  # noqa: BLE001 - user rules must never break a turn's assembly
        logger.warning("memory.user_rules_load_failed", user_id=user_id, error=str(e))
        return TurnRuleView()


async def assemble_turn_rules(
    store: MemoryStore,
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None = None,
) -> str:
    """Turn-time convenience over :func:`assemble_injected_rules` (the pipeline entry point).

    With account creds, prepare reads the process snapshot cache only (warm seeds it);
    miss → empty injection — never await cloud HTTP on the turn hot path. User-rule
    loading degrades to「no rules」on ANY error so injection can never break a turn.

    Nested folders inherit outside-in (§5.4): the ancestor chain comes from the warm
    snapshot on the ticketed path and from ``folders.rel_path`` otherwise.
    """
    view = await load_turn_rule_view(
        store, user_id, folder_id=folder_id, folder_user_id=folder_user_id
    )
    return view.settings


# --- on-demand user rules (规则目录 + consult_rule; NOT memory topics) ----------------------


@dataclass(frozen=True)
class OnDemandUserRule:
    """One entry in the「规则目录」: consult name + optional one-line summary.

    On-demand rules are constraint appendices (应遵守), listed in the consult directory.
    """

    name: str
    summary: str = ""


def rule_consult_name(doc_name: str) -> str:
    """Normalize a rule document filename to the name models pass to ``consult_rule``."""
    return doc_name.removesuffix(".md").strip()


def _iter_cloud_rule_docs(payload: Mapping[str, object], key: str) -> list[Mapping[str, object]]:
    """Normalize ``payload[key]`` to a list of mapping docs (skip junk)."""
    raw = payload.get(key) or []
    if not isinstance(raw, list):
        return []
    return [doc for doc in raw if isinstance(doc, Mapping)]


def _resolved_from_cloud(payload: Mapping[str, object], *, folder_id: str | None):
    from agentcore.memory.rule_resolve import resolve_rules

    return resolve_rules(_cloud_rules(payload, folder_id=folder_id).rules)


def on_demand_user_rules_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[OnDemandUserRule]:
    """按需 winners with a non-empty description. Same name: nearest desk only."""
    resolved = _resolved_from_cloud(payload, folder_id=folder_id)
    return [
        OnDemandUserRule(name=rule.name, summary=rule.description)
        for rule in resolved.on_demand
    ]


def lookup_on_demand_rule_body_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None, name: str
) -> str | None:
    """Body of the nearest on-demand winner. A nearer always/path rule hides the name."""
    key = rule_consult_name(name)
    if not key:
        return None
    resolved = _resolved_from_cloud(payload, folder_id=folder_id)
    for rule in resolved.on_demand:
        if rule.name == key:
            return _consult_body(rule.content)
    return None


def _consult_body(content: str) -> str | None:
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return None
    text = parsed.body if parsed.has_frontmatter else content
    return text if text.strip() else None


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
            return on_demand_user_rules_from_cloud(snap.rules_payload, folder_id=folder_id)
        async with async_session_factory() as session:
            from agentcore.memory.rule_resolve import resolve_rules

            repo = DocumentRepository(session)
            live = await _scope_live_rules(repo, user_id, None, 0)
            chain = await db_scope_chain(user_id, folder_id, session=session)
            for index, scope in enumerate(chain, start=1):
                live.extend(await _scope_live_rules(repo, user_id, scope, index))
            resolved = resolve_rules(live)
            return [
                OnDemandUserRule(name=rule.name, summary=rule.description)
                for rule in resolved.on_demand
            ]
    except Exception as e:  # noqa: BLE001 - must never break turn assembly
        logger.warning("memory.on_demand_rules_load_failed", user_id=user_id, error=str(e))
        return []


async def lookup_on_demand_rule_body(
    user_id: str, *, folder_id: str | None, name: str
) -> str | None:
    """Nearest on-demand body. A nearer always or path rule hides the name."""
    key = rule_consult_name(name)
    if not key:
        return None
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot

    try:
        if get_account_credentials() is not None:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return None
            return lookup_on_demand_rule_body_from_cloud(
                snap.rules_payload, folder_id=folder_id, name=key
            )
        async with async_session_factory() as session:
            from agentcore.memory.rule_resolve import resolve_rules

            repo = DocumentRepository(session)
            live = await _scope_live_rules(repo, user_id, None, 0)
            chain = await db_scope_chain(user_id, folder_id, session=session)
            for index, scope in enumerate(chain, start=1):
                live.extend(await _scope_live_rules(repo, user_id, scope, index))
            for rule in resolve_rules(live).on_demand:
                if rule.name == key:
                    return _consult_body(rule.content)
            return None
    except Exception as e:  # noqa: BLE001 — never break consult over rules IO
        logger.warning(
            "consult.rule_fetch_failed", user_id=user_id, name=key, error=str(e)
        )
        return None

