"""常驻核体积棘轮 + 权威归属——CEO 提示词只许瘦、不许悄悄回潮。

## 为什么有这道棘轮

工具 schema 早就有下行棘轮（``test_tool_schema_size_ratchet.py``），常驻核一直没有，而
``test_prompt.py`` 里七十多条断言几乎全是「某条禁令的字面必须出现在 ``_CEO_CORE_HINT`` 里」，
半数带着事故编号。于是激励是单向的：**加一条禁令零成本还有奖励**（测试证明事故修了），
**删掉或通用化一条要付红灯**、在 review 里看起来像退步。核就是这么长到两万字符的。

比体积更要紧的是**权威归属**：同一件事只能有一处权威。

- 环境能不能做某事 = **算出来的事实**，住 ``<工作区>``（``本回合执行能力`` /
  ``产物格式`` / ``出站网络`` …）。核里复述一份就会漂——案 0a71 就是核里
  散文断言「``md_to_docx`` / ``md_to_pdf`` 无条件装配」，而 CEO 并不持这两把工具、在自己的
  工具列表里看不见它们，模型于是花了整段思考链猜「队员到底有没有」，最后把用户的三个选项
  连同提问一起丢了。**下面那条 assembly-claim 测试就是这个 bug 的回归守卫。**
- 可履约的操作手册 = **consult 正文**（``capability_how_suffix`` 只给 consult 拼，不挂冻结核）。
  通道不在的回合，手册是一份证明履行不了的说明书。
- **诚实底线双向且常驻**：未装配不许假装用过（缺失那一回合才用到）；已装配不许假装没有
  （按需工具未进开场表的那一回合才用到）。都不许按可用性下线。
  「禁止把仅结构自检说成跑绿」「禁止称不可产的工具已装配」这类留在核里是对的。

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
    assemble_system_prompt,
    capability_how_suffix,
    compose_ceo_chat_prompt,
)

# 2026-08-19 跨界搬迁：交付验收对照 / 可用性短问 / 概览契约从共享基座迁入核
# （队员开场用不到；finish_guard 仅 CEO 路径查）。核 +319、基座 −467，
# 基座+核 24472 → 24324（净 −148）。核单列不再代表真实成本，改量 CEO 常驻总长。
# 当次实测 24324。
#
# 2026-08-20 降水位（不是抬顶）：五项迁出常驻核——本轮材料收窄+附件缺件改场面门、
# 删冷启动探索幕核内副本、产品面地图→product_help_map、成品文件只装成品→long_form_writing、
# 删执行事实行复述（核只留对照指针）。cap 保持 24330。当次实测 23448，
# 2026-08-25 本波：开工卡退役文案 / 工具失败脸 / rebuild 提示进入常驻前缀。
# 当次实测 24465。cap 提到 24470（向上取整到十位）。
# 2026-08-26 ask_user 核/Skill 去重第一刀：常驻百科压成开火短卡，HOW 真源进
# asking_the_user。当次实测 23556。cap 降到 23560。
# 2026-08-26 第二刀：结局分层/拆人手册下沉 team_orchestration_advanced。
# 当次实测 22439。cap 降到 22440。
# 2026-08-26 第三刀：立刻派≠全量 / 绿场切片 HOW 下沉编排·build_app，核留开火短卡。
# 当次实测 21972（薄旁路开火留核、桌上档指针收口后）。cap 降到 21980。
# 2026-08-26 第四刀：收口落地百科（空桌/派单/产物路径/面板/交付指引）压成开火短卡，
# HOW 真源在 team_orchestration_advanced / product_help_map。当次实测 21169。cap 降到 21170。
# 2026-08-26 第五刀：执行/运行/打开 HOW 下沉挂门手册与编排 skill，核留开火短卡。
# 当次实测 20714。cap 降到 20720。
# 2026-08-26 第六刀：目标格式/不可产 HOW 下沉编排 skill 与 data_file_landing，核留开火短卡。
# 当次实测 20181。cap 降到 20190。
# 2026-08-26 第七刀：已确认约束/派工写法 HOW 下沉编排 skill，核留开火短卡。
# 当次实测 19731。cap 降到 19740。
# 2026-08-26 编制自选进核：取消「讨论/盘点默认两路 brief」，人数由 CEO 按缝选。
# 当次实测 19859。cap 提到 19860。
# 2026-08-26 问面广度开火：讨论/盘点/架构不再并列自己聊；对话本身=不发卡不写盘+队员结论开口。
# 当次实测 19855。cap 保持 19860。
# 2026-08-27 第 3 步：Windows .bat 出共享基座，HOW 进 run。当次实测 19655。
# cap 降到 19660。
# 2026-08-27 第 7 步：CEO 核判例→原则；ask_user 百科去双写；事故话术出核。
# 当次实测 18859。cap 降到 18860。
# 同步补回点名载体短钩触发句（过压后脊柱不全：盖不住/次优 → 先短问）。当次实测 18912。
# cap 18920。
# 2026-08-27 第二轮 1–3 步：核从场面汇编收到宪法；场面 HOW 一句 consult 钩；
# 诚实禁语表收成一条元规则。当次实测 15019。cap 降到 15020。
# 短改稿开工模板出核（HOW 只在 asking_the_user）。当次实测 14911。cap 降到 14920。
# 2026-08-27 第三轮：编制自选吞拆几个人；已确认约束/明示确认收成钩；
# 勿推销并进主张对照。当次实测 14752。cap 降到 14760。
# 明示确认钩补回「落盘前须 ask_user」（常见路勿先 consult）。当次实测 14791。cap 14800。
# 2026-08-27 第四轮：核按场面钩改成三本名字（跨文件夹 / 交付边界）。当次实测 14828。cap 14840。
# 2026-08-27 统一路由脊柱：how_you_work 收成 CEO 自判一棵树（讨论自己做、改产物必须派）。
# 当次实测 13744。cap 降到 13750。
# 2026-08-27 第 20 步：⑥ 后尾巴收钩（判例出核、与基座/skill 重复 HOW 删核内副本）。
# 当次实测 13484。cap 降到 13490。
# 2026-08-27 第 21 步：① 短问收脊柱（跨产品出 how_you_work；穿插/细则出核）。
# 当次实测 13353。cap 降到 13360。
# 2026-08-27 第 22 步：④ 绿场/假两段收 consult 钩。当次实测 13255。cap 降到 13260。
# 2026-08-27 第 23 步：⑤ 结局分层收钩。当次实测 13132。cap 降到 13140。
# 2026-08-27 第 24 步：how_you_act 第二棵树（甲–戊）；两分路由去撞号。当次实测 13095。
# cap 降到 13100。
# 2026-08-27 第 25 步：③④ 按问句拆（要不要 / 拉几人）；必须与应该分写不合并。
# 当次实测 13094。cap 保持 13100。
# 2026-08-27 第 26 步：核内自抄清掉（③/戊免查表、甲禁派空跑、两分路由并进②、
# ⑥后 schema/目录钩出核、①收脊柱、default 归 schema、delegate 用/不用对齐）。
# 当次实测 11775。cap 降到 11780。
# 2026-08-27 第 27 步：拆 <platform_knowledge>；品类+官网并进 <role>；产品强度串出核。
# 当次实测 11064。cap 降到 11070。
# 2026-08-27 第 28 步：甲乙能力手册出核（三分日志/启服报 URL/右坞 HOW → consult）。
# 当次实测 10729。cap 降到 10730。
# 2026-08-27 第 29 步：④ 编排强度串出核；场面 WHEN 归目录摘要。
# 当次实测 10577。cap 降到 10580。
# 2026-08-27 第 30 步：⑥ 后钩子收脊柱（主拍板/审查同字面/未定案·窄出核）。
# 当次实测 10352。cap 降到 10360。
# 2026-08-27 第 31 步：丙继续项目出核；误读地板并进基座 work_authority。
# 当次实测 10287。cap 降到 10290。
# 2026-08-27 现行信息清废名：核/基座去掉已死对照（两路 brief、format_options、
# M0、用户硬/AI软、辩词式 #eN、暂靠提醒、引擎不剥、日历门槛、甲–戊不同套等）。
# 当次实测 10107。cap 降到 10110。
# 2026-08-27 playbook 更名进核 ⑤（research_report→cite_write_review、
# parallel_brief→map_fanout）。场面 WHEN 下沉目录 / consult。当次实测 9560。cap 降到 9560。
# 2026-08-27 第三刀：身份/问方法收成原则（查询词出核）。当次实测 9482。cap 降到 9490。
# 2026-08-27 落盘前对齐/点名载体去查询词；当场缺口尾收一句姿势。当次实测 9412。cap 降到 9420。
# 2026-08-27 讨论开场去共创/审美举例。当次实测 9369。cap 降到 9370。
# 2026-08-27 A：戊对照变体表收成底线；点名载体防吞并进①。当次实测 9196。cap 降到 9200。
# 2026-08-27 窄 B：①开口 / ④编制 / ⑤成文 同义复读。当次实测 9155。cap 降到 9160。
# 2026-08-28 凭据卫生：用户终端作业单改为当次 env；基座净瘦。当次实测 8995。cap 降到 9000。
# 2026-08-28 全员基座按现行信息/一层一所有者收束：路径主张唯一所有者 claim_evidence、
# 能力姿势去 ①–⑤ 撞号、tool_use 去掉与 schema/当场重复的 HOW。当次实测 8756。cap 降到 8760。
# 2026-08-28 丁「派完」并进团队状态（人已派出唯一所有者 how_you_act）。当次实测 8726。cap 降到 8730。
# 2026-08-28 常驻同文抄录：核删填参/能力枚举/跨文件夹 WHEN/「默认 A」；基座检索收短钩；
# 场面门收姿势；rules 冲突句归 work_authority。当次实测 8367。cap 降到 8370。
# 2026-08-28 核 ② 删与基座 <capability_honesty> 同文的「否决论文 / 产品 FAQ」半句。
# 当次实测 8345。cap 降到 8350。
# 2026-08-28 先取证再开口进核（新路由脊柱）：删讨论免探场面账
# （讨论不必查 / 讨论读文档不是摸底 / 本产品机制禁止摸底开组）；
# FAQ vs 工作区课题分写。当次实测 8450。cap 提到 8450（抬顶，非回潮）。
# 2026-08-29 禁止句补集瘦身：核删正向反面复述（发卡/包办/打折/编制话术）；
# 甲留挂载底线。当次实测 8330。cap 降到 8330。
# 2026-08-29 补集第二刀：核折路由推演/凑确认/工种凑人；编排去重作者口吻。
# 当次实测 8264。cap 降到 8270。
# 2026-08-29 对话引用改检索产物：基座 delivery_baseline 删 search-only 禁令 / 引擎因引用回炉。
# 当次实测 8182。cap 降到 8190。
# 2026-08-29 路由脊柱极性：应答闭集 / 干活默认拉人（删 Cursor 主程序员默认）。
# 当次实测 8190。cap 保持 8190。
# 2026-08-29 C：常驻核从编号判决树收到身份 + 工具边界 + 对照结构面。
# 何时用工具写在 description。当次实测 4503。cap 降到 4510。
# 2026-08-29 基座 C：删 problem_solving；检索收敛下沉 web_search description；
# delivery_baseline+claim_evidence 并成 delivery_honesty；能力诚实收成双条件。
# 当次实测 2820。cap 降到 2820。
# 2026-08-29 删常驻 <visualization>；基座文风句点名 mermaid（与 LaTeX 同句）。
# 可视化段本不进本棘轮；基座文风句 +9 码点。当次实测 2829。cap 2830。
# 2026-08-29 基座 C 补集：文风质量启发式 / 系统反馈补集 / 注入近义词表 / consult 时序出基座；
# role 删不必先查阅。当次实测 2541。cap 降到 2550。
# 2026-08-29 装配侧收短：凭据卫生短 A；核删挡路才问；consult HOW 压成原则+A≠B。
# 当次实测 2306。cap 降到 2310。
# 2026-08-29 核删「有稳妥默认就标假设继续」（何时问归 ask_user description）。
# 当次实测 2294。cap 降到 2300。
# 2026-08-29 核 how_you_work 只留身份边界；WHEN 归 delegate / consult description。
# 当次实测 2219。cap 降到 2220。
# 2026-08-29 官网 URL 出 <role>，走 product_help。当次实测 2149。cap 降到 2150。
# 2026-08-30 核出两条补集禁止（空白 / 未装配写进任务）；切口在 delegate.task。
# 当次实测 1824。cap 降到 1830。
# 2026-08-30 持械边界并进 <身份>，删独立 <怎么干活> 标签。当次实测 1807。cap 降到 1810。
# 2026-08-30 全员基座并段：开场收口、输入合并、诚实并标签、凭据进工作权威。
# 当次实测 1635。cap 降到 1640。
# 2026-08-30 「能查就别猜」并进 <诚实> 首句（用过之后不编造覆盖不了用之前凭空答）。
# 当次实测 1652。cap 提到 1660（抬顶，非回潮）。
# 2026-08-30 五时刻脊柱：身份并进角色层、拆 <怎么对人>、文风改 <输出>。
# 当次实测 1517。cap 降到 1520。
# 2026-08-30 工种叠层：CEO 专属（大白话 / 用户可见面）从基座搬回 <身份>；
# 扩范围按角色分叉。当次实测 1569。cap 1570（抬顶=身份语义归位，非回潮）。
# 2026-08-30 基座权威去通道分叉（问谁归工具 description）。当次实测 1498。cap 1500。
# 2026-08-30 CEO 身份：全套工具、装得下自己做。当次实测 1503。cap 1510
# （抬顶=身份语义，非回潮；不靠核内塞字躲 3 字符）。
# 2026-08-30 「全套工具」出核（装配态归 <工作区>）。当次实测 1492。cap 1500
# 2026-08-30 档 1 身份：超规模默认交团队、协调收口；装得下≠看起来简单/手上有工具。
# 当次实测 1539。cap 1540（抬顶=新身份语义，非回潮）。
# 2026-08-30 核去「超规模」门槛，默认交团队；「动手前先判规模」留下。当次实测 1536。cap 1540。
# 2026-08-31 规模句：自己做只限一问就能收口；「一份注意力 / 装得下」出核。
# 当次实测 1527。cap 1530。
# 2026-08-31 「手上有工具 ≠ 该自己做完」出核（正向已盖住；工具面仍有「有写权 ≠」）。
# 当次实测 1513。cap 1520。
# 2026-08-31 规模句改为短答和单点 ≠ 成件事。当次实测 1518。cap 1520。
# 2026-09-01 身份问路由句出核；基座删只读审计句；run HOW 出 capability_how_suffix。
# 当次实测 1443。cap 降到 1450。
_RESIDENT_CAP = 1450

# (门工具, 该手册的签名字面) —— 手册只在门开的回合出现，不许常驻。
# run 的 HOW 在 skill body（consult(run) 命中 skill），不进 capability_how_suffix。
_GATED_MANUALS: tuple[tuple[str, str], ...] = (
    ("run", "wait_for"),
    ("host", "通识 FAQ"),
    ("host", "Get-WinEvent"),
    ("browser", "ask_user(browser_login=true)"),
    ("browser", "永不代填密码"),
    ("external_mount_readonly", "先写工作区"),
    ("external_mount_readonly", "只读已挂"),
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
    """案 0a71 回归守卫：装配态只能由 `<工作区>` 算，核里不许用散文断言。"""
    hint = _CEO_CORE_HINT
    assert "无条件装配" not in hint
    # 专用导出器的名字出现在核里，几乎总是为了断言「它一定在」——装没装配看产物格式行。
    for exporter in ("md_to_docx", "md_to_pdf"):
        assert exporter not in hint, f"{exporter} 的装配态归 `产物格式：` 行，核不点名"
    # 后缀枚举同理：能产什么由注册表 + 本回合闸算出来，核只教怎么读那行。
    for suffix in ("pptx", "xlsx", "docx"):
        assert suffix not in hint.lower(), f"核不枚举 .{suffix}；对照 `产物格式：` 行"
    assert "产物格式" in hint
    assert "产物格式" not in assemble_system_prompt()


def test_core_does_not_restate_computed_workspace_facts():
    """已在事实行算出来的事实，核里不留第二份（第三份就是漂移的开始）。"""
    hint = _CEO_CORE_HINT
    # 「无原生生图工具」两条 egress 分支都已陈述；核只留「对照出站网络行」+ 出图路由。
    assert "无原生生图工具" not in hint
    assert "出站网络" in hint
    assert "出站网络" not in assemble_system_prompt()


@pytest.mark.parametrize(("gate_tool", "signature"), _GATED_MANUALS)
def test_gated_manuals_do_not_ride_the_resident_core(gate_tool: str, signature: str):
    """可履约手册唯一所有者 = consult / skill：核与 compose 开场都不挂。"""
    assert signature not in _CEO_CORE_HINT, (
        f"{signature} 属于 {gate_tool} 的手册，不该常驻——交给 consult"
    )
    if gate_tool == "run":
        from agentcore.runtime.skills.run import _RUN

        assert signature in _RUN, (
            f"run 的手册丢了签名 {signature}（应在 skill body）"
        )
        assert signature not in capability_how_suffix({"run"}), (
            "run HOW 不进 capability_how_suffix"
        )
    else:
        assert signature in capability_how_suffix({gate_tool}), (
            f"{gate_tool} 的 consult 手册丢了签名 {signature}"
        )
    catalog_like = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        ceo_tool_names={"delegate", gate_tool},
    )
    offered = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        ceo_tool_names={"delegate", gate_tool},
        ceo_offered_names={"delegate", gate_tool},
    )
    assert signature not in catalog_like, (
        f"{signature} 不应因图鉴漏传 offered 挂回核（{gate_tool}）"
    )
    assert signature not in offered, (
        f"{signature} 不应在工具已进表时再挂进冻结核（{gate_tool}）"
    )


def test_capability_how_has_no_ceo_must_delegate_leftovers():
    """Tool-lock leftovers left consult HOW; do not reintroduce as 禁止自己跑/截图."""
    how = capability_how_suffix({"run", "host", "browser"})
    for token in (
        "验收与短命令由队员",
        "打开系统面板 / 切默认音频 / 重启白名单服务 / 装本机软件 → `delegate`",
        "验收 / 截图 → `delegate`",
        "队员 `screenshot`",
        "禁止自己跑",
        "禁止自己截图",
    ):
        assert token not in how
        assert token not in _CEO_CORE_HINT


def test_honesty_floors_stay_resident():
    """诚实底线不跟门走：核留对照结构面元规则；装包/格式细则在 skill，已装配反向在基座。"""
    from agentcore.runtime.skills.building_software import _BUILDING_SOFTWARE
    from agentcore.runtime.skills.team_delivery_env import _TEAM_DELIVERY_ENV

    hint = _CEO_CORE_HINT
    base = assemble_system_prompt()
    assert "对照本回合结构面" in base
    assert "未对照则不得声称" in base
    assert "未对照则不得声称" not in hint
    assert "已落盘" not in base
    assert "已落盘" in hint
    assert "结构自检" in _BUILDING_SOFTWARE
    assert "export_to_local" in _BUILDING_SOFTWARE
    assert "不可产" in _TEAM_DELIVERY_ENV and "等效替代" in _TEAM_DELIVERY_ENV
    assert "已装配" in base and "通道在" in base
    assert "邻格" in base
