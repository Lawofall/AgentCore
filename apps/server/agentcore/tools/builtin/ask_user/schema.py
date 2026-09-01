"""Card field normalization and caps for ask_user."""

from __future__ import annotations

import json
import re
from typing import Any

from agentcore.core.paths import is_absolute_os_path

# Caps so a runaway prompt can't bloat the card / event. The free-form note on the
# card always lets the user steer beyond these.
_MAX_QUESTIONS = 5  # 开场重点问题最多 5 个（对齐 Cursor 2.1 的 3–5）
_MAX_OPTIONS = 6  # 每个 choice 问题的选项上限
_MAX_OPTION_DETAIL = 120  # 单个选项的权衡说明上限（一行内）
_MAX_TARGET_NAME = 120  # grant_* target_name 截断上限
_WELL_KNOWN_DIRS = frozenset({"desktop", "downloads", "documents"})
_LOCAL_PROJECT_ACTIONS: tuple[str, ...] = (
    "open_local_project",
    "register_local_project",
    "bind_local_folder",
)
_GRANT_ORGANIZE = "grant_organize_folder"
_GRANT_ATTACH = "grant_attach_folder"
_ALLOWED_OPTION_ACTIONS = frozenset(
    (*_LOCAL_PROJECT_ACTIONS, _GRANT_ORGANIZE, _GRANT_ATTACH)
)


def advertised_option_actions(
    *, desktop: bool, workspace_location: str | None
) -> tuple[str, ...]:
    """Desktop AskOption.action enum for this turn's workspace location.

    Cloud / unknown：本机传统入口 + 整理，不广告 attach_rw。
    已在本机传统：整理 + 区外旁根 attach，不再广告 open/register/bind。
    """
    if not desktop:
        return ()
    if (workspace_location or "").strip().lower() == "local":
        return (_GRANT_ORGANIZE, _GRANT_ATTACH)
    return (*_LOCAL_PROJECT_ACTIONS, _GRANT_ORGANIZE)
_MAX_ASSUMPTIONS = 10
_MAX_ASSUMPTION_LABEL = 8  # 短项名原样保留；更长并入 value，项名改「假设」
_FALLBACK_ASSUMPTION_LABEL = "假设"

# Claude Code-style tendency: the advised option is first, name ends with
# 「（推荐）」or (recommended). Bare「推荐」in a product name stays unmarked.
_LABEL_RECOMMENDATION_MARK = re.compile(
    r"[（(【\[]\s*推荐\s*[）)】\]]|[（(【\[]\s*recommended\s*[）)】\]]",
    re.IGNORECASE,
)


class ListArgError(ValueError):
    """Non-list tool arg that cannot be coerced to a JSON array (e.g. double-encoded junk)."""


# Markdown bullet / numbered list line → capture the item body.
_MD_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+)$")


def split_markdown_list_items(text: str) -> list[str] | None:
    """If ``text`` looks like a markdown bullet/numbered list, return item bodies.

    Used by handoff ``key_points`` loose parse (models often emit ``"- a\\n- b"`` instead
    of a JSON array). Returns ``None`` when the string is not a list shape so callers can
    fall back to wrapping the whole string as a single item.
    """
    if not text or not text.strip():
        return None
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    items: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        m = _MD_LIST_ITEM_RE.match(stripped)
        if not m:
            return None
        body = m.group(1).strip()
        if body:
            items.append(body)
    return items or None


def coerce_list_arg(
    raw: Any, *, field: str, allow_markdown_bullets: bool = False
) -> list[Any]:
    """Accept a real list, or a single JSON-encoded array string (common model fumble).

    Empty / missing → ``[]``. A non-empty string that is not a JSON array raises
    :class:`ListArgError` so the tool can reject instead of silently dropping options.
    When ``allow_markdown_bullets`` is True, a markdown bullet/numbered list string is
    accepted as a list (handoff ``key_points``); truly bad JSON still fails.
    """
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            if allow_markdown_bullets:
                md_items = split_markdown_list_items(text)
                if md_items is not None:
                    return md_items
            raise ListArgError(f"{field} 须为数组；收到无法解析的 JSON 字符串。") from exc
        if isinstance(parsed, list):
            return parsed
        if allow_markdown_bullets and isinstance(parsed, str):
            md_items = split_markdown_list_items(parsed)
            if md_items is not None:
                return md_items
            return [parsed] if parsed.strip() else []
        raise ListArgError(
            f"{field} 须为数组；JSON 字符串解析结果为 {type(parsed).__name__}，不是数组。"
        )
    return []


