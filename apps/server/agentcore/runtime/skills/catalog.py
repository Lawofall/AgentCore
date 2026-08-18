"""Thin registry assembly + 按需目录 rendering for system skills."""

from __future__ import annotations

from collections.abc import Collection

from agentcore.runtime.skills.ask_user import _ASK_USER_KICKOFF, _ASK_USER_MIDTASK
from agentcore.runtime.skills.build import _BUILD_APP, _BUILD_WEBSITE
from agentcore.runtime.skills.data_file_landing import _DATA_FILE_LANDING
from agentcore.runtime.skills.debate_and_review import _DEBATE_AND_REVIEW
from agentcore.runtime.skills.deep_multi_lens_research import (
    _DEEP_MULTI_LENS_RESEARCH,
    _MULTI_LENS_COURTROOM_TRIGGERS_JOINED,
)
from agentcore.runtime.skills.delegate_checkpoint import _DELEGATE_CHECKPOINT
from agentcore.runtime.skills.long_form_writing import (
    _LONG_FORM_LANDING,
    _LONG_FORM_WRITING,
)
from agentcore.runtime.skills.product_help import (
    _PRODUCT_BUG_TRIAGE,
    _PRODUCT_HELP,
    _PRODUCT_HELP_FAQ,
    _PRODUCT_HELP_MAP,
)
from agentcore.runtime.skills.registry import (
    AUDIENCE_CEO_ONLY,
    AUDIENCE_WORKER_ONLY,
    SkillRegistry,
    SystemSkill,
)
from agentcore.runtime.skills.revising_a_product import _REVISING_A_PRODUCT
from agentcore.runtime.skills.team_orchestration import (
    _TEAM_ORCHESTRATION_ADVANCED,
)
from agentcore.runtime.skills.verify_and_fix import _VERIFY_AND_FIX
from agentcore.runtime.skills.work_discipline import _WORK_DISCIPLINE

