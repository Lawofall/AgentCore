"""Markdown injection (Agent记忆与知识系统).

A saved markdown document enters this turn only when it has a channel.
Account documents follow assembly membership. The current folder's documents
enter only while the turn sits at that desk. Ancestors are not a scope.

常驻 bodies go in ``<设定>`` (account, then this folder). 按需 is a catalog
line plus ``consult``. 碰到文件 follows the path. Frontmatter is stripped
before the model sees a consulted body (``agentcore.documents.frontmatter``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from agentcore.core.logging import get_logger
from agentcore.db.repositories import DocumentRepository
from agentcore.documents.frontmatter import (
    ApplyMode,
    FrontmatterEditError,
    FrontmatterError,
    parse_entry_frontmatter,
    set_entry_frontmatter,
    strip_entry_frontmatter,
)
from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
)

logger = get_logger(__name__)

# Layer label inside the shared <设定> block (this folder, not an ancestor).
_FOLDER_SETTINGS_LABEL = "（以下为「当前文件夹」专属设定，仅在本文件夹内适用）"

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
    """The caller's mode, else frontmatter, else the saved mode. Unset stays 按需."""
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return parsed
    if apply in ("always", "on_demand", "paths"):
        return apply  # type: ignore[return-value]
    if parsed.apply_present:
        return parsed.apply
    if existing_apply in ("always", "on_demand", "paths"):
        return existing_apply  # type: ignore[return-value]
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
    """Shared mutate outcome for account ``/rules/write|read|delete``."""

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
    """Persist one named user-rule markdown (toolbox / account shared).

    Description is derived from the body on every write.
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
        AlwaysUsage,
        always_entry_chars,
        check_always_write,
        notify_always_quota_exceeded,
    )
    from agentcore.memory.rule_resolve import counts_as_always_content

    new_is_always = counts_as_always_content(body)
    editing_existing = existing is not None and counts_as_always_content(
        existing.content or ""
    )
    if new_is_always or editing_existing:
        decision = await check_always_write(
            repo,
            user_id,
            folder_id=folder_id,
            writer="user",
            editing_existing_always=editing_existing,
            exclude_id=getattr(existing, "id", None) if existing is not None else None,
            new_content=body,
            new_is_always=new_is_always,
        )
        if not decision.allowed:
            usage = decision.usage or AlwaysUsage(used_chars=0, max_chars=0)
            exc = AlwaysQuotaExceededError(
                usage,
                decision.message,
                file=filename,
                scope=folder_id,
                attempted_chars=always_entry_chars(body) if new_is_always else 0,
            )
            await notify_always_quota_exceeded(user_id, exc)
            raise exc

    doc = await repo.upsert_user_rule_doc(
        user_id,
        folder_id,
        filename,
        text,
        apply=apply_mode,
        description=description,
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
    return f"### {title}\n{body}"


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


async def _scope_live_rules(
    repo: DocumentRepository, user_id: str, folder_id: str | None, rank: int
):
    """Saved markdown of one scope. Rank 0 is the account; rank 1 is this folder."""
    from agentcore.assembly.bind import current_omit_desk_rules, current_skill_membership
    from agentcore.memory.rule_resolve import LiveRule, live_rule_from_doc

    if folder_id is not None and current_omit_desk_rules():
        return []
    membership = current_skill_membership() if folder_id is None else None
    out: list[LiveRule] = []
    loaded = await repo.list_scoped_markdown(user_id, folder_id)
    for doc in loaded:
        override = None
        if membership is not None:
            mode = membership.get(str(getattr(doc, "id", "") or ""))
            if mode is None:
                continue
            override = mode
        live = live_rule_from_doc(
            doc, column_apply="", rank=rank, apply_override=override
        )
        if live is not None:
            out.append(live)
    return out


def _settings_from_resolved(resolved) -> str:
    """Account 常驻, then this folder's 常驻. Empty layers drop out."""
    account: list[str] = []
    folder: list[str] = []
    for rule in resolved.always:
        body = _labeled_rule_body(f"{rule.name}.md", rule.content)
        if not body:
            continue
        if rule.rank == 0:
            account.append(body)
        else:
            folder.append(body)
    parts: list[str] = []
    if account:
        parts.append("\n\n".join(account))
    if folder:
        parts.append(f"{_FOLDER_SETTINGS_LABEL}\n" + "\n\n".join(folder))
    return "\n\n".join(parts)