def option_label(opt: Any) -> str:
    """The canonical label of a choice option, tolerant of both shapes.

    Options normalize to ``{label, detail?}`` dicts (``detail`` only for dedicated
    cards), but a durable frame persisted before that change (or a hand-built
    test) may still carry a bare string — both the live tool and a resume read labels
    through here so an old paused turn still settles. The label is the answer value
    (答复模型 α): no separate wire value exists. Tendency lives in the name
    (``（推荐）`` / ``(recommended)``), not a separate flag.
    """
    if isinstance(opt, dict):
        return str(opt.get("label") or "").strip()
    return str(opt).strip()


def option_label_is_recommended(label: str) -> bool:
    """True when the option name carries Claude Code-style recommendation markup."""
    return bool(_LABEL_RECOMMENDATION_MARK.search(label))


def normalize_options(
    raw: Any,
    *,
    max_options: int = _MAX_OPTIONS,
    keep_detail: bool = False,
) -> list[dict[str, Any]]:
    """Cap choice options, accepting either bare strings or rich objects.

    Default cap is 6 (ordinary choice). ``card=organize_plan`` / ``daily_review``
    raise the cap to their list hats. A bare ``"Postgres"`` becomes
    ``{"label": "Postgres"}``; an object may add ``action`` (a desktop client action
    such as ``open_local_project`` / ``register_local_project`` / ``bind_local_folder``
    — unknown values drop so a hallucinated action never reaches the wire).
    ``detail`` (the one-line trade-off under the label) is kept only when
    ``keep_detail`` is true — dedicated cards ``organize_plan`` / ``daily_review``.
    Ordinary short asks and escalate drop it even if the model filled it; put the
    trade-off in ``label``. For ``grant_organize_folder`` only,
    ``well_known`` (``desktop`` / ``downloads`` / ``documents``), ``target_name``
    (basename fuzzy token; path separators rejected; truncated ≤120), and absolute
    ``path`` (C1 mount transport; non-absolute dropped) pass through — dropping
    ``detail`` must not strip these. Empty-label entries drop. Names may carry
    ``（推荐）`` / ``(recommended)``.
    """
    cap = max(1, int(max_options))
    items = coerce_list_arg(raw, field="options")
    out: list[dict[str, Any]] = []
    for it in items:
        label = option_label(it)
        if not label:
            continue
        opt: dict[str, Any] = {"label": label}
        if isinstance(it, dict):
            if keep_detail:
                detail = str(it.get("detail") or "").strip()
                if detail:
                    opt["detail"] = detail[:_MAX_OPTION_DETAIL]
            action = str(it.get("action") or "").strip()
            if action in _ALLOWED_OPTION_ACTIONS:
                opt["action"] = action
            # grant_* folder hints for desktop one-click / resolve-then-grant
            # (drop otherwise; unknown actions already omitted above).
            if action in {"grant_organize_folder", "grant_attach_folder"}:
                well_known = str(it.get("well_known") or "").strip().lower()
                if well_known in _WELL_KNOWN_DIRS:
                    opt["well_known"] = well_known
                target_name = str(it.get("target_name") or "").strip()
                if target_name and "/" not in target_name and "\\" not in target_name:
                    opt["target_name"] = target_name[:_MAX_TARGET_NAME]
                # Absolute only — matches desktop resolveGrantAbsPath (no CWD-relative).
                # Absoluteness is the client's, not this host's: the API runs on Linux
                # and most desks are Windows.
                grant_path = str(it.get("path") or "").strip()
                if grant_path and is_absolute_os_path(grant_path):
                    opt["path"] = grant_path[:512]
            # organize_plan structured fields (passed through for plan binding).
            op = str(it.get("op") or "").strip()
            if op in ("move", "copy", "delete", "mkdir"):
                opt["op"] = op
                if op in ("move", "copy"):
                    src = str(it.get("source") or "").strip()
                    dst = str(it.get("destination") or "").strip()
                    if src:
                        opt["source"] = src
                    if dst:
                        opt["destination"] = dst
                else:
                    p = str(it.get("path") or "").strip()
                    if p:
                        opt["path"] = p
            # daily_review structured fields (server apply on confirm).
            review_kind = str(it.get("review_kind") or "").strip()
            if review_kind in ("preference", "profile", "topic", "rule", "doc"):
                opt["review_kind"] = review_kind
                body = str(it.get("body") or "").strip()
                if body:
                    opt["body"] = body[:4000]
                slug = str(it.get("slug") or "").strip()
                if slug:
                    opt["slug"] = slug[:64]
                section = str(it.get("section") or "").strip()
                if section:
                    opt["section"] = section[:64]
                rpath = str(it.get("path") or "").strip()
                if rpath and review_kind == "doc":
                    opt["path"] = rpath[:240]
        out.append(opt)
        if len(out) >= cap:
            break
    return out


