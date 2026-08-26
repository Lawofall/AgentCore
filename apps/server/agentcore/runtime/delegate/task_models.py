"""Per-worker 模型覆盖（编排器权威段）：delegate/replan 任务目录身份 → RunSpec.model。

空身份 = 跟组合 Worker 槽（合法）；非空须目录身份（``@`` 句柄或可消歧提及）+ 目录校验，非法硬失败。
校验通过后把身份编成路由键写入 ``item["model"]``（builder 只透传该字符串）。
跨 provider 经 :func:`ensure_debate_route_extras` 窄接注册 router extras。

→ 见设计: docs/03-AI核心/编排器与CEO主Agent.md §Per-worker 模型覆盖
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agentcore.llm.model_ref import parse_model_input
from agentcore.runtime.debate.models import (
    ModelIdentity,
    coerce_identity,
    ensure_debate_route_extras,
    identity_shape_error,
    needs_mention_resolve,
    resolve_model_mention,
    validate_identity_in_catalog,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from agentcore.llm.catalog import ModelCatalog
    from agentcore.llm.provider.protocol import LLMProvider

# Shared JSON-schema fragment (delegate tasks + replan binds/add).
TASK_MODEL_SCHEMA_PROPS: dict[str, dict[str, object]] = {
    "model": {
        "type": "string",
        "description": (
            "（可选）本节点模型：目录身份 @platform/{id} 或 @byok/{provider_id}/{id}，"
            "或可读提及。空=跟组合 Worker 槽。勿写未加 @ 的 platform/{id} 路由键。"
        ),
    },
}


def identity_from_task_item(item: Mapping[str, Any]) -> ModelIdentity:
    """从 task/bind/add 字典读目录身份（空 model = 未指定）。"""
    raw_model = str(item.get("model") or "").strip()
    parsed = parse_model_input(raw_model)
    if parsed.kind == "ref":
        ident = ModelIdentity(
            model=parsed.model,
            origin=parsed.origin,
            provider_id=parsed.provider_id,
        ).normalized()
        return ident
    origin = str(item.get("origin") or "").strip().lower()
    provider_id = str(item.get("provider_id") or "").strip()
    return ModelIdentity(
        model=raw_model,
        origin=origin,
        provider_id=provider_id,
    ).normalized()


async def prepare_task_model_fields(
    items: Sequence[Any],
    *,
    user_id: str,
    where_prefix: str = "tasks",
    session: AsyncSession | None = None,
    catalog: ModelCatalog | None = None,
    inherit_model: Callable[[str], str] | None = None,
) -> tuple[list[str], list[ModelIdentity]]:
    """校验并编码每项模型字段；成功时原地把 ``model`` 写成路由键。

    Returns ``(errors, identities)``。``errors`` 非空时调用方不得继续建计划。
    空身份合法：可选经 ``inherit_model(continue_from_run_id)`` 继承既有路由键。
    """
    errors: list[str] = []
    identities: list[ModelIdentity] = []
    if not items:
        return errors, identities

    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        where = f"{where_prefix}[{i}]"
        ident = identity_from_task_item(raw)
        ident, coerce_err = coerce_identity(ident)
        if coerce_err:
            errors.append(f"{where}.model {coerce_err}")
            continue
        if ident.is_empty():
            # 同人续派：默认继承该 run 已解析模型（路由键）；本次显式改则走下方非空分支。
            cf = str(raw.get("continue_from_run_id") or "").strip()
            if cf and inherit_model is not None:
                inherited = (inherit_model(cf) or "").strip()
                if inherited:
                    raw["model"] = inherited
            continue

        if needs_mention_resolve(ident):
            if catalog is None and session is None:
                errors.append(
                    f"{where}.model 已填模型提及「{ident.model}」但无法加载目录消歧；"
                    "请稍后重试，禁止 silent 回退。"
                )
                continue
            from agentcore.llm.catalog import resolve_model_catalog as _resolve_cat

            cat = catalog
            if cat is None and session is not None and user_id:
                cat = await _resolve_cat(session, user_id)
            if cat is None:
                errors.append(
                    f"{where}.model 已填模型提及「{ident.model}」但无法加载目录消歧；"
                    "请稍后重试，禁止 silent 回退。"
                )
                continue
            prefer = ident.origin if ident.origin in ("platform", "byok") else None
            result = resolve_model_mention(
                ident.model,
                cat,
                prefer,  # type: ignore[arg-type]
                where=f"{where}.model",
            )
            if not result.ok:
                errors.append(result.error)
                continue
            ident = result.identity
            catalog = cat

        shape = identity_shape_error(ident, where=f"{where}.model")
        if shape:
            errors.append(shape)
            continue

        if not user_id and catalog is None and session is None:
            errors.append(
                f"{where}.model 已填模型身份但无法校验目录；请稍后重试，禁止 silent 回退。"
            )
            continue

        catalog_err = await validate_identity_in_catalog(
            session,
            user_id,
            ident,
            where=f"{where}.model",
            catalog=catalog,
        )
        if catalog_err:
            errors.append(catalog_err)
            continue

        route = ident.route_key()
        if not route:
            errors.append(f"{where}.model 无法编成路由键。")
            continue
        raw["model"] = route
        # 路由键已写入；清掉 origin/provider 避免下游误把路由键当裸 id 再解析。
        raw.pop("origin", None)
        raw.pop("provider_id", None)
        identities.append(ident)

    return errors, identities


async def ensure_delegate_route_extras(
    llm: LLMProvider,
    identities: Sequence[ModelIdentity],
    *,
    user_id: str | None = None,
) -> None:
    """多 identity 跨 provider：复用辩论 extras 注册（窄接，不搬辩论整锅）。"""
    if not identities:
        return
    await ensure_debate_route_extras(llm, identities, user_id=user_id)


def inherit_model_from_tool(tool: Any, continue_from_run_id: str) -> str:
    """从 session / 同图计划解析续派源 run 的已解析路由键（无则空）。"""
    target = (continue_from_run_id or "").strip()
    if not target:
        return ""
    store = getattr(tool, "_session_store", None)
    if store is not None:
        get = getattr(store, "get", None)
        if callable(get):
            sess = get(target)
            if sess is not None:
                model = str(getattr(getattr(sess, "spec", None), "model", "") or "").strip()
                if model:
                    return model
    for plan_attr in ("_last_graph_plan",):
        plan = getattr(tool, plan_attr, None)
        if plan is None:
            continue
        by_id = getattr(plan, "by_id", None)
        if not callable(by_id):
            continue
        node = by_id(target)
        if node is not None:
            model = str(getattr(node, "model", "") or "").strip()
            if model:
                return model
    seed = getattr(tool, "_last_graph_seed", None)
    if isinstance(seed, dict):
        st = seed.get(target)
        if st is not None:
            # RunState.model 是计费裸 id；仅当无路由键时不拿它冒充路由键。
            pass
    return ""