async def _user_rule_fragments(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None = None,
) -> list[RuleFragment]:
    """Account ``<设定>``, then this folder. Ancestors are not a scope."""
    resolved = await _resolved_pair(
        repo, user_id, folder_id=folder_id, folder_user_id=None
    )
    text = _settings_from_resolved(resolved)
    return [RuleFragment(body=text)] if text else []


async def _resolved_pair(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None,
):
    """Account markdown for ``user_id``, plus this folder's markdown for its owner."""
    from agentcore.memory.rule_resolve import resolve_rules

    account = await _scope_live_rules(repo, user_id, None, 0)
    folder: list = []
    if folder_id:
        owner = folder_user_id or user_id
        folder = await _scope_live_rules(repo, owner, folder_id, 1)
    return resolve_rules([*account, *folder])


@dataclass(frozen=True)
class _CloudRules:
    rules: list
    ancestor_count: int
    has_current: bool


def _cloud_rules(payload: Mapping[str, object], *, folder_id: str | None) -> _CloudRules:
    """Account lists plus this folder's lists. Ancestor keys are not a scope.

    The list name is the channel when the cached body has no ``apply`` key.
    ``folder_id`` selects whether this folder's lists are in scope.
    """
    from agentcore.assembly.bind import current_omit_desk_rules, current_skill_membership
    from agentcore.memory.rule_resolve import live_rule_from_mapping

    membership = current_skill_membership()
    omit_folder = current_omit_desk_rules() or folder_id is None
    out: list = []

    def _add(docs: list[Mapping[str, object]], rank: int, channel: str) -> None:
        if rank != 0 and omit_folder:
            return
        for doc in docs:
            override = None
            if rank == 0 and membership is not None:
                doc_id = str(doc.get("id") or "")
                mode = membership.get(doc_id) if doc_id else None
                if mode is None:
                    continue
                override = mode
            live = live_rule_from_mapping(
                doc, column_apply=channel, rank=rank, apply_override=override
            )
            if live is not None:
                out.append(live)

    _add(_iter_cloud_rule_docs(payload, "global_rules"), 0, "always")
    _add(_iter_cloud_rule_docs(payload, "global_on_demand_rules"), 0, "on_demand")
    _add(_iter_cloud_rule_docs(payload, "global_path_rules"), 0, "paths")
    _add(_iter_cloud_rule_docs(payload, "project_rules"), 1, "always")
    _add(_iter_cloud_rule_docs(payload, "project_on_demand_rules"), 1, "on_demand")
    _add(_iter_cloud_rule_docs(payload, "project_path_rules"), 1, "paths")
    return _CloudRules(rules=out, ancestor_count=0, has_current=bool(out))


def _user_rule_fragments_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[RuleFragment]:
    """``<设定>`` slices from ``POST /v1/account/rules/list``."""
    from agentcore.memory.rule_resolve import resolve_rules

    loaded = _cloud_rules(payload, folder_id=folder_id)
    text = _settings_from_resolved(resolve_rules(loaded.rules))
    return [RuleFragment(body=text)] if text else []


async def assemble_injected_rules(
    repo: DocumentRepository,
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None = None,
) -> str:
    """Load + compose this turn's ``<设定>`` body.

    Account 常驻, then the current folder's 常驻. Ancestors are not a scope.
    """
    resolved = await _resolved_pair(
        repo, user_id, folder_id=folder_id, folder_user_id=folder_user_id
    )
    return _settings_from_resolved(resolved)