def normalize_assumptions(raw: Any) -> list[dict[str, Any]]:
    """Cap + id the 起步计划 chips, dropping malformed / empty-label entries.

    Short labels (≤8 字) stay as-is. Longer ones fold into ``value``
    (``label：value`` when value is non-empty) and the chip name becomes「假设」—
    a runaway item name never rejects the ask.
    """
    items = coerce_list_arg(raw, field="assumptions")
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items[:_MAX_ASSUMPTIONS]):
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()
        if not label:
            continue
        value = str(it.get("value") or "").strip()
        if len(label) > _MAX_ASSUMPTION_LABEL:
            value = f"{label}：{value}" if value else label
            label = _FALLBACK_ASSUMPTION_LABEL
        out.append({"id": f"a{i}", "label": label, "value": value})
    return out


def _question_prompt(it: dict[str, Any]) -> str:
    """Ask stem: ``prompt`` wins; ``question`` is the Claude-pretrained alias."""
    return str(it.get("prompt") or it.get("question") or "").strip()


def _options_absent(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, list) and not raw:
        return True
    return bool(isinstance(raw, str) and not raw.strip())


def _flattened_option_raw(it: dict[str, Any]) -> list[dict[str, Any]]:
    """Option fields written on the question (no ``options`` array)."""
    label = str(it.get("label") or "").strip()
    if not label:
        return []
    raw: dict[str, Any] = {"label": label}
    detail = str(it.get("detail") or "").strip()
    if detail:
        raw["detail"] = detail
    return [raw]


def normalize_questions(
    raw: Any,
    *,
    max_options: int = _MAX_OPTIONS,
    keep_detail: bool = False,
) -> list[dict[str, Any]]:
    """Cap (≤5) + id the questions, normalizing kind/options/multiple/default.

    ``default`` is optional here (unlike the old kickoff): an opening question should
    pre-fill one, but a mid-task fork usually wants the user to actively choose, so it
    is left empty when the CEO omits it.     ``max_options`` / ``keep_detail`` forward to
    :func:`normalize_options` (cap raised for ``organize_plan`` / ``daily_review``;
    ``keep_detail`` only for those dedicated cards).

    Choice with no options after absorb is lowered to ``text`` so the card is
    fill-in, never a zero-button choice. A question-level ``label`` with absent
    ``options`` becomes a one-item choice (model flattened the option onto the
    question). ``question`` is accepted as an alias for ``prompt``.
    """
    items = coerce_list_arg(raw, field="questions")
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items[:_MAX_QUESTIONS]):
        if not isinstance(it, dict):
            continue
        prompt = _question_prompt(it)
        if not prompt:
            continue
        kind = "text" if str(it.get("kind") or "").strip() == "text" else "choice"
        if kind == "choice":
            raw_options = it.get("options")
            if _options_absent(raw_options):
                absorbed = _flattened_option_raw(it)
                if absorbed:
                    raw_options = absorbed
            options = normalize_options(
                raw_options, max_options=max_options, keep_detail=keep_detail
            )
            multiple = bool(it.get("multiple") or False)
            default = str(it.get("default") or "").strip()
            # Models sometimes put a desktop action on the question. Promote only
            # onto the intended choice — never every option (a sibling "skip /
            # 口头汇报" must not inherit register/open/bind).
            q_action = str(it.get("action") or "").strip()
            if q_action in _ALLOWED_OPTION_ACTIONS:
                targets: list[dict[str, Any]] = []
                if default:
                    targets = [
                        opt
                        for opt in options
                        if opt.get("label") == default and "action" not in opt
                    ]
                if not targets and options and "action" not in options[0]:
                    targets = [options[0]]
                for opt in targets:
                    opt["action"] = q_action
            if not options:
                kind = "text"
                options = []
                multiple = False
                default = ""
        else:
            options = []
            multiple = False
            default = ""
        out.append(
            {
                "id": f"q{i}",
                "prompt": prompt,
                "kind": kind,
                "options": options,
                "multiple": multiple,
                "default": default,
            }
        )
    return out
