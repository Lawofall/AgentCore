"""Card field normalization and caps for ask_user."""

from __future__ import annotations

import json
import re
from typing import Any

# Caps so a runaway prompt can't bloat the card / event. The free-form note on the
# card always lets the user steer beyond these.
_MAX_QUESTIONS = 5  # 开场重点问题最多 5 个（对齐 Cursor 2.1 的 3–5）
_MAX_OPTIONS = 6  # 每个 choice 问题的选项上限
# Appended to every ask_user reject: the fix is a silent resend, not something to
# narrate — without this the model tends to recount the failed call in its visible
# prose ("The ask_user call failed because…"), which reads as fumbling to the user.
ASK_RETRY_HINT = "改对后直接重发 ask_user 即可；不要在给用户看的正文里复述这次调用失败。"
_LOCAL_PROJECT_ACTIONS: tuple[str, ...] = (
    "open_local_project",
    "register_local_project",
    "bind_local_folder",
)
_ALLOWED_OPTION_ACTIONS = frozenset(_LOCAL_PROJECT_ACTIONS)


def advertised_option_actions(
    *, desktop: bool, workspace_location: str | None
) -> tuple[str, ...]:
    """Open / bind folder is a human entry (Composer / 文件树 / 交付卡芯片).

    Never advertised on the ``ask_user`` button. ``desktop`` / location are
    accepted so assemble / resume signatures stay; the enum is always empty.
    Wire still accepts known actions on already-persisted cards.
    """
    _ = (desktop, workspace_location)
    return ()


# Shared questions[] card shape (ask_user + escalate). Per-tool overlays:
# array description / minItems。option.action 不进按钮（开夹走人侧）。
# 填卡合同只叠在 ask_user（写参当轮必见）；escalate 不抄推荐 / 桌上结果。
# 倾向只在 label 名末「（推荐）」；不广告 questions[].default。
# 卡种由有无 options 决定，不广告 kind。
_PROMPT_DESC = "问句。"
_OPTIONS_DESC = f"候选项（最多 {_MAX_OPTIONS}）。"
_LABEL_DESC = "选项名（回传答案）。"
_MULTIPLE_DESC = "允许多选。"

# WHEN 短触发。填卡合同在 prompt/label。consult 赶不上这张卡。
ASK_WHEN = "向用户发问。"
ASK_PROMPT_HOW = "要什么 / 给谁 / 做到哪一档。问句在卡上 ≠ 再抄进正文。假设和背景写正文。"
ASK_LABEL_HOW = (
    "桌上结果；权衡写进选项名 ≠ 编制套餐 ≠ 正文候选再投卡。"
    "有倾向时该项第一、名末「（推荐）」。"
)


def questions_array_schema(
    *,
    description: str,
    min_items: int | None = None,
    option_properties: dict[str, Any] | None = None,
    prompt_description: str | None = None,
) -> dict[str, Any]:
    """JSON Schema for ``questions`` — one card shape, two callers.

    ``option_properties`` merge onto ``{label}``.
    Escalate omits ``minItems``; array description stays per-tool.
    ``action`` is never advertised (Composer / 交付卡芯片).
    Tendency is label markup only; ``default`` is not advertised.
    """
    option_props: dict[str, Any] = {
        "label": {"type": "string", "description": _LABEL_DESC},
    }
    if option_properties:
        option_props.update(option_properties)
    schema: dict[str, Any] = {
        "type": "array",
        "description": description,
        "items": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": prompt_description or _PROMPT_DESC,
                },
                "options": {
                    "type": "array",
                    "description": _OPTIONS_DESC,
                    "items": {
                        "type": "object",
                        "properties": option_props,
                        "required": ["label"],
                    },
                },
                "multiple": {
                    "type": "boolean",
                    "description": _MULTIPLE_DESC,
                },
            },
            "required": ["prompt"],
        },
    }
    if min_items is not None:
        schema["minItems"] = min_items
    return schema


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

    Options normalize to ``{label}`` dicts (plus optional ``action``), but a
    durable frame persisted before that change (or a hand-built test) may still
    carry a bare string — both the live tool and a resume read labels through
    here so an old paused turn still settles. The label is the answer value
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
) -> list[dict[str, Any]]:
    """Cap choice options, accepting either bare strings or rich objects.

    Default cap is 6. A bare ``"Postgres"`` becomes ``{"label": "Postgres"}``;
    an object may add ``action`` (a desktop client action such as
    ``open_local_project`` / ``register_local_project`` / ``bind_local_folder``
    — unknown values drop so a hallucinated action never reaches the wire).
    ``detail`` is dropped even if the model filled it; put the trade-off in
    ``label``. Empty-label entries drop. Names may carry
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
            action = str(it.get("action") or "").strip()
            if action in _ALLOWED_OPTION_ACTIONS:
                opt["action"] = action
        out.append(opt)
        if len(out) >= cap:
            break
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
    return [raw]


def normalize_questions(
    raw: Any,
    *,
    max_options: int = _MAX_OPTIONS,
) -> list[dict[str, Any]]:
    """Cap (≤5) + id the questions, normalizing kind/options/multiple.

    Tendency lives in option labels (``（推荐）``). A leftover model ``default`` is
    dropped, not copied onto the card. ``max_options`` forwards to
    :func:`normalize_options`. Leftover ``kind`` is ignored: options (including a
    flattened question-level ``label``) → choice; none → text. Never a
    zero-button choice. ``question`` is accepted as an alias for ``prompt``.
    """
    items = coerce_list_arg(raw, field="questions")
    out: list[dict[str, Any]] = []
    for i, it in enumerate(items[:_MAX_QUESTIONS]):
        if not isinstance(it, dict):
            continue
        prompt = _question_prompt(it)
        if not prompt:
            continue
        raw_options = it.get("options")
        if _options_absent(raw_options):
            absorbed = _flattened_option_raw(it)
            if absorbed:
                raw_options = absorbed
        options = normalize_options(raw_options, max_options=max_options)
        if options:
            kind = "choice"
            multiple = bool(it.get("multiple") or False)
            # Models sometimes put a desktop action on the question. Promote onto
            # the first option that does not already have one — never every
            # option (a sibling "skip / 口头汇报" must not inherit register/open/bind).
            q_action = str(it.get("action") or "").strip()
            if q_action in _ALLOWED_OPTION_ACTIONS:
                for opt in options:
                    if "action" not in opt:
                        opt["action"] = q_action
                        break
        else:
            kind = "text"
            options = []
            multiple = False
        out.append(
            {
                "id": f"q{i}",
                "prompt": prompt,
                "kind": kind,
                "options": options,
                "multiple": multiple,
            }
        )
    return out


def card_stem(questions: list[dict[str, Any]]) -> str:
    """Wire ``question`` headline: the first question prompt."""
    if not questions:
        return ""
    return str(questions[0].get("prompt") or "").strip()
