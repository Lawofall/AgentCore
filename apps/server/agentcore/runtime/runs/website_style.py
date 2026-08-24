"""Website DESIGN.md helpers + optional style confirmation cache.

场面账硬闸已拆除：站点流水线不再因缺视觉风格账拒调
（``playbook_args.style`` 气质槽另计，与本账无关）。
无确认时 :func:`design_prompt_block` 软注入 ``s_default`` 与短正向 DESIGN 配方
（``domain=tool`` 为工具台配方，否则营销向默认配方）；
``web_quality_scan`` 仍要求 DESIGN.md 含「用户选定风格 id」标记。

Ledger helpers (``record_*`` / pause rehydrate) retained for tests and durable
cache — production resume no longer records style picks from ask wire.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Any, ClassVar

from agentcore.runtime.facts import Fact, FactKind, current_fact_log, record_turn_fact

# Workspace design contract path (playbook artifact).
DESIGN_MD_PATH = "site/DESIGN.md"

# Markdown heading / line the design worker must write; scanner looks for this.
STYLE_ID_HEADING = "用户选定风格 id"

# full_auto narrow default when CEO skips the style card.
DEFAULT_STYLE_ID = "s_default"
DEFAULT_STYLE_LABEL = "简洁克制·高对比"

# ``s0`` / ``s_default`` / ``s12`` — DESIGN.md style ids (soft default or user-written).
_STYLE_ID_TOKEN_RE = re.compile(r"\b(s(?:_default|\d+))\b", re.IGNORECASE)

_HEX_COLOR_RE = re.compile(r"#(?:[0-9A-Fa-f]{3,4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})\b")

# ``var(--token, #fallback)`` — fallback hex is not "scattered brand color".
_VAR_FALLBACK_PREFIX = re.compile(
    r"var\s*\(\s*--[\w-]+\s*,\s*$",
    re.IGNORECASE,
)

# Neutrals / CSS keywords allowed without appearing in DESIGN tokens.
_NEUTRAL_COLORS = frozenset(
    {
        "#000",
        "#000000",
        "#fff",
        "#ffffff",
        "#fff0",
        "#ffffff00",
        "#0000",
        "#00000000",
    }
)

_lock = threading.Lock()


@dataclass(frozen=True)
class StyleConfirmation:
    style_id: str
    label: str
    source: str  # "ask_user" | "full_auto_default"


# conversation_id → StyleConfirmation (hot cache; durable source = journal / paused)
_LEDGER: dict[str, StyleConfirmation] = {}


@dataclass(frozen=True, slots=True)
class WebsiteStyleConfirmedFact:
    """Durable structured style pick for site-style gate rehydration."""

    style_id: str
    label: str
    source: str
    conversation_id: str = ""
    kind: ClassVar[FactKind] = FactKind.WEBSITE_STYLE_CONFIRMED

    def to_fact(self, ts: str | None = None) -> Fact:
        return Fact(
            kind=self.kind.value,
            payload={
                "style_id": self.style_id,
                "label": self.label,
                "source": self.source,
                "conversation_id": self.conversation_id,
            },
            ts=ts,
        )


def build_website_missing_style_error() -> str:
    return (
        "站点风格无确认时由机制软注入 s_default"
        "（写入 site/DESIGN.md「用户选定风格 id」）。"
        "AutonomyPolicy.full_auto 同样落默认风格，无需开工卡。"
    )


def style_confirmation_to_payload(conf: StyleConfirmation) -> dict[str, str]:
    return {
        "style_id": conf.style_id,
        "label": conf.label,
        "source": conf.source,
    }


def style_confirmation_from_payload(payload: dict[str, Any] | None) -> StyleConfirmation | None:
    if not isinstance(payload, dict):
        return None
    sid = str(payload.get("style_id") or "").strip()
    if not sid:
        return None
    return StyleConfirmation(
        style_id=sid,
        label=str(payload.get("label") or "").strip() or sid,
        source=str(payload.get("source") or "").strip() or "ask_user",
    )


def style_from_journal_entries(
    entries: list[dict[str, Any]] | None,
) -> StyleConfirmation | None:
    """Fold the last ``website_style_confirmed`` fact from a journal stream."""
    if not entries:
        return None
    last: StyleConfirmation | None = None
    kind = FactKind.WEBSITE_STYLE_CONFIRMED.value
    for entry in entries:
        if (entry.get("kind") or "") != kind:
            continue
        conf = style_confirmation_from_payload(entry.get("payload"))
        if conf is not None:
            last = conf
    return last


def _cache_put(conversation_id: str, conf: StyleConfirmation) -> StyleConfirmation:
    with _lock:
        _LEDGER[conversation_id] = conf
    return conf


def _cache_get(conversation_id: str) -> StyleConfirmation | None:
    with _lock:
        return _LEDGER.get(conversation_id)


def hydrate_style_confirmation(
    conversation_id: str,
    conf: StyleConfirmation,
) -> StyleConfirmation:
    """Fill the hot cache only (no journal append) — used by rehydrate paths."""
    cid = (conversation_id or "").strip()
    if not cid:
        raise ValueError("conversation_id required")
    return _cache_put(cid, conf)


def rehydrate_style_confirmation(
    conversation_id: str | None,
    *,
    entries: list[dict[str, Any]] | None = None,
    turn_paused_style: dict[str, Any] | None = None,
) -> StyleConfirmation | None:
    """Restore memory from ``turn_paused.website_style`` and/or journal facts.

    Priority: turn_paused snapshot → last ``website_style_confirmed`` in ``entries``.
    Returns the hydrated confirmation, or ``None`` when neither source has one.
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    conf = style_confirmation_from_payload(turn_paused_style)
    if conf is None:
        conf = style_from_journal_entries(entries)
    if conf is None:
        return None
    return hydrate_style_confirmation(cid, conf)