# --- The system skills (single source of truth) -----------------------------
# Catalog summaries (the always-on one-line triggers) per the design (§4.4): sharp
# enough that the model knows WHEN to pull each, without spending the body on it.
_SYSTEM_SKILLS: tuple[SystemSkill, ...] = (
    SystemSkill(
        name="team_orchestration_advanced",
        summary=(
            "形状词汇组队 / 跨文件夹（读写通吃同次 delegate+target_folder_id；"
            "CEO list_folder_dir·read_folder_file 仅派前认桌；裸聊写盘缺桌自动建云文件夹勿催 create；"
            "空壳先问；显式多线先建齐再派；拒后禁塌缩窄例外；≠open/bind/mount 冒充）/ "
            "多 worker 流水线 / 契约 / 嵌套委派(depth≤3) / 摸底波与专班自判 / 协调墙的进阶用法"
        ),
        body=_TEAM_ORCHESTRATION_ADVANCED,
        # 派单 / 协调 / 跨路复核：队员开场目录与 consult 共用此滤，避免叶子与嵌套 lead 两套前缀。
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="work_discipline",
        summary=(
            "设计三问 / 补丁绊线 / 探索信任 / 讨论与查证分相 / 沉淀与按职责拆文件"
            "（常驻权威红线见共享基座，本 skill 为进阶 HOW）"
        ),
        body=_WORK_DISCIPLINE,
    ),
    SystemSkill(
        name="product_help",
        summary=(
            "用户问本产品怎么用 / 入口在哪 / UI·功能介绍 / 这是什么项目 / 你是什么 / "
            "产品面 FAQ / 官网 / 下载"
            "（为何没组团、费用、Key、.md/文件面板怎么打开、"
            "Cursor 规则 / `.mdc` / 改成 AgentCore 规则…）→ 先查本 skill 再短答；"
            "入口点名再查 product_help_map，FAQ 再查 product_help_faq"
        ),
        body=_PRODUCT_HELP,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_help_map",
        summary=(
            "用户点名某入口 / UI /「××在哪」（含文件面板 / .md 阅读预览 vs HTML 完整预览）"
            "→ 入口地图短答；桌面可附手册深链，手机只短答勿承诺深链"
        ),
        body=_PRODUCT_HELP_MAP,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_help_faq",
        summary=(
            "产品面 FAQ（组团 / 费用 / Key / 官网·下载 / 断网 / .md·文件面板怎么打开 / "
            "Cursor 规则↔AgentCore 用户规则…）"
            "→ 自含短答；桌面可附对应手册节"
        ),
        body=_PRODUCT_HELP_FAQ,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="product_bug_triage",
        summary=(
            "用户主动查/报产品本身可证伪故障（UI/运行时/工具/编排，像不像产品 Bug）"
            "→ 四类结论 + 复现要点；我方自检/运行时报错不预设归用户环境；"
            "FAQ 自助仍走 product_help*；禁 L4/跨用户/假装读服务端日志"
        ),
        body=_PRODUCT_BUG_TRIAGE,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="build_website",
        summary=(
            "建站/落地页：糊问形态+桌上档；规格已齐→playbook=build_website + topic + "
            "intensity(solo|standard)；控制台 dense 加 style=toolshed"
        ),
        body=_BUILD_WEBSITE,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="build_app",
        summary=(
            "绿场 SPA【推荐】build_app（手写/none 不硬拒）：交付档→intensity(lean|full)；"
            "MVP→lean；模块流水线→full+modules；边界未钉禁首派满编"
        ),
        body=_BUILD_APP,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="debate_and_review",
        summary=(
            "对抗性多视角思考用 debate（决策/压力测试/争议光谱）；"
            "点名开辩→本 skill；调研意图→deep_multi_lens_research（入口分流见 body）"
        ),
        body=_DEBATE_AND_REVIEW,
        requires_tools=("debate",),
    ),
    SystemSkill(
        name="revising_a_product",
        summary=(
            "带现场续派：唤回原作者改稿/接强相关新任务；"
            "调查批确认修默认乙（换 title≠换职能；禁再套 repair_code 冷开）"
        ),
        body=_REVISING_A_PRODUCT,
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="ask_user_kickoff",
        summary=(
            "通用短澄清：桌上档 label→intensity/playbook；糊建站问形态+档；"
            "点名载体/手段顾问短对齐；选项勿写编制；禁意图分类器"
        ),
        body=_ASK_USER_KICKOFF,
        requires_tools=("ask_user",),
    ),
    SystemSkill(
        name="ask_user_midtask",
        summary=(
            "执行途中遇到高代价岔路用 ask_user 暂停拍板；含「何时不打断（合理默认 + 标注一句）」、"
            "非阻塞发问 blocking=false、途中载体/手段顾问短对齐、辩论收尾交用户取舍"
        ),
        body=_ASK_USER_MIDTASK,
        requires_tools=("ask_user",),
    ),
    # checkpoint_after is a delegate-DAG mechanism, but it pauses for USER review —
    # only meaningful with a live user. Gate it on ``ask_user`` (the live-user proxy,
    # same as the other ask_* skills) so it never advertises on the autonomous path,
    # exactly as it did when it rode the merged asking_the_user skill.
    SystemSkill(
        name="delegate_checkpoint",
        summary=(
            "委派多步流水线时给高危中间步设 checkpoint_after，在波边界暂停让用户把关"
            "「继续 / 调整 / 取消」"
        ),
        body=_DELEGATE_CHECKPOINT,
        requires_tools=("ask_user",),
    ),
    SystemSkill(
        name="verify_and_fix",
        summary="完成代码改动后验证并修复测试失败（test_run → 读上下文 → 修代码 → 重试）",
        body=_VERIFY_AND_FIX,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``delegate`` (would hide it from workers) or ``test_run``
        # (CEO has no test_run → skill would vanish from the supervisor catalog).
    ),
    SystemSkill(
        name="long_form_writing",
        summary=(
            "超长单文档成篇 / 多源合并：主路径一次完整 file_write；可选骨架填空；成篇后 str_replace；"
            "骨架禁审校清理连环、禁 CEO 自写、勿极低 max_rounds；"
            "单写手超长跨 delegate 分波；成篇未写完用 continue_from；MD 禁 write_section；"
            "可并行拆章但验收须单主文件+合并责任"
        ),
        body=_LONG_FORM_WRITING,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
    SystemSkill(
        name="long_form_landing",
        summary=(
            "超长单文档落盘：主路径一次完整 file_write；可选骨架填空；成篇后 str_replace；"
            "MD 禁 write_section；manifest 即验真，勿回读自产物"
        ),
        body=_LONG_FORM_LANDING,
        audience=AUDIENCE_WORKER_ONLY,
    ),
    SystemSkill(
        name="data_file_landing",
        summary=(
            "账单/报表/导出记录/凭证：用户丢数据文件+一句话要可打开产物"
            "（整理成 excel/表、分栏、汇总）→ 有执行：脚本变换+不变量校验后交文件；"
            "表质量基线（任意表）：明细与汇总分表、单元格真类型、冻结+筛选、明细带合计、口径写进表内；"
            "无执行：form=files 交结构报告+待跑脚本（完整交付），"
            "下一步只说运算环境暂时不可用稍后再试；禁手抄、禁谎称已校验"
        ),
        body=_DATA_FILE_LANDING,
        # Consult is CEO+worker. Body is the worker loop; CEO still consults to brief.
        # Do not gate on ``code_execute`` (CEO has none → skill would vanish from
        # the supervisor catalog).
    ),
    SystemSkill(
        name="deep_multi_lens_research",
        summary=(
            "多维公共事件调研/研究：平行取证→命题卡→批准再辩；"
            f"点名开辩（含{_MULTI_LENS_COURTROOM_TRIGGERS_JOINED}）→debate_and_review，勿抢拦；"
            "细则与主张须证教法见 body"
        ),
        body=_DEEP_MULTI_LENS_RESEARCH,
        requires_tools=("delegate",),
        audience=AUDIENCE_CEO_ONLY,
    ),
)


def build_system_skill_registry(
    *,
    enabled_packs: Collection[str] = (),
    include_legal: bool = False,
) -> SkillRegistry:
    """Register the platform's built-in (system) skills — the single source of truth.

    Mirrors ``build_builtin_registry`` for tools: code-defined, always available to
    the CEO via ``consult``. Future market skills register into the SAME
    registry shape (单一机制、多类来源).

    ``enabled_packs`` layers deployment-gated capability packs (e.g. ``\"legal\"``)
    into the SAME registry. Call sites pass
    :func:`agentcore.runtime.capability_packs.enabled_packs` (listing gate = activation).
    ``include_legal=True`` remains a test/convenience alias for ``enabled_packs``
    containing ``\"legal\"``. Deferred import keeps the module graph free of a
    core→domain edge when no vertical pack is enabled.
    """
    packs = set(enabled_packs)
    if include_legal:
        packs.add("legal")
    registry = SkillRegistry()
    for skill in _SYSTEM_SKILLS:
        registry.register(skill)
    if packs:
        from agentcore.runtime.capability_packs import pack_skills

        for skill in pack_skills(sorted(packs)):
            registry.register(skill)
    return registry


def render_skill_directory(registry: SkillRegistry, tool_names: set[str]) -> str:
    """Backward-compat wrapper → unified ``<按需目录>`` (skills only).

    Prefer building entries via :class:`MergedConsultSource` so directory and
    ``consult`` fetch cannot drift. Kept for tests / capability catalog that only
    need the skill slice.
    """
    from agentcore.runtime.context.consultable import ConsultDirectoryEntry
    from agentcore.runtime.resolve.prompt.compose import render_on_demand_directory

    skills = registry.available(tool_names)
    if not skills:
        return ""
    entries = [
        ConsultDirectoryEntry(name=skill.name, summary=skill.summary) for skill in skills
    ]
    return render_on_demand_directory(entries, with_summaries=True)
