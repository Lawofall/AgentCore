"""常驻核体积棘轮 + 权威归属——CEO 提示词只许瘦、不许悄悄回潮。

## 为什么有这道棘轮

工具 schema 早就有下行棘轮（``test_tool_schema_size_ratchet.py``），常驻核一直没有，而
``test_prompt.py`` 里七十多条断言几乎全是「某条禁令的字面必须出现在 ``_CEO_CORE_HINT`` 里」，
半数带着事故编号。于是激励是单向的：**加一条禁令零成本还有奖励**（测试证明事故修了），
**删掉或通用化一条要付红灯**、在 review 里看起来像退步。核就是这么长到两万字符的。

比体积更要紧的是**权威归属**：同一件事只能有一处权威。

- 环境能不能做某事 = **算出来的事实**，住 ``<workspace_context>``（``本回合执行能力`` /
  ``产物格式`` / ``装包事实`` / ``出站网络`` …）。核里复述一份就会漂——案 0a71 就是核里
  散文断言「``md_to_docx`` / ``md_to_pdf`` 无条件装配」，而 CEO 并不持这两把工具、在自己的
  工具列表里看不见它们，模型于是花了整段思考链猜「队员到底有没有」，最后把用户的三个选项
  连同提问一起丢了。**下面那条 assembly-claim 测试就是这个 bug 的回归守卫。**
- 可履约的操作手册 = 跟**装配门**走（``capability_how_suffix``）。通道/工具不在的回合，
  手册是一份证明履行不了的说明书。
- **诚实底线相反：它恰在能力缺失的那一回合才生效，必须常驻**，不许按可用性下线。
  所以「禁止把仅结构自检说成跑绿」「禁止称不可产的工具已装配」这类留在核里是对的。

## 红了怎么办

- **超了上限**：先问这段是不是三类之一——已在事实行算出来的、有门可挂的手册、
  已下沉 skill 的 HOW。是就搬走，别在核里再写一份。确实是**新增**的常驻路由语义 →
  把数字调上去，并在 PR 里说清多出来的是什么。
- **远低于上限**（又搬走一批）：把数字调下来，棘轮才继续咬合。
- **权威归属红了**：不要靠改字面绕过（把「无条件装配」换个说法照样是断言）。
  正解是删掉核里那份，改成指事实行 / 挂门 / 进 skill。

数字 = 当次实测值向上取整到十位；只许降不许升。
量的是 **CEO 常驻总长**（``assemble_system_prompt`` + ``_CEO_CORE_HINT``），不是核单列——
同一条纪律跨基座/核搬迁时核字数会涨、总长才是真实成本。
"""

from __future__ import annotations

import pytest

from agentcore.runtime.resolve.prompt import (
    _CEO_CORE_HINT,
    assemble_ceo_core,
    assemble_system_prompt,
)

# 2026-08-19 跨界搬迁：交付验收对照 / 可用性短问 / 概览契约从共享基座迁入核
# （队员开场用不到；finish_guard 仅 CEO 路径查）。核 +319、基座 −467，
# 基座+核 24472 → 24324（净 −148）。核单列不再代表真实成本，改量 CEO 常驻总长。
# 当次实测 24324。
#
# 2026-08-20 降水位（不是抬顶）：五项迁出常驻核——本轮材料收窄+附件缺件改场面门、
# 删冷启动探索幕核内副本、产品面地图→product_help_map、成品文件只装成品→long_form_writing、
# 删执行事实行复述（核只留对照指针）。cap 保持 24330。当次实测 23448，
# 刻意留出 882 字符工作余量。余量用完时再腾核，不要抬顶。
_RESIDENT_CAP = 24330

# (门工具, 该手册的签名字面) —— 手册只在门开的回合出现，不许常驻。
_GATED_MANUALS: tuple[tuple[str, str], ...] = (
    ("terminal", "wait_for"),
    ("host", "通识长文当交付"),
    ("browser_navigate", "ask_user(browser_login=true)"),
    ("external_mount_readonly", "【授权后发现】"),
    ("external_mount_readonly", "先写工作区"),
)


def _ceo_resident_chars() -> int:
    """CEO 实际付账的稳定前缀：共享基座 + 常驻核（不含按需目录 / 挂门手册 / 易变尾）。

    2026-08-19 起这句才名副其实：``workspace_facts`` 已从基座（原 order 250、核前）
    挪到 order 750（核后、紧邻易变尾），所以 ``assemble_system_prompt()`` + 核不再把
    每回合变的环境事实算进这段前缀。量的仍是无 facts 的基座——与生产付账前缀对齐。
    """
    return len(assemble_system_prompt()) + len(_CEO_CORE_HINT)


def test_resident_core_chars_within_cap():
    chars = _ceo_resident_chars()
    assert chars <= _RESIDENT_CAP, (
        f"CEO 常驻总长（基座+核）{chars} 字符 > 上限 {_RESIDENT_CAP}；"
        "先看是不是该搬去事实行 / 挂门 / 进 skill"
    )


def test_core_states_no_tool_assembly_claims():
    """案 0a71 回归守卫：装配态只能由 `<workspace_context>` 算，核里不许用散文断言。"""
    hint = _CEO_CORE_HINT
    assert "无条件装配" not in hint
    # 专用导出器的名字出现在核里，几乎总是为了断言「它一定在」——装没装配看产物格式行。
    for exporter in ("md_to_docx", "md_to_pdf"):
        assert exporter not in hint, f"{exporter} 的装配态归 `产物格式：` 行，核不点名"
    # 后缀枚举同理：能产什么由注册表 + 本回合闸算出来，核只教怎么读那行。
    for suffix in ("pptx", "xlsx", "docx"):
        assert suffix not in hint.lower(), f"核不枚举 .{suffix}；对照 `产物格式：` 行"
    assert "产物格式" in hint  # 但必须教模型去读那行


def test_core_does_not_restate_computed_workspace_facts():
    """已在事实行算出来的事实，核里不留第二份（第三份就是漂移的开始）。"""
    hint = _CEO_CORE_HINT
    # 「无原生生图工具」两条 egress 分支都已陈述；核只留「对照出站网络行」+ 出图路由。
    assert "无原生生图工具" not in hint
    assert "出站网络" in hint


@pytest.mark.parametrize(("gate_tool", "signature"), _GATED_MANUALS)
def test_gated_manuals_do_not_ride_the_resident_core(gate_tool: str, signature: str):
    """可履约手册跟装配门走：门关时不出现，门开时才挂上。"""
    assert signature not in _CEO_CORE_HINT, (
        f"{signature} 属于 {gate_tool} 的手册，不该常驻——挂到 capability_how_suffix 上"
    )
    assert signature in assemble_ceo_core({gate_tool}), (
        f"{gate_tool} 已装配却没挂上手册（{signature} 丢了）"
    )


def test_honesty_floors_stay_resident():
    """诚实底线不跟门走——它恰在能力缺失那一回合才生效，按可用性下线就是删错。"""
    hint = _CEO_CORE_HINT
    # 装包/验绿：未装配时才需要这几条。
    assert "跑绿" in hint or "单测已绿" in hint
    assert "全绿" in hint
    # 格式：标不可产时才需要。
    assert "不可产" in hint and "等效替代" in hint
    assert "已落盘可直接使用" in hint
    # 区外授权：通道不在时才需要（手册本体已挂门，底线留下）。
    assert "host=未装配" in hint
    assert "勿挂载" in hint and "勿发卡" in hint
