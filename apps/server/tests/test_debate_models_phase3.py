"""Phase 3 真·多模型辩手：身份解析 / 默认对阵 / 裁判选型 / 注入优先。"""

from __future__ import annotations

import pytest

from agentcore.llm.catalog import ModelCatalog, ModelCatalogCurrent, ModelCatalogEntry
from agentcore.runtime.debate.models import (
    ModelIdentity,
    coerce_identity,
    identity_shape_error,
    resolve_default_matchup,
    resolve_moderator_identity,
    side_route_model,
)
from agentcore.runtime.debate.prompt import debater_task
from agentcore.runtime.debate.types import DebateConfig, DebateForm, DebateSide
from agentcore.tools.builtin.debate.schema import parse_sides


def _entry(
    mid: str,
    *,
    origin: str = "platform",
    provider_id: str | None = None,
    available: bool = True,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=mid,
        origin=origin,  # type: ignore[arg-type]
        display_name=mid,
        vendor="test",
        available=available,
        provider_id=provider_id,
    )


def _catalog(*entries: ModelCatalogEntry) -> ModelCatalog:
    return ModelCatalog(
        current=ModelCatalogCurrent(id="x", origin="platform"),
        byok_configured=True,
        models=list(entries),
    )


# --- 解析 / 形状 -------------------------------------------------------------


def test_parse_sides_empty_model_ok():
    sides, err = parse_sides(
        [
            {"key": "a", "name": "正", "stance": "支持"},
            {"key": "b", "name": "反", "stance": "反对"},
        ]
    )
    assert err == ""
    assert sides[0].model == "" and sides[0].origin == ""


def test_parse_sides_nonempty_requires_origin():
    sides, err = parse_sides(
        [
            {"key": "a", "name": "正", "stance": "支持", "model": "gpt-4o"},
            {"key": "b", "name": "反", "stance": "反对"},
        ]
    )
    # §7.5 B：缺 origin 视为待消歧提及，parse 不硬拒。
    assert err == ""
    assert sides[0].model == "gpt-4o" and sides[0].origin == ""


def test_parse_sides_byok_requires_provider_id():
    sides, err = parse_sides(
        [
            {
                "key": "a",
                "name": "正",
                "stance": "支持",
                "model": "deepseek-chat",
                "origin": "byok",
            },
            {"key": "b", "name": "反", "stance": "反对"},
        ]
    )
    # byok 缺 provider_id → 交 prepare 消歧，parse 不硬拒。
    assert err == ""
    assert sides[0].origin == "byok" and sides[0].provider_id == ""


def test_parse_sides_triple_ok():
    sides, err = parse_sides(
        [
            {
                "key": "a",
                "name": "正",
                "stance": "支持",
                "model": "gpt-4o",
                "origin": "platform",
            },
            {
                "key": "b",
                "name": "反",
                "stance": "反对",
                "model": "deepseek-chat",
                "origin": "byok",
                "provider_id": "prov-ds",
            },
        ]
    )
    assert err == ""
    assert sides[0].origin == "platform" and sides[0].provider_id == ""
    assert sides[1].provider_id == "prov-ds"


def test_identity_shape_error_empty_ok():
    assert identity_shape_error(ModelIdentity()) == ""


def test_identity_shape_error_bare_model_is_mention():
    assert identity_shape_error(ModelIdentity(model="gpt-4o")) == ""


def test_identity_shape_error_at_ref_expands():
    err = identity_shape_error(ModelIdentity(model="@platform/glm-5.2"))
    assert err == ""
    ident, coerce_err = coerce_identity(ModelIdentity(model="@platform/glm-5.2"))
    assert coerce_err == ""
    assert ident.origin == "platform" and ident.model == "glm-5.2"


def test_identity_shape_error_bad_at_prefix():
    err = identity_shape_error(ModelIdentity(model="@foo/bar"))
    assert err
    assert "@platform" in err or "目录身份" in err


