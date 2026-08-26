"""真·多模型辩手（Phase 3）——目录身份 / 路由键 / 默认对阵 / 裁判选型。

权威定案：docs/03-AI核心/辩论编排设计.md §7.5。

- 产品身份 = 目录 ``ref``（``@platform/{id}`` / ``@byok/{provider_id}/{id}``）或口语提及
- 解析后内部仍是 model + origin + provider_id；出站路由键 = platform/{id} 或 {provider_id}/{id}
- 空身份 → 回退 turn 主模型；非空校验失败 → 硬失败（禁 silent）
- 裁判不要求中立、可与辩手同模；用户点名优先
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from agentcore.llm.model_ref import format_model_ref, parse_model_input
from agentcore.llm.profiles import PLATFORM_PROVIDER_SENTINEL
from agentcore.runtime.debate.types import DebateSide

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentcore.llm.catalog import ModelCatalog, ModelCatalogEntry
    from agentcore.llm.provider.protocol import LLMProvider
    from agentcore.runtime.debate.types import DebateConfig

ModelOrigin = Literal["platform", "byok"]


@dataclass(frozen=True)
class ModelIdentity:
    """解析后的目录行槽位（空 model = 未指定）。产品面用 :meth:`ref`。"""

    model: str = ""
    origin: str = ""  # platform | byok | ""
    provider_id: str = ""

    def is_empty(self) -> bool:
        return not (self.model or "").strip()

    def normalized(self) -> ModelIdentity:
        mid = (self.model or "").strip()
        if not mid:
            return ModelIdentity()
        origin = (self.origin or "").strip().lower()
        provider_id = (self.provider_id or "").strip()
        if origin == "platform":
            provider_id = ""
        return ModelIdentity(model=mid, origin=origin, provider_id=provider_id)

    def route_key(self) -> str:
        """编成 ProviderRouter 路由键；空身份返回空串。"""
        ident = self.normalized()
        if ident.is_empty():
            return ""
        if ident.origin == "platform":
            return f"{PLATFORM_PROVIDER_SENTINEL}/{ident.model}"
        if ident.origin == "byok" and ident.provider_id:
            return f"{ident.provider_id}/{ident.model}"
        # 形状未齐——调用方应先 shape_error；此处不瞎猜裸串消歧。
        return ""

    def router_prefix(self) -> str:
        """ProviderRouter extras 注册键（platform 哨兵或 BYOK provider_id）。"""
        ident = self.normalized()
        if ident.is_empty():
            return ""
        if ident.origin == "platform":
            return PLATFORM_PROVIDER_SENTINEL
        return ident.provider_id if ident.origin == "byok" else ""

    def matches(self, other: ModelIdentity) -> bool:
        """参赛身份相等：优先比路由键；仅有裸 model 时比 id。"""
        a, b = self.normalized(), other.normalized()
        if a.is_empty() or b.is_empty():
            return False
        ka, kb = a.route_key(), b.route_key()
        if ka and kb:
            return ka == kb
        return a.model == b.model

    def ref(self) -> str:
        """Product catalog identity; empty if the triple is not yet complete."""
        ident = self.normalized()
        if ident.is_empty():
            return ""
        return format_model_ref(ident.origin, ident.model, ident.provider_id or None)


def coerce_identity(ident: ModelIdentity) -> tuple[ModelIdentity, str]:
    """Expand an unparsed ``@`` handle sitting in ``model``; leftover triples pass through."""
    ident = ident.normalized()
    if ident.is_empty():
        return ident, ""
    parsed = parse_model_input(ident.model)
    if parsed.kind == "bad_ref":
        return ident, parsed.error
    if parsed.kind == "ref":
        return (
            ModelIdentity(
                model=parsed.model,
                origin=parsed.origin,
                provider_id=parsed.provider_id,
            ).normalized(),
            "",
        )
    return ident, ""


def identity_from_side(side: DebateSide) -> ModelIdentity:
    return ModelIdentity(
        model=getattr(side, "model", "") or "",
        origin=getattr(side, "origin", "") or "",
        provider_id=getattr(side, "provider_id", "") or "",
    ).normalized()


def identity_shape_error(ident: ModelIdentity, *, where: str = "model") -> str:
    """Complete identity ready to route. Empty is ok. Mentions must be resolved first.

    Unparsed ``@`` handles in ``model`` are expanded. Upstream ids may contain ``/``.
    """
    ident, coerce_err = coerce_identity(ident)
    if coerce_err:
        return f"{where} {coerce_err}"
    ident = ident.normalized()
    if ident.is_empty():
        return ""
    if ident.origin not in ("platform", "byok"):
        return ""
    if ident.origin == "byok" and not ident.provider_id:
        return (
            f"{where} 须抄写完整目录身份 @byok/{{provider_id}}/{{model}}，"
            "或只填可消歧提及。"
        )
    if not ident.route_key():
        return f"{where} 无法编成路由键。"
    return ""


def format_candidate_line(c: dict[str, Any], *, with_label: bool = True) -> str:
    """候选 / 错误 tip 同形：展示名 + 可抄写 ``ref``，禁止教 ``platform/{id}`` 路由键。"""
    parts: list[str] = []
    if with_label:
        label = (c.get("label") or "").strip()
        if label:
            parts.append(label)
    ref = (c.get("ref") or "").strip()
    if not ref:
        ident = ModelIdentity(
            model=str(c.get("model") or ""),
            origin=str(c.get("origin") or ""),
            provider_id=str(c.get("provider_id") or ""),
        ).normalized()
        ref = ident.ref()
    if ref:
        parts.append(ref)
    return " · ".join(parts) if parts else "(empty)"


def format_identity_fields(ident: ModelIdentity) -> str:
    """校验失败文案用：优先 ``ref``，未齐时回落裸 model。"""
    ident, _ = coerce_identity(ident)
    ident = ident.normalized()
    ref = ident.ref()
    if ref:
        return ref
    return ident.model or "(empty)"


def infer_utterance_origin_preference(*texts: str) -> ModelOrigin | None:
    """从本轮 user_message（主）及 motion（辅）解析 platform|byok 偏好。

    先命中先返回；无信号 → None。这是消歧上下文，不是非法身份的 silent 回退。
    """
    for text in texts:
        raw = (text or "").strip()
        if not raw:
            continue
        lower = raw.lower()
        if any(
            k in lower
            for k in ("byok", "自备密钥", "自己的密钥", "我的密钥", "自备 key")
        ):
            return "byok"
        if "平台的" in raw or "用平台" in raw or "平台模型" in raw:
            return "platform"
        if "platform" in lower and "platform/" not in lower:
            return "platform"
        # 口语「平台 xxx」但避免把路由键残片当偏好
        if "平台" in raw and "平台/" not in raw:
            return "platform"
    return None


def side_route_model(side: DebateSide, *, turn_model: str = "") -> str:
    """注入用模型：side 非空 → 路由键；空 → turn 主模型（可空）。"""
    ident = identity_from_side(side)
    if not ident.is_empty():
        key = ident.route_key()
        if key:
            return key
        # 形状残缺时不 silent 回退——由上游校验拦截；此处返回空让调用方可见。
        return ""
    return (turn_model or "").strip()


def priced_model_from_route(route_or_model: str) -> str:
    """计费用裸 model：剥 platform/ 或 BYOK provider_id/ 前缀；保留 doubao/ 等厂商键。"""
    raw = (route_or_model or "").strip()
    if "/" not in raw:
        return raw
    prefix, _, rest = raw.partition("/")
    if not rest:
        return raw
    if prefix == PLATFORM_PROVIDER_SENTINEL:
        return rest
    from agentcore.llm.pricing import _VENDOR_PREFIXES

    if prefix in _VENDOR_PREFIXES:
        return raw
    return rest


def identity_from_route_key(route_or_model: str) -> ModelIdentity:
    """``RunSpec.model`` 路由键 → 可展示目录行槽位（空=未指定）。

    ``platform/{id}`` / ``{provider_id}/{id}`` 还原 origin；厂商前缀裸串
    （如 ``doubao/…``）与无前缀裸 id 仅透出 ``model``（无 origin）。
    """
    raw = (route_or_model or "").strip()
    if not raw:
        return ModelIdentity()
    if "/" not in raw:
        return ModelIdentity(model=raw)
    prefix, _, rest = raw.partition("/")
    if not rest:
        return ModelIdentity(model=raw)
    if prefix == PLATFORM_PROVIDER_SENTINEL:
        return ModelIdentity(model=rest, origin="platform").normalized()
    from agentcore.llm.pricing import _VENDOR_PREFIXES

    if prefix in _VENDOR_PREFIXES:
        return ModelIdentity(model=raw)
    return ModelIdentity(model=rest, origin="byok", provider_id=prefix).normalized()


def _is_deepseek_family(model_id: str) -> bool:
    return "deepseek" in (model_id or "").lower()


def _entry_identity(entry: ModelCatalogEntry) -> ModelIdentity:
    return ModelIdentity(
        model=entry.id,
        origin=entry.origin,
        provider_id=entry.provider_id or "",
    ).normalized()


def _available_entries(catalog: ModelCatalog) -> list[ModelCatalogEntry]:
    return [e for e in catalog.models if e.available]


def candidate_from_entry(
    entry: ModelCatalogEntry, *, side_key: str = ""
) -> dict[str, Any]:
    """开赛卡 / 错误载荷候选行（ref 为可抄写身份；origin/provider_id 仍作行属性）。"""
    ident = _entry_identity(entry)
    row: dict[str, Any] = {
        "ref": ident.ref() or entry.ref,
        "model": entry.id,
        "origin": entry.origin,
        "provider_id": entry.provider_id or "",
        "label": (entry.display_name or entry.id).strip() or entry.id,
    }
    if side_key:
        row["side_key"] = side_key
    return row


def needs_mention_resolve(ident: ModelIdentity) -> bool:
    """model 非空但缺 origin（或 byok 缺 provider_id）→ 待消歧提及。"""
    ident = ident.normalized()
    if ident.is_empty():
        return False
    if ident.origin not in ("platform", "byok"):
        return True
    return ident.origin == "byok" and not ident.provider_id


def apply_identity_to_side(side: DebateSide, ident: ModelIdentity) -> DebateSide:
    ident = ident.normalized()
    return DebateSide(
        key=side.key,
        name=side.name,
        stance=side.stance,
        is_subject=bool(side.is_subject),
        model=ident.model,
        origin=ident.origin,
        provider_id=ident.provider_id,
        run_id=getattr(side, "run_id", "") or "",
    )


@dataclass(frozen=True)
class MentionResolveResult:
    """``resolve_model_mention`` 结果：唯一命中写 identity；否则带 candidates。"""

    ok: bool
    identity: ModelIdentity = field(default_factory=ModelIdentity)
    candidates: tuple[dict[str, Any], ...] = ()
    error: str = ""


def _strip_platform_prefix(mention: str) -> tuple[str, ModelOrigin | None]:
    """去「平台」前缀；命中则 prefer_origin=platform。

    ``platform/…`` / ``平台/…`` 是路由键残片，不当口语前缀。
    """
    raw = (mention or "").strip()
    if not raw:
        return "", None
    lower = raw.lower()
    for prefix in ("平台", "platform"):
        matched = raw.startswith(prefix) or (
            prefix.isascii() and lower.startswith(prefix.lower())
        )
        if not matched:
            continue
        rest = raw[len(prefix) :]
        if rest.startswith("/"):
            continue
        rest = rest.strip(" \t　:：-_")
        return rest, "platform"
    return raw, None


def resolve_model_mention(
    mention: str,
    catalog: ModelCatalog,
    prefer_origin: ModelOrigin | None = None,
    *,
    utterance_prefer: ModelOrigin | None = None,
    side_key: str = "",
    where: str = "",
) -> MentionResolveResult:
    """口语提及 → 目录唯一身份。

    去「平台」前缀、大小写不敏感匹配 id / display_name；DeepSeek 系走
    :func:`_is_deepseek_family`。唯一命中 → ok；0 / 多命中 → 结构化失败带 candidates。

    偏好合并优先级：``prefer_origin``（side 已填 origin）> mention「平台」前缀 >
    ``utterance_prefer``（用户原文 / motion）。同 id 多 origin 时有偏好则池过滤后唯一选定。
    """
    mention_text = (mention or "").strip()
    parsed = parse_model_input(mention_text)
    if parsed.kind == "bad_ref":
        where_l = where or (f"sides[`{side_key}`]" if side_key else "model")
        return MentionResolveResult(ok=False, error=f"{where_l} {parsed.error}")
    if parsed.kind == "ref":
        ident = ModelIdentity(
            model=parsed.model,
            origin=parsed.origin,
            provider_id=parsed.provider_id,
        ).normalized()
        return MentionResolveResult(ok=True, identity=ident)

    text, inferred = _strip_platform_prefix(mention_text)
    # side.origin > mention 前缀 > utterance
    prefer: ModelOrigin | None = prefer_origin or inferred or utterance_prefer
    if not text:
        return MentionResolveResult(
            ok=False,
            error="模型提及为空，无法消歧。",
            candidates=tuple(
                candidate_from_entry(e, side_key=side_key)
                for e in _available_entries(catalog)
            ),
        )

    available = _available_entries(catalog)
    pool = (
        [e for e in available if e.origin == prefer] if prefer else list(available)
    )
    text_l = text.lower()
    hits: list[ModelCatalogEntry] = []
    seen: set[tuple[str, str, str]] = set()

    def _add(entry: ModelCatalogEntry) -> None:
        key = (entry.id, entry.origin, entry.provider_id or "")
        if key in seen:
            return
        seen.add(key)
        hits.append(entry)

    for e in pool:
        if e.id.lower() == text_l:
            _add(e)
            continue
        if (e.display_name or "").strip().lower() == text_l:
            _add(e)
            continue
        if _is_deepseek_family(text) and "/" not in text and (
            _is_deepseek_family(e.id) or _is_deepseek_family(e.display_name or "")
        ):
            _add(e)

    if len(hits) == 1:
        return MentionResolveResult(ok=True, identity=_entry_identity(hits[0]))

    label = where or (f"sides[`{side_key}`]" if side_key else "model")
    cand_src = hits if hits else (pool if pool else available)
    candidates = tuple(
        candidate_from_entry(e, side_key=side_key) for e in cand_src
    )
    if not hits:
        tip = (
            f"{label} 提及「{mention}」在可用目录中零匹配"
            + (f"（prefer={prefer}）" if prefer else "")
            + "。请从下列候选把 `model` 抄成 @platform/… 或 @byok/{provider_id}/…；"
            "禁止再 ask_user 元问题。"
        )
    else:
        tip = (
            f"{label} 提及「{mention}」匹配到 {len(hits)} 个目录条目，无法唯一消歧。"
            "请从下列候选选定一条，把 `model` 抄成 @platform/… 或 @byok/{provider_id}/…；"
            "禁止再 ask_user「是不是当前主模型」类元问题。"
        )
    lines = [f"  - {format_candidate_line(c)}" for c in candidates]
    body = tip + ("\n候选：\n" + "\n".join(lines) if lines else "（目录无可选项）")
    return MentionResolveResult(ok=False, error=body, candidates=candidates)


def resolve_default_matchup(
    catalog: ModelCatalog,
) -> tuple[ModelIdentity, ModelIdentity] | None:
    """默认对阵：PLATFORM_MODELS[0] vs [1]；否则 1 平台 + BYOK DeepSeek；凑不齐 → None.

    ``prepare_debate_model_plan(cross_model=True)`` 在各方 model 皆空时真调本函数写回双方。
    """
    from agentcore.billing.preference import platform_model_allowlist

    available = _available_entries(catalog)
    platform_rows = [e for e in available if e.origin == "platform"]
    allowlist = platform_model_allowlist()
    # ① 平台 allowlist ≥2 → 按 PLATFORM_MODELS 顺序取前两名（非统一目录 models[] 前两项）。
    if len(allowlist) >= 2:
        by_id = {e.id: e for e in platform_rows}
        a, b = allowlist[0], allowlist[1]
        if a in by_id and b in by_id:
            return _entry_identity(by_id[a]), _entry_identity(by_id[b])
    if len(platform_rows) >= 2 and not allowlist:
        # 无显式 allowlist 时退化为平台目录前两行（稳定顺序来自 _platform_model_ids）。
        return _entry_identity(platform_rows[0]), _entry_identity(platform_rows[1])

    # ② 仅 1 个平台模型 + 已配 BYOK DeepSeek
    if len(platform_rows) >= 1:
        plat = platform_rows[0]
        for e in available:
            if e.origin == "byok" and _is_deepseek_family(e.id):
                return _entry_identity(plat), _entry_identity(e)
    return None


@dataclass(frozen=True)
class ModeratorResolution:
    identity: ModelIdentity
    same_model_debate: bool = False


def resolve_moderator_identity(
    *,
    catalog: ModelCatalog,
    debater_identities: Sequence[ModelIdentity],
    turn_main: ModelIdentity,
) -> ModeratorResolution:
    """裁判默认槽（未点名）：DeepSeek 系 → PLATFORM_MODELS 首个可用 → turn 主模型。

    **不要求中立、可与辩手同模**——不再因辩手已占用而跳过。``debater_identities``
    保留入参兼容调用方，不参与过滤。目录只剩一模型时 ``same_model_debate=True``。
    用户点名裁判走 :func:`prepare_debate_model_plan` 消歧，不经本函数。
    """
    from agentcore.billing.preference import platform_model_allowlist

    _ = debater_identities  # 兼容保留；不再「避开辩手」
    available = _available_entries(catalog)
    only_one = len(available) <= 1

    # ① DeepSeek 系（即使辩手已用）
    for e in available:
        if _is_deepseek_family(e.id):
            return ModeratorResolution(
                identity=_entry_identity(e),
                same_model_debate=only_one,
            )

    # ② PLATFORM_MODELS 第一可用
    allowlist = platform_model_allowlist()
    platform_by_id = {e.id: e for e in available if e.origin == "platform"}
    for mid in allowlist:
        entry = platform_by_id.get(mid)
        if entry is not None:
            return ModeratorResolution(
                identity=_entry_identity(entry),
                same_model_debate=only_one,
            )

    # 无 allowlist 命中时：平台目录第一可用
    for e in available:
        if e.origin == "platform":
            return ModeratorResolution(
                identity=_entry_identity(e),
                same_model_debate=only_one,
            )

    # ③ turn 主模型
    if not turn_main.is_empty():
        return ModeratorResolution(
            identity=turn_main.normalized(),
            same_model_debate=only_one if available else True,
        )

    # ④ 目录仅剩一项 / 空目录降级
    if available:
        return ModeratorResolution(
            identity=_entry_identity(available[0]),
            same_model_debate=only_one,
        )
    return ModeratorResolution(identity=ModelIdentity(), same_model_debate=True)


async def validate_identity_in_catalog(
    session: AsyncSession | None,
    user_id: str,
    ident: ModelIdentity,
    *,
    where: str = "model",
    catalog: ModelCatalog | None = None,
) -> str:
    """非空身份须过目录校验；失败返回错误文案，成功返回空串。"""
    from agentcore.llm.catalog import resolve_model_catalog, validate_model_choice

    ident = ident.normalized()
    shape = identity_shape_error(ident, where=where)
    if shape:
        return shape
    if ident.is_empty():
        return ""
    if catalog is None:
        if session is None:
            return f"{where} 无法校验目录（缺少 session）。"
        catalog = await resolve_model_catalog(session, user_id)

    want_provider = ident.provider_id if ident.origin == "byok" else None
    exact = [
        e
        for e in catalog.models
        if e.id == ident.model
        and e.origin == ident.origin
        and e.provider_id == want_provider
    ]
    if exact and any(e.available for e in exact):
        return ""
    if exact:
        # 目录命中但不可用 / 无凭据
        return (
            f"{where} 目录命中但不可用或无凭据：{format_identity_fields(ident)}"
            "。请改选可用模型，禁止 silent 回退。"
        )
    if session is not None:
        ok = await validate_model_choice(
            session,
            user_id,
            ident.model,
            ident.origin,  # type: ignore[arg-type]
            ident.provider_id or None,
        )
        if ok:
            return ""
    return (
        f"{where} 目录未命中：{format_identity_fields(ident)}"
        "。请改选目录内模型，禁止 silent 回退。"
    )


async def prepare_debate_model_plan(
    config: DebateConfig,
    *,
    user_id: str,
    turn_model: str,
    turn_origin: str = "",
    turn_provider_id: str = "",
    session: AsyncSession | None = None,
    catalog: ModelCatalog | None = None,
    cross_model: bool = False,
    user_message: str = "",
) -> str:
    """开赛前：提及消歧 → 校验目录身份 → 解析裁判（点名优先），写回 config。失败返回错误文案。

    - 口语提及（model 非空缺 origin）→ :func:`resolve_model_mention` 写回正式身份
    - ``@platform/…`` / ``@byok/…`` 句柄 → 直接展开
    - ``cross_model=True`` 且各方空 model → 真调 :func:`resolve_default_matchup`
    - 空且无旗标 = 同模型场（回退 turn main）
    - 消歧 0/多候选 → 硬失败，``config.model_candidates`` 挂目录候选
    - 裁判：``moderator_model`` 提及/句柄非空 → 消歧或校验写回；空 → 系统默认（可同模）
    - ``user_message`` + motion → :func:`infer_utterance_origin_preference` 作消歧偏好
    """
    from agentcore.llm.catalog import resolve_model_catalog

    config.model_candidates = []

    if catalog is None and session is not None and user_id:
        catalog = await resolve_model_catalog(session, user_id)

    turn_main = ModelIdentity(
        model=turn_model, origin=turn_origin, provider_id=turn_provider_id
    ).normalized()
    utterance_prefer = infer_utterance_origin_preference(
        user_message, getattr(config, "motion", "") or ""
    )

    # C · 默认对阵：旗标 + 各方空 model
    all_empty = all(identity_from_side(s).is_empty() for s in config.sides)
    if cross_model and all_empty:
        if catalog is None:
            return (
                "cross_model=true 但无法加载模型目录，凑不出默认对阵。"
                "请点名双方模型，或稍后重试。"
            )
        matchup = resolve_default_matchup(catalog)
        if matchup is None:
            config.model_candidates = [
                candidate_from_entry(e) for e in _available_entries(catalog)
            ]
            return (
                "cross_model=true 但目录凑不出默认跨模型对阵"
                "（需平台 allowlist≥2，或 1 平台 + BYOK DeepSeek）。"
                "请去配模型 / 加 BYOK，或点名双方模型；禁止 ask_user 元问题。"
            )
        a, b = matchup
        if len(config.sides) >= 2:
            config.sides[0] = apply_identity_to_side(config.sides[0], a)
            config.sides[1] = apply_identity_to_side(config.sides[1], b)

    # B · 提及消歧（写回正式身份）；``@`` 句柄先展开
    for i, side in enumerate(config.sides):
        ident = identity_from_side(side)
        where = f"sides[`{side.key}`]"
        ident, coerce_err = coerce_identity(ident)
        if coerce_err:
            return f"{where} {coerce_err}"
        if ident.is_empty():
            continue
        if ident.model != (getattr(side, "model", "") or "") or ident.origin != (
            getattr(side, "origin", "") or ""
        ):
            config.sides[i] = apply_identity_to_side(side, ident)
            side = config.sides[i]
        if not needs_mention_resolve(ident):
            continue
        if catalog is None:
            return (
                f"{where} 已填模型提及「{ident.model}」但无法加载目录消歧；"
                "请稍后重试，禁止 silent 回退 / ask_user 元问题。"
            )
        prefer: ModelOrigin | None = None
        if ident.origin in ("platform", "byok"):
            prefer = ident.origin  # type: ignore[assignment]
        result = resolve_model_mention(
            ident.model,
            catalog,
            prefer,
            utterance_prefer=utterance_prefer,
            side_key=side.key,
        )
        if not result.ok:
            config.model_candidates = list(result.candidates)
            return result.error
        config.sides[i] = apply_identity_to_side(side, result.identity)

    # 校验各方（完整目录身份；空=同模型场）
    for side in config.sides:
        ident = identity_from_side(side)
        where = f"sides[`{side.key}`]"
        shape = identity_shape_error(ident, where=where)
        if shape:
            return shape
        if ident.is_empty():
            continue
        if catalog is None and session is None:
            return (
                f"{where} 已填模型身份但无法校验目录；"
                "请稍后重试，禁止 silent 回退。"
            )
        err = await validate_identity_in_catalog(
            session,
            user_id,
            ident,
            where=where,
            catalog=catalog,
        )
        if err:
            return err

    # 裁判：用户点名优先（提及消歧 / 完整身份校验）；未点名 → 系统默认（可与辩手同模）
    mod_named = ModelIdentity(
        model=config.moderator_model or "",
        origin=config.moderator_origin or "",
        provider_id=config.moderator_provider_id or "",
    ).normalized()
    mod_named, mod_coerce_err = coerce_identity(mod_named)
    if mod_coerce_err:
        return f"moderator_model {mod_coerce_err}"

    if not mod_named.is_empty():
        if needs_mention_resolve(mod_named):
            if catalog is None:
                return (
                    f"moderator_model 已填模型提及「{mod_named.model}」但无法加载目录消歧；"
                    "请稍后重试，禁止 silent 回退 / ask_user 元问题。"
                )
            prefer_mod: ModelOrigin | None = None
            if mod_named.origin in ("platform", "byok"):
                prefer_mod = mod_named.origin  # type: ignore[assignment]
            mod_result = resolve_model_mention(
                mod_named.model,
                catalog,
                prefer_mod,
                utterance_prefer=utterance_prefer,
                where="moderator_model",
            )
            if not mod_result.ok:
                config.model_candidates = list(mod_result.candidates)
                return mod_result.error
            mod = mod_result.identity.normalized()
        else:
            shape = identity_shape_error(mod_named, where="moderator_model")
            if shape:
                return shape
            if catalog is None and session is None:
                return (
                    "moderator_model 已填模型身份但无法校验目录；"
                    "请稍后重试，禁止 silent 回退。"
                )
            mod_err = await validate_identity_in_catalog(
                session,
                user_id,
                mod_named,
                where="moderator_model",
                catalog=catalog,
            )
            if mod_err:
                return mod_err
            mod = mod_named
        config.moderator_model = mod.model
        config.moderator_origin = mod.origin
        config.moderator_provider_id = mod.provider_id
        config.moderator_route = mod.route_key() or (turn_model or "").strip()
        only_one = (
            catalog is not None and len(_available_entries(catalog)) <= 1
        )
        config.same_model_debate = only_one
        return ""

    debater_idents = [identity_from_side(s) for s in config.sides]
    effective = [
        (d if not d.is_empty() else turn_main) for d in debater_idents
    ]
    if catalog is not None:
        resolution = resolve_moderator_identity(
            catalog=catalog,
            debater_identities=effective,
            turn_main=turn_main,
        )
    else:
        resolution = ModeratorResolution(
            identity=turn_main, same_model_debate=True
        )

    mod = resolution.identity.normalized()
    config.moderator_model = mod.model
    config.moderator_origin = mod.origin
    config.moderator_provider_id = mod.provider_id
    config.moderator_route = mod.route_key() or (turn_model or "").strip()
    config.same_model_debate = bool(resolution.same_model_debate)
    return ""


async def ensure_debate_route_extras(
    llm: LLMProvider,
    identities: Sequence[ModelIdentity],
    *,
    user_id: str | None = None,
) -> None:
    """为辩手+裁判所需 prefix 注册 ProviderRouter extras（突破单 Worker extra）。

    平台身份一律挂 :func:`build_platform_provider`（按 request.model 取 key），
    不按「最后一个平台辩手」冻死单 key——跨平台模型辩论依赖此点（per-model credentials）。
    """
    from agentcore.billing.preference import platform_catalog_visible
    from agentcore.llm.factory import build_platform_provider, build_provider
    from agentcore.llm.provider.router import ProviderRouter
    from agentcore.llm.resolve import resolve_provider_credentials

    if not isinstance(llm, ProviderRouter):
        return
    needed_byok: dict[str, ModelIdentity] = {}
    need_platform = False
    for ident in identities:
        ident = ident.normalized()
        if ident.is_empty():
            continue
        prefix = ident.router_prefix()
        if not prefix:
            continue
        if prefix == PLATFORM_PROVIDER_SENTINEL:
            need_platform = True
            continue
        if prefix in llm.available_prefixes:
            continue
        needed_byok[prefix] = ident

    if need_platform and platform_catalog_visible():
        # 覆盖已有冻结 leaf（旧 build_turn_router / 误注册），保证一 key 一模型。
        llm.register(PLATFORM_PROVIDER_SENTINEL, build_platform_provider())

    if not needed_byok:
        return

    for prefix, _ident in needed_byok.items():
        if not user_id:
            continue
        from agentcore.db.base import async_session_factory

        async with async_session_factory() as session:
            creds = await resolve_provider_credentials(session, user_id, prefix)
        if creds is not None:
            llm.register(prefix, build_provider(creds))


def collect_debate_identities(config: DebateConfig, *, turn_model: str = "") -> list[ModelIdentity]:
    """辩手有效身份 + 裁判身份（供 router extras）。"""
    turn = ModelIdentity(model=turn_model).normalized()
    out: list[ModelIdentity] = []
    for side in config.sides:
        ident = identity_from_side(side)
        out.append(ident if not ident.is_empty() else turn)
    if (config.moderator_route or config.moderator_model or "").strip():
        out.append(
            ModelIdentity(
                model=config.moderator_model,
                origin=config.moderator_origin,
                provider_id=config.moderator_provider_id,
            ).normalized()
        )
    return out


def side_wire_fields(side: DebateSide) -> dict[str, Any]:
    """开赛卡 / debate_result sides 行：有已消歧身份才带字段（absent 兼容旧向量）。"""
    row: dict[str, Any] = {
        "key": side.key,
        "name": side.name,
        "stance": side.stance,
        "is_subject": bool(side.is_subject),
    }
    rid = (getattr(side, "run_id", "") or "").strip()
    if rid:
        row["run_id"] = rid
    ident = identity_from_side(side)
    if not ident.is_empty():
        row["model"] = ident.model
        if ident.origin:
            row["origin"] = ident.origin
        if ident.provider_id:
            row["provider_id"] = ident.provider_id
    return row


def allocate_debate_run_ids(
    config: DebateConfig,
    arguments: dict[str, Any] | None = None,
) -> str:
    """开赛前预分配主持人 + 各方稳定 ``run_id``（幂等：已有则复用）。

    - 主持人：``debate_{uuid}``（与开赛后主持人节点 id 同形）
    - 各方槽位：``{moderator_run_id}_{side.key}``（``model_overrides`` 键；≠ 各拍发言 run）

    写回 ``config`` 与可选 ``arguments``（resume blob / 开工卡持久化）。
    """
    from dataclasses import replace

    from agentcore.core.types import new_id

    args = arguments if isinstance(arguments, dict) else None
    mod = (getattr(config, "moderator_run_id", "") or "").strip()
    if not mod and args is not None:
        mod = str(args.get("moderator_run_id") or "").strip()
    if not mod:
        mod = f"debate_{new_id()}"
    config.moderator_run_id = mod

    arg_sides_by_key: dict[str, dict[str, Any]] = {}
    if args is not None:
        raw_sides = args.get("sides")
        if isinstance(raw_sides, list):
            for row in raw_sides:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("key") or "").strip()
                if key:
                    arg_sides_by_key[key] = row

    new_sides: list[DebateSide] = []
    for side in config.sides:
        rid = (getattr(side, "run_id", "") or "").strip()
        if not rid:
            arg_row = arg_sides_by_key.get(side.key)
            if arg_row is not None:
                rid = str(arg_row.get("run_id") or "").strip()
        if not rid:
            rid = f"{mod}_{side.key}"
        new_sides.append(replace(side, run_id=rid))
    config.sides = new_sides

    if args is not None:
        args["moderator_run_id"] = mod
        if (config.moderator_model or "").strip():
            args["moderator_model"] = config.moderator_model
            if (config.moderator_origin or "").strip():
                args["moderator_origin"] = config.moderator_origin
            if (config.moderator_provider_id or "").strip():
                args["moderator_provider_id"] = config.moderator_provider_id
        synced: list[dict[str, Any]] = []
        for side in config.sides:
            row = arg_sides_by_key.get(side.key)
            if row is None:
                row = {
                    "key": side.key,
                    "name": side.name,
                    "stance": side.stance,
                    "is_subject": bool(side.is_subject),
                }
            else:
                row = dict(row)
            wire = side_wire_fields(side)
            row["run_id"] = side.run_id
            for k in ("model", "origin", "provider_id"):
                if k in wire:
                    row[k] = wire[k]
                else:
                    row.pop(k, None)
            synced.append(row)
        args["sides"] = synced

    return mod
