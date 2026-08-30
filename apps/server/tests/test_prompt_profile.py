"""方向① 变体注入的运行期机制测试（contextvar + 装配 resolve），零 LLM.

守两件事：(1) **逐字节守恒**——无变体 / 空变体时 ``assemble_*`` 输出与现状一致（DeepSeek
前缀缓存安全的命脉）；(2) 覆盖某 key 只换那一段、其余不动，且空串覆盖=干净移除（消融）。
``use_profile`` 的 set/reset/嵌套/异常复位也在此守，防变体泄漏出作用域。
"""

from __future__ import annotations

from agentcore.runtime.resolve.profile import (
    FRAGMENT_BASE,
    FRAGMENT_CEO_CORE,
    OVERRIDABLE_KEYS,
    PromptProfile,
    active_profile,
    resolve,
    use_profile,
)
from agentcore.runtime.resolve.prompt import (
    assemble_system_prompt,
    compose_ceo_chat_prompt,
)
from agentcore.runtime.skills import build_system_skill_registry


def _ceo() -> str:
    return compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )


# --- PromptProfile.resolve + contextvar 生命周期 ---


def test_resolve_override_else_default() -> None:
    p = PromptProfile("x", {FRAGMENT_BASE: "NEW"})
    assert p.resolve(FRAGMENT_BASE, "OLD") == "NEW"
    assert p.resolve(FRAGMENT_CEO_CORE, "OLD") == "OLD"


def test_resolve_is_identity_without_active_profile() -> None:
    assert active_profile() is None
    assert resolve(FRAGMENT_BASE, "DEFAULT") == "DEFAULT"


def test_use_profile_sets_and_resets() -> None:
    assert active_profile() is None
    with use_profile(PromptProfile("x", {})):
        assert active_profile() is not None
        assert active_profile().name == "x"
    assert active_profile() is None


def test_use_profile_nested_restores_outer() -> None:
    with use_profile(PromptProfile("outer", {})):
        with use_profile(PromptProfile("inner", {})):
            assert active_profile().name == "inner"
        assert active_profile().name == "outer"
    assert active_profile() is None


def test_use_profile_resets_on_exception() -> None:
    try:
        with use_profile(PromptProfile("x", {})):
            raise ValueError("boom")
    except ValueError:
        pass
    assert active_profile() is None


def test_overridable_keys_are_the_static_fragments() -> None:
    assert {FRAGMENT_BASE, FRAGMENT_CEO_CORE} == OVERRIDABLE_KEYS


# --- 逐字节守恒（前缀缓存安全的命脉） ---


def test_assemble_byte_identical_without_or_with_empty_profile() -> None:
    out = assemble_system_prompt()
    with use_profile(None):  # 显式恒等
        assert assemble_system_prompt() == out
    with use_profile(PromptProfile("noop", {})):  # 空 overrides == 恒等
        assert assemble_system_prompt() == out
    assert assemble_system_prompt() == out  # 退出作用域后仍一致


def test_compose_byte_identical_without_or_with_empty_profile() -> None:
    out = _ceo()
    with use_profile(PromptProfile("noop", {})):
        assert _ceo() == out


# --- 覆盖只换目标片段、其余不动 ---


def test_base_override_reaches_both_workers_and_ceo() -> None:
    sentinel = "SENTINEL_BASE_乱炖_xyz"
    with use_profile(PromptProfile("x", {FRAGMENT_BASE: sentinel})):
        base = assemble_system_prompt()
        ceo = _ceo()
    assert sentinel in base
    assert "<输出>" not in base  # 原 base 文案被换掉
    assert sentinel in ceo  # CEO 的 base_prompt 即 assemble 输出，故也变


def test_ceo_core_override_only_swaps_ceo_core() -> None:
    sentinel = "SENTINEL_CORE_xyz"
    with use_profile(PromptProfile("x", {FRAGMENT_CEO_CORE: sentinel})):
        ceo = _ceo()
        base = assemble_system_prompt()
    assert sentinel in ceo
    assert "<身份>" not in ceo  # 原 ceo_core 被换掉
    assert "<输出>" in base  # base 片段未受影响（隔离）


def test_empty_override_ablates_fragment_cleanly() -> None:
    # 消融：空串覆盖 → assembler 跳过 falsy → 整段移除、不留空行。
    with use_profile(PromptProfile("ablate", {FRAGMENT_CEO_CORE: ""})):
        ceo = _ceo()
    assert "<身份>" not in ceo
    assert "\n\n\n" not in ceo  # 没有因移除留下连续空行
    assert "<输出>" in ceo  # 其余片段完好


def test_citation_block_is_absent_from_production_ceo() -> None:
    ceo = _ceo()
    assert "<citing_sources>" not in ceo