def test_parse_sides_platform_ref():
    sides, err = parse_sides(
        [
            {
                "key": "a",
                "name": "正",
                "stance": "支持",
                "model": "@platform/gpt-4o",
            },
            {"key": "b", "name": "反", "stance": "反对"},
        ]
    )
    assert err == ""
    assert sides[0].model == "gpt-4o" and sides[0].origin == "platform"


def test_route_key_platform_and_byok():
    assert (
        ModelIdentity(model="gpt-4o", origin="platform").route_key()
        == "platform/gpt-4o"
    )
    assert (
        ModelIdentity(
            model="deepseek-chat", origin="byok", provider_id="p1"
        ).route_key()
        == "p1/deepseek-chat"
    )


def test_side_route_model_prefers_nonempty_side():
    side = DebateSide(
        key="a",
        name="正",
        stance="支持",
        model="gpt-4o",
        origin="platform",
    )
    assert side_route_model(side, turn_model="turn-main") == "platform/gpt-4o"


def test_side_route_model_empty_falls_back_to_turn():
    side = DebateSide(key="a", name="正", stance="支持")
    assert side_route_model(side, turn_model="turn-main") == "turn-main"


def test_debater_task_injects_side_route_key():
    sides, err = parse_sides(
        [
            {
                "key": "a",
                "name": "豆包",
                "stance": "我最聪明",
                "model": "gpt-4o",
                "origin": "platform",
            },
            {"key": "b", "name": "DeepSeek", "stance": "我才最聪明"},
        ]
    )
    assert err == ""
    cfg = DebateConfig(motion="谁更聪明", form=DebateForm.DEBATE, sides=sides)
    t_a = debater_task(
        cfg, sides[0], 0, round_no=1, focus="智商", turn_model="turn-main"
    )
    t_b = debater_task(
        cfg, sides[1], 1, round_no=1, focus="智商", turn_model="turn-main"
    )
    assert t_a["model"] == "platform/gpt-4o"
    assert t_b["model"] == "turn-main"


def test_debater_task_empty_sides_use_turn_main():
    sides, err = parse_sides(
        [
            {"key": "a", "name": "正", "stance": "支持"},
            {"key": "b", "name": "反", "stance": "反对"},
        ]
    )
    assert err == ""
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    t = debater_task(cfg, sides[0], 0, round_no=1, focus="f", turn_model="main-pro")
    assert t["model"] == "main-pro"


# --- 默认对阵 / 裁判 ---------------------------------------------------------


def test_default_matchup_platform_allowlist(monkeypatch):
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["plat-a", "plat-b", "plat-c"],
    )
    cat = _catalog(
        _entry("plat-a"),
        _entry("plat-b"),
        _entry("plat-c"),
        _entry("byok-first", origin="byok", provider_id="p1"),
    )
    match = resolve_default_matchup(cat)
    assert match is not None
    assert match[0].model == "plat-a" and match[1].model == "plat-b"
    assert match[0].origin == "platform"


def test_default_matchup_platform_plus_byok_deepseek(monkeypatch):
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["only-plat"],
    )
    cat = _catalog(
        _entry("only-plat"),
        _entry("deepseek-chat", origin="byok", provider_id="ds"),
    )
    match = resolve_default_matchup(cat)
    assert match is not None
    assert match[0].model == "only-plat"
    assert match[1].model == "deepseek-chat" and match[1].origin == "byok"


def test_default_matchup_unavailable_when_single(monkeypatch):
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["only"],
    )
    cat = _catalog(_entry("only"))
    assert resolve_default_matchup(cat) is None


def test_moderator_prefers_deepseek(monkeypatch):
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["gpt-4o", "deepseek-v4-flash"],
    )
    cat = _catalog(
        _entry("gpt-4o"),
        _entry("deepseek-v4-flash"),
        _entry("other"),
    )
    debaters = [
        ModelIdentity(model="gpt-4o", origin="platform"),
        ModelIdentity(model="other", origin="platform"),
    ]
    res = resolve_moderator_identity(
        catalog=cat,
        debater_identities=debaters,
        turn_main=ModelIdentity(model="gpt-4o", origin="platform"),
    )
    assert res.identity.model == "deepseek-v4-flash"
    assert res.same_model_debate is False