@dataclass(frozen=True)
class TurnRuleView:
    """``<设定>`` body plus path rules for this turn."""

    settings: str = ""
    path_rules: tuple = ()

    @property
    def path_index(self) -> str:
        from agentcore.documents.path_rules import render_path_index

        return render_path_index(self.path_rules)


async def load_turn_rule_view(
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None = None,
    reader: object | None = None,
) -> TurnRuleView:
    """``<设定>`` body and path rules. Errors → empty, so injection cannot break a turn."""
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot
    from agentcore.memory.rule_resolve import resolve_rules
    from agentcore.memory.workspace_files import (
        current_workspace_markdown_reader,
        discover_workspace_rules,
        workspace_markdown_reader_scope,
    )

    active_reader = reader if reader is not None else current_workspace_markdown_reader()
    try:
        with workspace_markdown_reader_scope(active_reader):
            extra = await discover_workspace_rules(active_reader)
        folder_actor = folder_user_id or user_id
        creds = get_account_credentials()
        if creds is not None and folder_actor == user_id:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                resolved = resolve_rules(extra)
                return TurnRuleView(
                    settings=_settings_from_resolved(resolved),
                    path_rules=resolved.path,
                )
            loaded = _cloud_rules(snap.rules_payload, folder_id=folder_id)
            resolved = resolve_rules([*loaded.rules, *extra])
            return TurnRuleView(
                settings=_settings_from_resolved(resolved),
                path_rules=resolved.path,
            )
        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            account = await _scope_live_rules(repo, user_id, None, 0)
            folder: list = []
            if folder_id:
                owner = folder_user_id or user_id
                folder = await _scope_live_rules(repo, owner, folder_id, 1)
            resolved = resolve_rules([*account, *folder, *extra])
            return TurnRuleView(
                settings=_settings_from_resolved(resolved),
                path_rules=resolved.path,
            )
    except Exception as e:  # noqa: BLE001 - user rules must never break a turn's assembly
        logger.warning("memory.user_rules_load_failed", user_id=user_id, error=str(e))
        return TurnRuleView()


async def assemble_turn_rules(
    user_id: str,
    *,
    folder_id: str | None,
    folder_user_id: str | None = None,
) -> str:
    """Turn-time convenience over :func:`assemble_injected_rules` (the pipeline entry point).

    With account creds, prepare reads the process snapshot cache only (warm seeds it);
    miss → empty injection — never await cloud HTTP on the turn hot path. Loading
    degrades to no markdown on any error so injection cannot break a turn.
    """
    view = await load_turn_rule_view(
        user_id, folder_id=folder_id, folder_user_id=folder_user_id
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


def _merge_file_catalog(
    rules: list[OnDemandUserRule], extra: Sequence[object]
) -> list[OnDemandUserRule]:
    from agentcore.memory.rule_resolve import LiveRule

    replaced: dict[str, str] = {}
    for row in extra:
        if not isinstance(row, LiveRule):
            continue
        if row.apply != "on_demand" or not row.description.strip():
            continue
        replaced[row.name] = row.description
    out: list[OnDemandUserRule] = []
    seen: set[str] = set()
    for rule in rules:
        if rule.name in seen:
            continue
        seen.add(rule.name)
        if rule.name in replaced:
            out.append(OnDemandUserRule(name=rule.name, summary=replaced[rule.name]))
        else:
            out.append(rule)
    for name, summary in replaced.items():
        if name not in seen:
            out.append(OnDemandUserRule(name=name, summary=summary))
    return out


async def _workspace_on_demand(
    reader: object | None,
) -> list:
    from agentcore.memory.workspace_files import discover_workspace_rules

    return await discover_workspace_rules(reader)


def on_demand_user_rules_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None
) -> list[OnDemandUserRule]:
    """On-demand rows for the account and, when present, this folder."""
    resolved = _resolved_from_cloud(payload, folder_id=folder_id)
    return [
        OnDemandUserRule(name=rule.name, summary=rule.description)
        for rule in resolved.on_demand
    ]