def record_style_confirmation(
    conversation_id: str,
    *,
    style_id: str,
    label: str,
    source: str,
) -> StyleConfirmation:
    """Overwrite the conversation's confirmed style and append a durable journal fact."""
    cid = (conversation_id or "").strip()
    sid = (style_id or "").strip()
    if not cid or not sid:
        raise ValueError("conversation_id and style_id required")
    conf = StyleConfirmation(
        style_id=sid,
        label=(label or "").strip() or sid,
        source=source,
    )
    _cache_put(cid, conf)
    record_turn_fact(
        WebsiteStyleConfirmedFact(
            style_id=conf.style_id,
            label=conf.label,
            source=conf.source,
            conversation_id=cid,
        ).to_fact()
    )
    return conf


def get_style_confirmation(conversation_id: str | None) -> StyleConfirmation | None:
    """Hot cache, then ambient fact log (same-turn durable rehydrate without DB)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    hit = _cache_get(cid)
    if hit is not None:
        return hit
    log = current_fact_log.get()
    if log is None:
        return None
    conf = style_from_journal_entries(log.entries())
    if conf is None:
        return None
    return hydrate_style_confirmation(cid, conf)


def clear_style_confirmation(conversation_id: str | None) -> None:
    """Test helper — drop a conversation's hot-cache entry (journal untouched)."""
    cid = (conversation_id or "").strip()
    if not cid:
        return
    with _lock:
        _LEDGER.pop(cid, None)


def ensure_full_auto_default_style(conversation_id: str) -> StyleConfirmation:
    """Narrow full_auto exemption: ledger the default if none confirmed yet."""
    existing = get_style_confirmation(conversation_id)
    if existing is not None:
        return existing
    return record_style_confirmation(
        conversation_id,
        style_id=DEFAULT_STYLE_ID,
        label=DEFAULT_STYLE_LABEL,
        source="full_auto_default",
    )


async def load_style_confirmation_from_db(
    conversation_id: str | None,
) -> StyleConfirmation | None:
    """Cold path: scan recent turn journals for ``website_style_confirmed``.

    Used when the hot cache and ambient fact log miss (process restart / new turn).
    Hydrates memory on hit. Best-effort — DB gaps return ``None`` (gate fails cleanly).
    """
    cid = (conversation_id or "").strip()
    if not cid:
        return None
    hit = _cache_get(cid)
    if hit is not None:
        return hit
    try:
        from agentcore.db.base import async_session_factory
        from agentcore.db.repositories import TurnJournalRepository
    except Exception:
        return None
    try:
        async with async_session_factory() as session:
            repo = TurnJournalRepository(session)
            payload = await repo.find_latest_website_style(conversation_id=cid)
    except Exception:
        return None
    conf = style_confirmation_from_payload(payload)
    if conf is None:
        return None
    return hydrate_style_confirmation(cid, conf)


async def resolve_style_confirmation(
    conversation_id: str | None,
) -> StyleConfirmation | None:
    """Gate helper: memory / ambient journal → else durable DB scan."""
    conf = get_style_confirmation(conversation_id)
    if conf is not None:
        return conf
    return await load_style_confirmation_from_db(conversation_id)


def snapshot_website_style_for_pause(
    journal_entries: list[dict[str, Any]] | None,
    *,
    conversation_id: str | None = None,
) -> dict[str, str] | None:
    """Build ``turn_paused.website_style`` from journal fact or hot cache."""
    conf = style_from_journal_entries(journal_entries)
    if conf is None and conversation_id:
        conf = _cache_get((conversation_id or "").strip())
    if conf is None:
        return None
    return style_confirmation_to_payload(conf)