def test_moderator_default_allows_same_as_debater_deepseek(monkeypatch):
    """未点名且辩手已用 DeepSeek → 默认仍可选 DeepSeek，不再跳到其它平台槽。"""
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["relay-b", "deepseek-v4-flash"],
    )
    cat = _catalog(
        _entry("relay-b"),
        _entry("deepseek-v4-flash"),
    )
    ds = ModelIdentity(model="deepseek-v4-flash", origin="platform")
    res = resolve_moderator_identity(
        catalog=cat,
        debater_identities=[
            ModelIdentity(model="relay-b", origin="platform"),
            ds,
        ],
        turn_main=ModelIdentity(model="relay-b", origin="platform"),
    )
    assert res.identity.model == "deepseek-v4-flash"
    assert res.same_model_debate is False


def test_moderator_degrades_same_model_when_only_one(monkeypatch):
    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["only"],
    )
    cat = _catalog(_entry("only"))
    turn = ModelIdentity(model="only", origin="platform")
    res = resolve_moderator_identity(
        catalog=cat,
        debater_identities=[turn, turn],
        turn_main=turn,
    )
    assert res.same_model_debate is True
    assert res.identity.model == "only"


@pytest.mark.asyncio
async def test_prepare_named_moderator_deepseek_same_as_con():
    """反方 DeepSeek + moderator_model=DeepSeek → 裁判 identity 为该 DeepSeek。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(
            key="pro",
            name="正",
            stance="支持",
            model="gpt-4o",
            origin="platform",
        ),
        DebateSide(
            key="con",
            name="反",
            stance="反对",
            model="deepseek-chat",
            origin="byok",
            provider_id="ds",
        ),
    ]
    cfg = DebateConfig(
        motion="m",
        form=DebateForm.DEBATE,
        sides=sides,
        moderator_model="DeepSeek",
    )
    cat = _catalog(
        _entry("gpt-4o"),
        _entry("relay-b"),
        _entry("deepseek-chat", origin="byok", provider_id="ds"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        turn_origin="platform",
        catalog=cat,
    )
    assert err == ""
    assert cfg.moderator_model == "deepseek-chat"
    assert cfg.moderator_origin == "byok"
    assert cfg.moderator_provider_id == "ds"
    assert cfg.moderator_route == "ds/deepseek-chat"
    assert cfg.same_model_debate is False


@pytest.mark.asyncio
async def test_prepare_named_moderator_beats_auto_default(monkeypatch):
    """点名裁判优先于自动默认（目录有 DeepSeek 默认槽，点名 relay-b 则用 relay-b）。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["relay-b", "deepseek-v4-flash"],
    )
    sides = [
        DebateSide(
            key="pro",
            name="正",
            stance="支持",
            model="gpt-4o",
            origin="platform",
        ),
        DebateSide(
            key="con",
            name="反",
            stance="反对",
            model="deepseek-v4-flash",
            origin="platform",
        ),
    ]
    cfg = DebateConfig(
        motion="m",
        form=DebateForm.DEBATE,
        sides=sides,
        moderator_model="relay-b",
        moderator_origin="platform",
    )
    cat = _catalog(
        _entry("gpt-4o"),
        _entry("relay-b"),
        _entry("deepseek-v4-flash"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        catalog=cat,
    )
    assert err == ""
    assert cfg.moderator_model == "relay-b"
    assert cfg.moderator_origin == "platform"


@pytest.mark.asyncio
async def test_prepare_unnamed_moderator_keeps_deepseek_when_debater_uses_it(
    monkeypatch,
):
    """未点名且反方已是 DeepSeek → 默认仍选 DeepSeek，不开到其它平台槽。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["relay-b", "deepseek-v4-flash"],
    )
    sides = [
        DebateSide(
            key="pro",
            name="正",
            stance="支持",
            model="gpt-4o",
            origin="platform",
        ),
        DebateSide(
            key="con",
            name="反",
            stance="反对",
            model="deepseek-v4-flash",
            origin="platform",
        ),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(
        _entry("gpt-4o"),
        _entry("relay-b"),
        _entry("deepseek-v4-flash"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        catalog=cat,
    )
    assert err == ""
    assert cfg.moderator_model == "deepseek-v4-flash"
    assert cfg.moderator_origin == "platform"


@pytest.mark.asyncio
async def test_prepare_rejects_invalid_nonempty_no_silent():
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(
            key="a",
            name="正",
            stance="支持",
            model="no-such-model",
            origin="platform",
        ),
        DebateSide(key="b", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(_entry("gpt-4o"))
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        catalog=cat,
    )
    assert err
    assert "silent" in err or "目录" in err
    assert cfg.sides[0].model == "no-such-model"


def test_schema_no_longer_says_mvp_leave_empty():
    from agentcore.tools.builtin.debate.schema import DEBATE_PARAMETERS

    model_desc = DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]["model"][
        "description"
    ]
    assert "MVP 未启用" not in model_desc
    assert "请留空" not in model_desc
    assert "origin" not in DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]
    assert "provider_id" not in DEBATE_PARAMETERS["properties"]["sides"]["items"]["properties"]
    assert "@platform" in model_desc


def test_debate_kickoff_summary_includes_moderator_and_side_models():
    from agentcore.runtime.kickoff.summary import debate_kickoff_summary

    cfg = DebateConfig(
        motion="谁更聪明",
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(
                key="a",
                name="正",
                stance="支持",
                model="gpt-4o",
                origin="platform",
            ),
            DebateSide(key="b", name="反", stance="反对"),
        ],
        moderator_model="deepseek-v4-flash",
        moderator_origin="platform",
        same_model_debate=False,
    )
    summary = debate_kickoff_summary(cfg, arguments={})
    card = summary.card_payload()
    assert card["sides"][0]["model"] == "gpt-4o"
    assert card["sides"][0]["origin"] == "platform"
    assert "model" not in card["sides"][1]
    assert card["moderator_model"] == "deepseek-v4-flash"
    assert card["moderator_origin"] == "platform"


def test_skill_teaches_catalog_ref_not_mvp_empty():
    from agentcore.runtime.skills import build_system_skill_registry

    body = build_system_skill_registry().get("debate_and_review").body
    assert "MVP 未启用" not in body
    assert "请留空" not in body
    assert "cross_model" in body
    assert "禁止" in body and "元问题" in body
    assert "平台 glm-5.2" in body or "DeepSeek" in body
    assert "PLATFORM_MODELS" in body or "跨模型" in body
    assert "中立槽" not in body
    assert "moderator_model" in body
    assert "可与辩手同模" in body
    assert "@platform" in body or "@byok" in body
    assert "platform/xxx" in body or "路由键" in body


def test_schema_exposes_moderator_model():
    from agentcore.tools.builtin.debate.schema import DEBATE_PARAMETERS

    props = DEBATE_PARAMETERS["properties"]
    assert "moderator_model" in props
    assert "moderator_origin" not in props
    assert "moderator_provider_id" not in props


def test_parse_moderator_fields_mention_ok():
    from agentcore.tools.builtin.debate.schema import parse_moderator_fields

    model, origin, provider_id, err = parse_moderator_fields("DeepSeek")
    assert err == ""
    assert model == "DeepSeek" and origin == "" and provider_id == ""


def test_parse_moderator_fields_triple_ok():
    from agentcore.tools.builtin.debate.schema import parse_moderator_fields

    model, origin, provider_id, err = parse_moderator_fields(
        "deepseek-chat", "byok", "ds"
    )
    assert err == ""
    assert model == "deepseek-chat"
    assert origin == "byok"
    assert provider_id == "ds"


# --- §7.5 B+C 消歧层 ---------------------------------------------------------


def test_resolve_mention_52_and_platform_prefix():
    from agentcore.runtime.debate.models import resolve_model_mention

    cat = _catalog(
        _entry("glm-5.2", origin="platform"),
        _entry("deepseek-chat", origin="byok", provider_id="ds"),
    )
    r1 = resolve_model_mention("glm-5.2", cat)
    assert r1.ok and r1.identity.model == "glm-5.2" and r1.identity.origin == "platform"
    r2 = resolve_model_mention("平台 glm-5.2", cat)
    assert r2.ok and r2.identity.origin == "platform" and r2.identity.model == "glm-5.2"


def test_resolve_mention_deepseek_byok():
    from agentcore.runtime.debate.models import resolve_model_mention

    cat = _catalog(
        _entry("glm-5.2"),
        _entry("deepseek-chat", origin="byok", provider_id="prov-ds"),
    )
    r = resolve_model_mention("DeepSeek", cat)
    assert r.ok
    assert r.identity.model == "deepseek-chat"
    assert r.identity.origin == "byok"
    assert r.identity.provider_id == "prov-ds"


def test_resolve_mention_ambiguous_deepseek():
    from agentcore.runtime.debate.models import resolve_model_mention

    cat = _catalog(
        _entry("deepseek-chat", origin="byok", provider_id="a"),
        _entry("deepseek-v4-flash", origin="byok", provider_id="b"),
    )
    r = resolve_model_mention("DeepSeek", cat, side_key="con")
    assert not r.ok
    assert len(r.candidates) >= 2
    assert "元问题" in r.error or "消歧" in r.error


@pytest.mark.asyncio
async def test_prepare_cross_model_fills_default_matchup(monkeypatch):
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    monkeypatch.setattr(
        "agentcore.billing.preference.platform_model_allowlist",
        lambda: ["plat-a", "plat-b"],
    )
    sides = [
        DebateSide(key="a", name="正", stance="支持"),
        DebateSide(key="b", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(_entry("plat-a"), _entry("plat-b"))
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="plat-a",
        catalog=cat,
        cross_model=True,
    )
    assert err == ""
    assert cfg.sides[0].model == "plat-a" and cfg.sides[0].origin == "platform"
    assert cfg.sides[1].model == "plat-b" and cfg.sides[1].origin == "platform"


@pytest.mark.asyncio
async def test_prepare_empty_without_flag_same_model():
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(key="a", name="正", stance="支持"),
        DebateSide(key="b", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(_entry("gpt-4o"), _entry("other"))
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        turn_origin="platform",
        catalog=cat,
        cross_model=False,
    )
    assert err == ""
    assert cfg.sides[0].model == "" and cfg.sides[1].model == ""


@pytest.mark.asyncio
async def test_prepare_disambiguates_mentions_then_validates():
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(key="pro", name="正", stance="支持", model="平台 glm-5.2"),
        DebateSide(key="con", name="反", stance="反对", model="DeepSeek"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(
        _entry("glm-5.2"),
        _entry("deepseek-chat", origin="byok", provider_id="ds"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="glm-5.2",
        catalog=cat,
    )
    assert err == ""
    assert cfg.sides[0].model == "glm-5.2" and cfg.sides[0].origin == "platform"
    assert cfg.sides[1].model == "deepseek-chat"
    assert cfg.sides[1].origin == "byok"
    assert cfg.sides[1].provider_id == "ds"


@pytest.mark.asyncio
async def test_prepare_ambiguous_sets_model_candidates():
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(key="pro", name="正", stance="支持", model="DeepSeek"),
        DebateSide(key="con", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(
        _entry("deepseek-chat", origin="byok", provider_id="a"),
        _entry("deepseek-coder", origin="byok", provider_id="b"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="x",
        catalog=cat,
    )
    assert err
    assert cfg.model_candidates
    assert all("ref" in c and c["ref"].startswith("@") for c in cfg.model_candidates)
    assert "@byok/" in err
    assert "model=" not in err
    assert "origin=" not in err


@pytest.mark.asyncio
async def test_prepare_utterance_prefer_disambiguates_platform_byok():
    """用户话含「平台的」+ 同名 platform/byok → 一次消歧到 platform。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(key="pro", name="正", stance="支持", model="glm-5.2", origin="platform"),
        DebateSide(key="con", name="反", stance="反对", model="DeepSeek V4 flash"),
    ]
    cfg = DebateConfig(motion="谁更聪明", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(
        _entry("glm-5.2"),
        ModelCatalogEntry(
            id="deepseek-v4-flash",
            origin="platform",
            display_name="DeepSeek V4 flash",
            vendor="test",
            available=True,
            provider_id=None,
        ),
        ModelCatalogEntry(
            id="deepseek-v4-flash",
            origin="byok",
            display_name="DeepSeek V4 flash",
            vendor="test",
            available=True,
            provider_id="ds-byok",
        ),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="glm-5.2",
        catalog=cat,
        user_message="用平台的 DeepSeek V4 flash 跟 glm 辩一场",
    )
    assert err == ""
    assert cfg.sides[1].model == "deepseek-v4-flash"
    assert cfg.sides[1].origin == "platform"
    assert cfg.sides[1].provider_id == ""


@pytest.mark.asyncio
async def test_prepare_unprefixed_route_key_is_mention_not_stripped():
    """未加 @ 的 platform/{id} 不当句柄、不剥前缀；目录零匹配后挂 @ref 候选。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(
            key="pro",
            name="正",
            stance="支持",
            model="platform/deepseek-v4-flash",
        ),
        DebateSide(key="con", name="反", stance="反对"),
    ]
    cfg = DebateConfig(motion="m", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(_entry("deepseek-v4-flash"), _entry("gpt-4o"))
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        catalog=cat,
    )
    assert err
    assert "platform/platform" not in err
    assert "@platform/deepseek-v4-flash" in err
    assert cfg.sides[0].model == "platform/deepseek-v4-flash"


def test_resolve_mention_candidate_tip_field_format():
    from agentcore.runtime.debate.models import resolve_model_mention

    cat = _catalog(
        _entry("deepseek-chat", origin="byok", provider_id="a"),
        _entry("deepseek-coder", origin="byok", provider_id="b"),
    )
    r = resolve_model_mention("DeepSeek", cat, side_key="con")
    assert not r.ok
    assert "@byok/" in r.error
    assert "model=" not in r.error
    assert "origin=" not in r.error


def test_infer_utterance_origin_preference_platform():
    from agentcore.runtime.debate.models import infer_utterance_origin_preference

    assert (
        infer_utterance_origin_preference("用平台的 DeepSeek 辩", "谁更聪明")
        == "platform"
    )
    assert infer_utterance_origin_preference("", "byok deepseek") == "byok"
    assert infer_utterance_origin_preference("随便辩一场", "开放命题") is None


@pytest.mark.asyncio
async def test_prepare_no_prefer_still_ambiguous_hard_fail():
    """无 utterance 偏好 + platform/byok 同提及 → 仍多候选硬失败（禁 silent）。"""
    from agentcore.runtime.debate.models import prepare_debate_model_plan

    sides = [
        DebateSide(key="pro", name="正", stance="支持", model="gpt-4o", origin="platform"),
        DebateSide(key="con", name="反", stance="反对", model="deepseek-v4-flash"),
    ]
    cfg = DebateConfig(motion="开放辩题", form=DebateForm.DEBATE, sides=sides)
    cat = _catalog(
        _entry("gpt-4o"),
        _entry("deepseek-v4-flash", origin="platform"),
        _entry("deepseek-v4-flash", origin="byok", provider_id="ds"),
    )
    err = await prepare_debate_model_plan(
        cfg,
        user_id="u1",
        turn_model="gpt-4o",
        catalog=cat,
        user_message="两边辩一下",
    )
    assert err
    assert len(cfg.model_candidates) >= 2
    assert "silent" not in cfg.sides[1].origin  # 未写回 silent 身份
    assert cfg.sides[1].origin == ""  # 未 silent 选定