def lookup_on_demand_rule_body_from_cloud(
    payload: Mapping[str, object], *, folder_id: str | None, name: str
) -> str | None:
    """Body for ``name``. The current folder wins over the account on a clash."""
    key = rule_consult_name(name)
    if not key:
        return None
    resolved = _resolved_from_cloud(payload, folder_id=folder_id)
    match = [rule for rule in resolved.on_demand if rule.name == key]
    if not match:
        return None
    chosen = max(match, key=lambda rule: rule.rank)
    return _consult_body(chosen.content)


def _consult_body(content: str) -> str | None:
    parsed = parse_entry_frontmatter(content)
    if isinstance(parsed, FrontmatterError):
        return None
    text = parsed.body if parsed.has_frontmatter else content
    return text if text.strip() else None


async def load_on_demand_user_rules(
    user_id: str, *, folder_id: str | None, reader: object | None = None
) -> list[OnDemandUserRule]:
    """On-demand catalog for the account and, when set, this folder.

    Degrades to [] on any error.
    Account-ticketed turns read the process prepare snapshot only (warm seeds it;
    miss → []); local / server turns read the document session. Workspace
    ``AgentCore/rules`` files overlay the same names at folder rank.
    """
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot
    from agentcore.memory.workspace_files import current_workspace_markdown_reader

    active_reader = reader if reader is not None else current_workspace_markdown_reader()
    try:
        extra = await _workspace_on_demand(active_reader)
        creds = get_account_credentials()
        if creds is not None:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return _merge_file_catalog([], extra)
            return _merge_file_catalog(
                on_demand_user_rules_from_cloud(snap.rules_payload, folder_id=folder_id),
                extra,
            )
        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            resolved = await _resolved_pair(
                repo, user_id, folder_id=folder_id, folder_user_id=None
            )
            rules = [
                OnDemandUserRule(name=rule.name, summary=rule.description)
                for rule in resolved.on_demand
            ]
            return _merge_file_catalog(rules, extra)
    except Exception as e:  # noqa: BLE001 - must never break turn assembly
        logger.warning("memory.on_demand_rules_load_failed", user_id=user_id, error=str(e))
        return []


async def lookup_on_demand_rule_body(
    user_id: str, *, folder_id: str | None, name: str, reader: object | None = None
) -> str | None:
    """On-demand body. Workspace files win over the account when the name clashes."""
    key = rule_consult_name(name)
    if not key:
        return None
    from agentcore.account.credentials import get_account_credentials
    from agentcore.db.base import async_session_factory
    from agentcore.memory.account_prepare_cache import get_account_rules_memory_snapshot
    from agentcore.memory.workspace_files import (
        current_workspace_markdown_reader,
        matching_workspace_rule,
    )

    active_reader = reader if reader is not None else current_workspace_markdown_reader()
    try:
        extra = await _workspace_on_demand(active_reader)
        match = matching_workspace_rule(extra, key)
        if match is not None and match.apply == "on_demand" and match.description.strip():
            return _consult_body(match.content)
        if get_account_credentials() is not None:
            snap = get_account_rules_memory_snapshot(user_id, folder_id)
            if snap is None:
                return None
            return lookup_on_demand_rule_body_from_cloud(
                snap.rules_payload, folder_id=folder_id, name=key
            )
        async with async_session_factory() as session:
            repo = DocumentRepository(session)
            resolved = await _resolved_pair(
                repo, user_id, folder_id=folder_id, folder_user_id=None
            )
            found = [rule for rule in resolved.on_demand if rule.name == key]
            if not found:
                return None
            chosen = max(found, key=lambda rule: rule.rank)
            return _consult_body(chosen.content)
    except Exception as e:  # noqa: BLE001 — never break consult over rules IO
        logger.warning(
            "consult.rule_fetch_failed", user_id=user_id, name=key, error=str(e)
        )
        return None