def extract_style_id_from_design(text: str) -> str | None:
    """Parse ``用户选定风格 id`` from DESIGN.md body."""
    if not text:
        return None
    m = re.search(
        rf"{re.escape(STYLE_ID_HEADING)}\s*[:：]?\s*(\S+)?",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None
    same = (m.group(1) or "").strip()
    if same and _STYLE_ID_TOKEN_RE.fullmatch(same):
        return same
    after = text[m.end() :]
    for line in after.splitlines():
        cand = line.strip()
        if not cand:
            continue
        tok = _STYLE_ID_TOKEN_RE.search(cand)
        if tok:
            return tok.group(1)
        break
    return None


def extract_design_tokens(text: str) -> set[str]:
    """Hex colors declared in DESIGN.md (normalized lowercase)."""
    if not text:
        return set()
    return {h.casefold() for h in _HEX_COLOR_RE.findall(text)}


def _hex_is_css_var_fallback(text: str, start: int) -> bool:
    """True when ``text[start:]`` hex is the fallback arm of ``var(--token, #…)``."""
    window = text[max(0, start - 96) : start]
    return bool(_VAR_FALLBACK_PREFIX.search(window))


def find_scattered_colors(text: str, allowed: set[str]) -> list[str]:
    """Hex colors in implementation text not ⊆ DESIGN tokens (excl. neutrals).

    Ignores hex that appear only as ``var(--token, #fallback)`` defaults (catalog
    ``_shared.css`` bridge) — those are not brand scatter.
    """
    if not text:
        return []
    allowed_cf = {a.casefold() for a in allowed}
    neutrals = {n.casefold() for n in _NEUTRAL_COLORS}
    hits: list[str] = []
    seen: set[str] = set()
    for m in _HEX_COLOR_RE.finditer(text):
        if _hex_is_css_var_fallback(text, m.start()):
            continue
        raw = m.group(0)
        key = raw.casefold()
        if key in seen or key in neutrals or key in allowed_cf:
            continue
        if len(key) == 4:  # #rgb
            expanded = "#" + "".join(ch * 2 for ch in key[1:])
            if expanded in allowed_cf or expanded in neutrals:
                continue
        seen.add(key)
        hits.append(raw)
        if len(hits) >= 8:
            break
    return hits


# 默认 / s_default 路径软注入：抬品味，不扩硬闸 / anti-slop 指纹。
_DEFAULT_DESIGN_RECIPE = (
    "【正向配方·默认】单一视觉焦点；大面用中性色、主色极少；"
    "禁止装饰性渐变 / glow / 粒子；动效仅用于交互反馈，勿作氛围装饰。"
)
_TOOL_DESIGN_RECIPE = (
    "【正向配方·工具台】中性 chrome、accent 极少；"
    "禁止默认 Tailwind 蓝 #2563eb / blue-600 当主色；"
    "用密度 / 侧栏 · 表 token；勿套营销 hero。"
)


def design_prompt_block(
    *, style: StyleConfirmation | None, domain: str = "marketing"
) -> str:
    """Inject into the design-node task book.

    No style ledger: soft-inject ``s_default``. With a confirmation (legacy / tests),
    write that id. ``web_quality_scan`` still requires the DESIGN.md marker.

    Default / ``s_default`` also soft-injects a short positive DESIGN recipe
    (taste lift on the no-pick path). ``domain="tool"`` uses the toolshed recipe;
    ``marketing`` (default) keeps the landing-page recipe. Non-default confirmed
    styles skip the recipe.
    """
    positive_recipe = (
        _TOOL_DESIGN_RECIPE if domain == "tool" else _DEFAULT_DESIGN_RECIPE
    )
    if style is None:
        sid = DEFAULT_STYLE_ID
        label = DEFAULT_STYLE_LABEL
        style_line = (
            f"未指定风格时使用默认 id=`{sid}`（{label}）——必须原样写入。"
        )
        recipe = positive_recipe
    else:
        sid = style.style_id
        label = style.label
        style_line = f"用户选定风格 id=`{sid}`（{label}，来源 {style.source}）——必须原样写入。"
        # 默认路径抬品味优先：仅 s_default（含 full_auto 落默认）塞完整配方。
        recipe = positive_recipe if sid == DEFAULT_STYLE_ID else ""
    return (
        f"【设计契约】用 file_write 落盘 `{DESIGN_MD_PATH}`，须含："
        f"色板 tokens（CSS 变量名 + hex）、字体、间距、对比度策略、禁止项、"
        f"以及章节「{STYLE_ID_HEADING}」下一行写 `{sid}`。"
        f"{style_line}"
        f"{recipe}"
        "骨架与分区实现只读本文件，禁止另起散色。"
    )
