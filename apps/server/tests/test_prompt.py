"""Tests for system-prompt assembly (`assemble_system_prompt`) and the slim CEO core.

Pins two things:

1. The shared <output_style> contract that keeps the whole team's voice professional
   and anti-"AI slop" — it lives in the base prompt, so it must reach both the CEO
   chat agent and every delegated worker, and survive the optional memory /
   attachment-context sections layered on top. (<tool_safety> used to be shared here
   too, but 按角色 right-size 反向 moved it onto the worker identities — the coordinator
   CEO holds only read-only tools — so this file pins its ABSENCE from the base/CEO
   path; the worker-side presence is pinned in tests/runs_executor/test_identities.py.)
2. The CEO core is identity + honesty + consult hook — not a numbered
   routing classifier. HOW lives in system Skills / tool descriptions
   (runtime/skills/, see test_skills.py) and must NOT ride the core every turn.

断言政策（上下文工程 · 提示词设计原则）：禁止再加「某句事故禁语必须出现在
``_CEO_CORE_HINT``」。新回归用行为测试（``test_closing_posture`` / conformance / eval）
或 skill 缺席。本文件守卫分层 / 装配 / 缺席 / 唯一性与原则标题；不守卫某次事故的禁语字面。
"""

import re

from agentcore.runtime.resolve.prompt import (
    _ATTACHMENT_MATERIAL_HINT,
    _CEO_CORE_HINT,
    _DEFAULT_SYSTEM_PROMPT,
    assemble_ceo_core,
    assemble_system_prompt,
    attachment_material_scene,
    capability_how_suffix,
    compose_ceo_chat_prompt,
    compose_worker_base_prompt,
    derive_ceo_addon,
)
from agentcore.runtime.skills import (
    _TEAM_CROSS_FOLDER,
    _TEAM_DELIVERY_ENV,
    _TEAM_ORCHESTRATION_ADVANCED,
    build_system_skill_registry,
)

_LOCAL_HOW = capability_how_suffix({"terminal", "host", "browser"})


def test_derive_ceo_addon_splits_shared_prefix_from_full_ceo_prompt():
    base = assemble_system_prompt()
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult", "ask_user"},
    )
    addon = derive_ceo_addon(base, ceo)
    assert addon
    assert "<role>" in addon
    assert "<output_style>" not in addon
    assert ceo.startswith(base)
    assert addon == ceo[len(base) :].lstrip("\n")
    assert ceo == base + ceo[len(base) :]


def test_output_style_block_present_in_base():
    out = assemble_system_prompt()
    assert "<output_style>" in out
    assert "</output_style>" in out


def test_emoji_banned_with_soft_carve_out():
    out = assemble_system_prompt()
    assert "emoji" in out
    # Default-off, but allowed when the user uses one first or explicitly asks.
    assert "除非用户" in out
    assert "明确要求" in out


def test_anti_filler_and_formatting_restraint():
    out = assemble_system_prompt()
    style = out.split("<output_style>", 1)[1].split("</output_style>", 1)[0]
    assert "直接给结论" in style
    assert "不奉承" in style
    assert "不套话" in style
    # Quality heuristics / 补集 of the emoji ban do not ride the resident style.
    assert "滥用列表" not in out
    assert "表情符号" not in out


def test_render_capabilities_advertised():
    out = assemble_system_prompt()
    assert "Markdown" in out
    assert "LaTeX" in out


def test_untrusted_content_guard_frames_external_and_cross_agent_text():
    # <untrusted_content> (PI-003 + PI-006, 提示注入防御纵深) is the trust boundary the API
    # role="tool" alone doesn't enforce: external content — AND text authored by another Agent —
    # is DATA, never a command. It lives in the SHARED base so it reaches every worker AND the
    # composed CEO. Pin (1) the block + the data-not-command framing and a canonical injection
    # idiom it must resist, (2) that it names the PI-003 channels (tool/web/file/memory) AND the
    # PI-006 cross-agent channels (teammate notes / upstream product / delegated task), and
    # (3) that it survives into the composed CEO prompt — so a refactor can't silently drop the
    # guard or narrow it back to non-agent content only.
    base = assemble_system_prompt()
    assert "<untrusted_content>" in base and "</untrusted_content>" in base
    assert "【数据】" in base and "不是对你下达的指令" in base
    assert "忽略上面的指令" in base
    assert "现在改为执行" not in base
    assert "点开某链接" not in base
    for token in ("工具返回", "网页", "文件", "长期记忆"):  # PI-003 external channels
        assert token in base, f"untrusted_content lost the {token} channel"
    for token in ("上游", "委派"):  # PI-006 cross-agent channels
        assert token in base, f"untrusted_content lost the cross-agent {token} framing"
    assert "队友便签" not in base
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )
    assert "<untrusted_content>" in ceo and "上游" in ceo
    assert "队友便签" not in ceo


def test_system_feedback_block_frames_engine_steers_as_non_user():
    # 回合中引擎自动注入的 [系统提示] 以 role=user 进窗口，模型易误当用户纠错。
    # 共享 base 的 <system_feedback> 把这类注入定性为「系统自动机制、非用户发言」
    # 并要求直接修正。Pin 住块与非用户定性，不钉致谢禁令补集。
    base = assemble_system_prompt()
    assert "<system_feedback>" in base and "</system_feedback>" in base
    assert "[系统提示]" in base
    assert "不是用户" in base  # 定性：非用户发言
    assert "直接修正" in base
    feedback = base.split("<system_feedback>", 1)[1].split("</system_feedback>", 1)[0]
    assert "道谢" not in feedback
    # 复合进 CEO 提示后仍在（worker 走 bare base，天然带上）。
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )
    assert "<system_feedback>" in ceo


def test_tool_safety_moved_out_of_shared_base_and_ceo():
    # 按角色 right-size (反向): the environment-mutation caution (<tool_safety>) used to ride
    # the shared base, so the CEO carried it too — but the coordinator CEO holds only
    # read-only tools (build_ceo_tool_registry); a caution about write/delete/execute tools
    # it cannot call was inert weight. It moved onto the worker identities
    # (executor.identities._WORKER_TOOL_SAFETY_POLICY, pinned in test_identities.py). Pin its
    # ABSENCE from the base AND the composed CEO prompt so a refactor can't quietly re-inflate
    # the CEO prefix by folding it back into the shared base.
    base = assemble_system_prompt()
    assert "<tool_safety>" not in base
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )
    assert "<tool_safety>" not in ceo


def test_tool_use_block_teaches_parallel_calls():
    # The executor already runs a round's tool_calls concurrently (engine
    # _execute_tools: asyncio.gather + semaphore). The only missing lever was telling
    # the model to BATCH independent calls into one round so that concurrency is
    # actually used (otherwise the ReAct loop emits one call per round = serial). This
    # guidance lives in the shared base prompt so both the CEO and every worker batch
    # independent retrievals. Pin it so a refactor can't silently re-idle the
    # concurrent executor.
    out = assemble_system_prompt()
    assert "<tool_use>" in out
    assert "互相独立" in out
    assert "并发" in out
    assert "一次性" in out


def test_tool_use_block_does_not_restate_web_search_contract():
    # 并行批次留基座；查询上限 / 检索收敛归 web_search schema；挂号归 delivery_honesty。
    out = assemble_system_prompt()
    tool = out.split("<tool_use>", 1)[1].split("</tool_use>", 1)[0]
    honesty = out.split("<delivery_honesty>", 1)[1].split("</delivery_honesty>", 1)[0]
    assert "web_search" not in tool
    assert "截断" not in tool
    assert "2–3" not in tool and "核心词" not in tool
    assert "download_url" not in tool
    assert "无法规范化才拒绝" not in out
    assert "≤8 词" not in out
    assert "搜到 ≠ 可挂来源号" not in honesty
    assert "search-only 不可" not in honesty
    assert "已登记" in honesty
    assert "#rN" in honesty
    assert "文字概括" not in honesty
    assert "<problem_solving>" not in out
    assert "跨行业类比" not in out


def test_runtime_context_uses_date_granularity_for_cache_stability():
    # The runtime-context line sits in the system-prompt prefix BEFORE the large
    # stable hint stack, so it must NOT carry second-precision time: a value that
    # changed every turn broke DeepSeek's exact-prefix cache for everything after it
    # (the whole CEO hint stack got re-billed each turn). Pin date granularity + the
    # call-to-call stability that makes the stable core cacheable within a day, so a
    # refactor can't silently reintroduce the cache-buster.
    out = assemble_system_prompt()
    assert re.search(r"当前日期：\d{4}-\d{2}-\d{2}", out)
    assert not re.search(r"\d{2}:\d{2}:\d{2}", out)  # no HH:MM:SS timestamp
    assert assemble_system_prompt() == out  # byte-identical across calls (same day)


def test_output_style_survives_memory_and_context_layers():
    out = assemble_system_prompt(
        rules_markdown="- 用户偏好简洁回复",
        extra_context="<attached_files>...</attached_files>",
    )
    # The shared style block is not crowded out by the optional sections.
    assert "<output_style>" in out
    assert "用户偏好简洁回复" in out
    assert "<attached_files>" in out


def test_style_precedes_ceo_only_core_when_composed():
    # The CEO prompt is base + core hint (see pipeline.run_chat_pipeline). The
    # shared style must come from the base, independent of the CEO-only core.
    base = assemble_system_prompt()
    assert "<output_style>" in base
    assert "<output_style>" not in _CEO_CORE_HINT


def test_charting_affordance_is_mermaid_not_a_resident_classifier():
    # Industry: GFM + mermaid in the shared style sentence. No per-turn "when to
    # chart" block, no private compare grammar, no markmap/vega-lite dialect list.
    base = assemble_system_prompt()
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult"},
    )
    assert "mermaid" in base
    assert "<visualization>" not in base
    assert "<visualization>" not in ceo
    for token in ("markmap", "vega-lite", "```compare", "preview-*.jpg"):
        assert token not in base, f"charting HOW '{token}' leaked into the worker base"
        assert token not in ceo, f"charting HOW '{token}' leaked into the CEO prompt"


def test_core_states_coordinator_tool_boundary():
    # 协调者 CEO: 只读边界；写与超规模交给团队。HOW 在 consult，不进核。
    hint = _CEO_CORE_HINT
    assert "只读" in hint
    assert "delegate" in hint
    assert "超规模" in hint
    assert "未装配能力" in hint
    assert "consult(terminal)" not in hint
    assert "【三分日志】" not in hint
    assert "本机运行态" not in hint
    assert "本机 Host" not in hint
    host_how = capability_how_suffix({"host"})
    term_how = capability_how_suffix({"terminal"})
    assert "三分日志" in host_how
    assert "host(action=os_log)" in host_how
    assert "search_conversations" in host_how
    assert "Get-WinEvent" in host_how
    assert "报 URL" in term_how
    assert "wait_for" in term_how
    assert "长驻" in term_how and "host(action=shell)" in term_how
    assert "通识 FAQ" in host_how
    assert "盲探" in host_how
    assert "host_os_log_summary" not in hint
    assert "host_ping" not in hint
    assert "host_info" not in hint
    assert "host_shell" not in hint
    assert "Get-WinEvent" not in hint and "journalctl" not in hint


def test_capability_how_gated_on_ceo_tool_names():
    """本机/Host/浏览器 HOW 唯一所有者 = consult；冻结核与 compose 开场都不挂手册。"""
    spine = _CEO_CORE_HINT
    assert "wait_for" not in spine
    assert "ask_user(browser_login=true)" not in spine
    assert "通识 FAQ" not in spine
    assert "永不代填密码" not in spine
    assert "假开页" not in spine
    assert "三分日志" not in spine
    assert "把启服写进队员任务" not in spine
    assert "未装配能力" in spine

    term = capability_how_suffix({"terminal"})
    assert "wait_for" in term
    assert "云桌" in term
    assert "ask_user(browser_login=true)" not in term
    assert "通识 FAQ" not in term
    # 异步 HTTP 轮询短触发只在 code_execute schema，不进核 / 不进 consult(terminal) 启服手册
    assert "异步 HTTP" not in spine
    assert "异步 HTTP" not in term

    host = capability_how_suffix({"host"})
    assert "通识 FAQ" in host
    assert "host(action=status)" in host
    assert "host(action=os_log)" in host
    assert "三分日志" in host
    assert "search_conversations" in host
    assert "open_settings" in host and "install_package" in host
    assert "set_audio" in host and "restart_service" in host
    assert "Get-WinEvent" in host
    assert "delegate" in host
    assert "host_info" not in host
    assert "host_package_install" not in host
    assert "host_ping" not in host
    assert "wait_for" not in host

    browser = capability_how_suffix({"browser"})
    assert "ask_user(browser_login=true)" in browser
    assert "永不代填密码" in browser
    assert "read_url" in browser and "已开页" in browser
    assert "wait_for" not in browser

    grant = capability_how_suffix({"external_mount_readonly"})
    assert "先写工作区" in grant
    assert "只读已挂" in grant
    assert "wait_for" not in grant
    assert "通识 FAQ" not in grant
    assert "先写工作区" not in browser

    # Catalog/eval used to hang HOW by omitting offered (fallback = full registry).
    # Production opening also must not hang HOW even if the tool is already offered.
    for names, offered in (
        ({"delegate", "consult"}, None),
        ({"delegate", "terminal", "host", "browser"}, None),
        (
            {"delegate", "terminal", "host", "browser"},
            {"delegate", "terminal", "host", "browser"},
        ),
        ({"delegate", "terminal", "host", "browser"}, {"delegate"}),
    ):
        prompt = compose_ceo_chat_prompt(
            assemble_system_prompt(),
            ceo_tool_names=names,
            **({"ceo_offered_names": offered} if offered is not None else {}),
        )
        assert "wait_for" not in prompt
        assert "通识 FAQ" not in prompt
        assert "ask_user(browser_login=true)" not in prompt
        assert assemble_ceo_core(names) == spine


def test_core_teaches_split_criterion_over_count():
    # 身份核：只读边界；写与超规模交给团队。编制细则在 skill。
    hint = _CEO_CORE_HINT
    assert "只读" in hint and "delegate" in hint
    assert "超规模" in hint
    for retired in (
        "①",
        "②",
        "③",
        "④",
        "⑤",
        "map_fanout",
        "cite_write_review",
        "开子代理",
        "问面广度",
        "开局即派",
        "不派仅限",
        "单人直出",
        "轻量直出",
        "finalize",
        "拆几个人",
        "自动两路",
        "两路 brief",
        "档 2",
        "轻成文",
        "consumer_deps",
        "先多角度摸清",
        "暂不派队",
        "写成文档并保存",
    ):
        assert retired not in hint, retired
    assert "学术审校" in _TEAM_ORCHESTRATION_ADVANCED
    assert "够用即停" in _TEAM_ORCHESTRATION_ADVANCED
    assert "一页地图" in _TEAM_ORCHESTRATION_ADVANCED
    assert "一句目标" in _TEAM_ORCHESTRATION_ADVANCED
    assert "handoff" in _TEAM_ORCHESTRATION_ADVANCED
    # 短问 default 归 ask_user schema；百科唯一所有者 = ask_user_kickoff
    assert "【决策/澄清短问·default】" not in hint
    assert "凡写「短问 / `ask_user`」处一律适用" not in hint
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.ask_user.tool import AskUserTool

    ask_schema = AskUserTool(
        sink=EventSink(), conversation_id="c1", timeout_seconds=30.0
    ).schema
    assert "挡路才问" in ask_schema.description
    assert "猜错会做错" in ask_schema.description
    assert "可逆低杠杆" in ask_schema.description
    assert "标假设" in ask_schema.description
    assert "有默认则标假设" not in ask_schema.description
    q_desc = ask_schema.parameters["properties"]["questions"]["description"]
    d_desc = ask_schema.parameters["properties"]["questions"]["items"]["properties"]["default"][
        "description"
    ]
    assert "不预选" in q_desc
    assert "空 continue" in q_desc or "空 continue" in d_desc
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "【决策/澄清短问·default】" in kickoff
    assert "不预选" in kickoff and "default" in kickoff
    assert "按确认默认" in kickoff
    assert "承接" in kickoff and "缺主体" in kickoff
    assert "ask_user_kickoff" not in hint
    assert "【继续·承接确认项】" in kickoff
    assert "【短确认·只补缺口】" in kickoff
    assert "缺主体" in kickoff and "静默自拟" in kickoff
    assert "决策/澄清短问" in kickoff
    assert "先问你" not in kickoff
    assert "prior_delivery_gaps" in kickoff
    assert "整锅重派" in kickoff
    assert "默认路径" in kickoff
    assert "零摩擦" in kickoff and "（推荐）" in kickoff
    assert "放第一" in kickoff
    assert "【继续·承接确认项】" not in hint
    assert "【短确认·只补缺口】" not in hint
    assert "prior_delivery_gaps" not in hint
    assert "静默自拟" not in hint
    assert "零摩擦" not in hint
    assert "先问你" not in hint
    from agentcore.tools.builtin.delegate.schema import TASK_DELIVERABLE_SCHEMA

    form_desc = TASK_DELIVERABLE_SCHEMA["properties"]["form"]["description"]
    assert "【存文档】" in form_desc and "files" in form_desc
    help_map = build_system_skill_registry().get("product_help_map").body
    assert "主路径" in help_map
    lf_fail = build_system_skill_registry().get("long_form_writing").body
    assert "参数不是合法 JSON" in lf_fail
    assert "参数不是合法 JSON" not in hint
    assert "ask_user_kickoff" not in hint
    assert "糊建站" in kickoff or "做个网站" in kickoff
    assert "短改稿" in kickoff and "任务卡" in kickoff
    for retired in (
        "web_quality_scan",
        "visual_critic",
        "consult(build_website)",
        "format_options",
        "playbook=none",
        "playbook_id",
        "consult(building_software)",
        "做软件手写",
        "提案墙",
        "结构自检",
        "人已派出",
        "真两段",
        "假两段",
        "薄旁路",
        "continue_from_run_id",
    ):
        assert retired not in hint, retired
    assert "先设计再实现" in _TEAM_ORCHESTRATION_ADVANCED
    build_app = build_system_skill_registry().get("building_software").body
    assert "薄旁路" in build_app
    assert "单 lead" in _TEAM_ORCHESTRATION_ADVANCED
    assert "能力行" in hint and "交付状态" in hint and "文件面板" in hint
    assert "工具回执" in hint
    assert "未对照则不得声称" in hint
    assert "用户面前空白" in hint
    assert "先给用户一句可见打算" in hint
    orch_honesty = _TEAM_ORCHESTRATION_ADVANCED
    assert "收口对照核" in orch_honesty and "主张对照本回合结构真相" in orch_honesty
    for title in (
        "面板可见·落盘对账",
        "改文件·诚实落盘",
        "可见症状·勿报已修",
        "附件·勿否认",
        "已有结果·勿否认",
        "交付验收对照",
        "可用性短问",
        "概览契约",
        "收尾·先报断点",
        "长跑收口·打开看见",
    ):
        assert title not in orch_honesty, title
    lf = build_system_skill_registry().get("long_form_writing").body
    assert "多源合并" in lf and "成篇优先" in lf
    orch_sum = build_system_skill_registry().get("team_orchestration_advanced").summary
    assert "成文编制" in orch_sum
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "形状词汇" in skill
    assert "改产物 / 成规模取证该派就派" in skill or "成规模取证该派就派" in skill
    assert "桌上结果已定" in skill
    assert "教学示例形状" in skill and "对照学形状" in skill
    assert "免手搓" not in skill
    assert "并列对象分组" in skill and "独立多透镜诊断" in skill
    assert "实现+独立验证" in skill
    assert "跨域合成" in skill or "按工种" in skill
    assert "必读锚点" in skill or "≤2–3" in skill or "≤2-3" in skill
    assert "第一棒" in skill or "壳层" in skill
    assert "多屏" in skill and ("大原型" in skill or "单文件" in skill)
    assert "完整可玩" in skill
    assert "规格已齐 ≠ 全量" in skill or "规格已齐≠全量" in skill
    assert "结构槽" in skill or "playbook_args" in skill
    assert "设计波" in skill or "约定文档说明" in skill
    assert "真两段" in skill
    assert "假两段" in skill
    assert "同一 task" in skill
    assert "桌面壳" in skill or "多进程" in skill
    assert "省略 playbook" in skill
    assert "playbook=none" not in skill
    assert "playbook_id" not in skill
    assert "可跑闭环" in skill or "核心运行时" in skill
    assert "根委派切片诚实" in skill or "嵌套扇出" in skill
    assert "人已派出" not in skill
    assert "谁在后台、完成后会再汇报" not in skill


def test_ask_user_description_owns_when_to_ask():
    """何时问唯一所有者 = ask_user description；核不写「有默认就跳过发问」。"""
    hint = _CEO_CORE_HINT
    assert "有稳妥默认" not in hint
    assert "挡路才问" not in hint
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.ask_user.tool import AskUserTool

    desc = AskUserTool(
        sink=EventSink(), conversation_id="c1", timeout_seconds=30.0
    ).schema.description
    assert "挡路才问" in desc
    assert "猜错会做错" in desc
    assert "可逆低杠杆" in desc
    assert "标假设" in desc
    assert "unlocks" not in hint
    assert "【非阻塞问·压单】" not in hint
    assert "未答则拒" not in hint
    assert "拒 `delegate`" not in hint
    assert "拒 delegate" not in hint


def test_consult_hook_lives_only_in_the_core():
    """consult 钩在核；场面 HOW 在 skill 正文；目录只写这是什么。"""
    from agentcore.runtime.skills import render_skill_directory

    directory = render_skill_directory(
        build_system_skill_registry(),
        {"delegate", "consult", "ask_user", "debate"},
    )
    hint = _CEO_CORE_HINT
    assert "consult(name)" in hint
    assert "讨论/判断默认自己答不必查" not in hint
    assert "按场面：拿不准怎么拆" not in hint
    assert "Office / 空桌" not in hint
    assert "team_delivery_env" not in hint
    assert "先 consult `team_orchestration_advanced` 再决定团队形态" not in directory
    assert "纯对话式回答自己答即可，无需 consult" not in directory
    orch_sum = build_system_skill_registry().get("team_orchestration_advanced").summary
    assert "成文编制" in orch_sum
    assert orch_sum in directory
    delivery_sum = build_system_skill_registry().get("team_delivery_env").summary
    assert "Office" in delivery_sum
    assert "空桌" in delivery_sum
    cross_sum = build_system_skill_registry().get("team_cross_folder").summary
    assert "跨文件夹" in cross_sum or "target_folder_id" in cross_sum


def test_directory_preamble_only_says_what_and_how_to_pull():
    """前言只说这是按需目录、列 name＋摘要、用 consult 拉全文。
    何时拉 / 四类 / 低频进表 / 常驻不必查 → consult description。"""
    from agentcore.runtime.resolve.prompt.compose import _on_demand_preamble
    from agentcore.tools.builtin.consult import ConsultTool

    preamble = "\n".join(_on_demand_preamble(with_summaries=True))
    desc = ConsultTool(source=None).schema.description  # type: ignore[arg-type]
    assert "consult(name)" in preamble and "按需目录" in preamble
    assert "摘要" in preamble
    for restated in (
        "intensity",
        "playbook",
        "build_website",
        "build_app",
        "building_software",
        "薄旁路",
        "桌上档",
        "五波",
    ):
        assert restated not in preamble
    for owned_by_desc in (
        "系统能力指引",
        "按需用户规则",
        "记忆主题笔记",
        "无需查阅",
        "下一模型轮",
        "能力行已装配",
    ):
        assert owned_by_desc not in preamble
        if owned_by_desc != "能力行已装配":
            assert owned_by_desc in desc


def test_core_teaches_delegate_graph_and_coordinate_invariants():
    # 产品 AI 自述委派机制时曾误称「一次只能一个 delegate、同步阻塞」。
    # HOW 在 team_orchestration_advanced，核不复述。
    hint = _CEO_CORE_HINT
    assert "一回合一张协作图" not in hint
    assert "coordinate=false" not in hint
    assert "checkpoint_after" not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "不必等" in skill or "同回合再调" in skill
    assert "再带一层子队" in skill
    assert "二选一" in skill
    assert "一回合一张协作图" in skill


def test_core_teaches_dependency_judgment_before_delegating():
    # depends_on 正反例 HOW 只留编排 skill。
    hint = _CEO_CORE_HINT
    assert "team_orchestration_advanced" not in hint
    assert "正例" not in hint and "反例" not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "生产者→消费者" in skill or "下游是否要吃上游" in skill
    assert "depends_on" in skill
    assert "正例" in skill and "反例" in skill
    assert "全平铺" in skill or "平铺并行" in skill


def test_core_teaches_coordination_budget_awareness():
    # 协调预算数值与口径唯一所有者 = 编排 skill；核不复述。
    from agentcore.runtime.coordination.session import (
        DEFAULT_COORDINATION_BUDGET,
        MAX_COORDINATION_BUDGET,
    )

    hint = _CEO_CORE_HINT
    assert "协调预算" not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "协调预算" in skill
    assert f"默认约 {DEFAULT_COORDINATION_BUDGET} 次" in skill
    assert f"上限 {MAX_COORDINATION_BUDGET} 次" in skill
    assert "量力" in skill or "里程碑" in skill


def test_core_teaches_new_turn_new_graph_not_cross_turn_append():
    # 跨回合【合进旧图】已废除；接着上一支团队干 = 新开一队、接续上一张图。
    # HOW 唯一所有者 = skill；核不复述续接口径。
    hint = _CEO_CORE_HINT
    assert "新开一队" not in hint
    assert "接续上一张图" not in hint
    assert 'append_to_execution_id="latest"' not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "【新回合新图】" in skill
    assert "【跨回合延续】" not in skill
    assert "append_to_execution_id" in skill
    assert "新开一队、接续上一张图" in skill
    assert "向用户汇报用" not in skill
    assert "只计本图" in skill
    assert "已往上方协作图追加" not in skill
    assert "recent_team_graph" not in skill

def test_skill_teaches_same_layer_pipeline():
    # A multi-stage pipeline is a DAG within ONE delegate call (depends_on, same
    # layer) — moved to team_orchestration_advanced (P3). The nesting axis
    # (delegation depth) lives in the same skill.
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "depends_on" in skill
    assert "同一层" in skill


def test_core_teaches_delegating_parallel_research():
    # 成规模查证归团队；编制细则在编排 skill，核不写探路判决树。
    hint = _CEO_CORE_HINT
    assert "超规模" in hint
    assert "成规模查证" not in hint
    assert "【探路 ≠ 摸底】" not in hint
    assert "自动两路" not in hint
    assert "默认 ≥2" not in hint
    assert "引擎不剥" not in hint
    assert "必须派 brief" not in hint
    assert "问面广度" not in hint
    assert "开局即派" not in hint
    assert "不派仅限" not in hint
    assert "【工作流】" in _TEAM_ORCHESTRATION_ADVANCED
    assert "探路 0～1 轮" not in _TEAM_ORCHESTRATION_ADVANCED


def test_prompt_investigation_discipline_follows_settings():
    """探路停手在编排 skill【工作流】（认识条件，非轮次配额）；核与场面门不写 0～1。"""
    from agentcore.runtime.resolve.prompt.cold_start import (
        _COLD_START_EXPLORE_HINT_EMPTY,
        _COLD_START_EXPLORE_HINT_REBIND,
        _COLD_START_EXPLORE_HINT_REFRESH,
    )
    from agentcore.runtime.skills.deep_multi_lens_research import _DEEP_MULTI_LENS_RESEARCH

    assert "超规模" in _CEO_CORE_HINT
    assert "成规模查证" not in _CEO_CORE_HINT
    assert "【探路 ≠ 摸底】" not in _CEO_CORE_HINT
    assert "不派仅限" not in _CEO_CORE_HINT
    assert "0～1" not in _CEO_CORE_HINT
    assert "0～1" not in _COLD_START_EXPLORE_HINT_EMPTY
    assert "0～1" not in _COLD_START_EXPLORE_HINT_REBIND
    assert "0～1" not in _COLD_START_EXPLORE_HINT_REFRESH
    assert "【工作流】" in _TEAM_ORCHESTRATION_ADVANCED
    assert "探路 0～1 轮" not in _TEAM_ORCHESTRATION_ADVANCED
    assert "0～1" not in _TEAM_ORCHESTRATION_ADVANCED
    assert "【工作流】" in _DEEP_MULTI_LENS_RESEARCH
    assert "探路检索默认 0～1 轮" not in _DEEP_MULTI_LENS_RESEARCH
    assert "定位入口" in _COLD_START_EXPLORE_HINT_EMPTY
    assert "定位入口" in _COLD_START_EXPLORE_HINT_REBIND
    assert "定位入口" in _COLD_START_EXPLORE_HINT_REFRESH
    assert "只定位入口" not in _COLD_START_EXPLORE_HINT_EMPTY
    assert "定位入口" in _TEAM_ORCHESTRATION_ADVANCED
    assert "禁止自己取证" in _DEEP_MULTI_LENS_RESEARCH


def test_core_forbids_silent_worker_count_discount():
    # 点名人数 / 撞上限 HOW 在编排 skill 与 eval，不进常驻核判决树。
    hint = _CEO_CORE_HINT
    assert "派满 N" not in hint
    assert "向用户明示取舍" not in hint


def test_core_teaches_one_heavy_task_per_worker():
    # 编制细则在编排 skill，核不写「一件重活」判决。
    hint = _CEO_CORE_HINT
    assert "一个 worker 只派一件重活" not in hint
    assert "文件类交付物" not in hint


def test_core_reminds_pass_hidden_context_to_worker():
    # Worker 看不到对话历史：权威在 delegate schema，不在常驻核。
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS

    task_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["task"][
        "description"
    ]
    assert "看不到" in task_desc
    assert "历史" in task_desc
    assert "看不到" not in _CEO_CORE_HINT or "对话历史" not in _CEO_CORE_HINT


def test_core_teaches_confirmed_constraints_block_on_delegate():
    """已确认约束填法在编排 skill + delegate schema；核不复述。"""
    hint = _CEO_CORE_HINT
    assert "【已确认约束】" not in hint
    assert "派工须带已确认约束块" not in hint
    from agentcore.tools.builtin.delegate.schema import DELEGATE_PARAMETERS, TASK_DELIVERABLE_SCHEMA

    deliverable_desc = TASK_DELIVERABLE_SCHEMA["description"]
    task_desc = DELEGATE_PARAMETERS["properties"]["tasks"]["items"]["properties"]["task"][
        "description"
    ]
    assert "已确认约束" in deliverable_desc or "已确认约束" in task_desc
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "已确认约束" in skill
    assert "同一行" in skill
    assert "自由文" in skill
    assert "意图分类" in skill
    assert "约束块优先" in skill
    assert "自拟" in skill and "冒充拍板" in skill
    assert "（无）" in skill or "无已拍板项" in skill
    assert "附件" in skill


def test_core_teaches_assumption_is_not_user_confirmation():
    """终稿不得把模型自填假设写成用户已确认。标假设在 ask_user description；细则在 skill。"""
    hint = _CEO_CORE_HINT
    assert "标假设" not in hint
    assert "假设≠用户确认" not in hint
    assert "称确认仅限" not in hint
    assert "周末旅行" not in hint
    assert "意图分类" not in hint
    assert "队员已全部完成" not in hint
    from agentcore.runtime.events import EventSink
    from agentcore.tools.builtin.ask_user.tool import AskUserTool

    desc = AskUserTool(
        sink=EventSink(), conversation_id="c1", timeout_seconds=30.0
    ).schema.description
    assert "标假设" in desc
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "按确认默认" in kickoff
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "自拟的默认" in orch and "冒充拍板" in orch
    assert "无已拍板项" in orch


def test_core_teaches_unsettled_ask_user_stops_without_done():
    """未结算细则不进常驻核判决树；kickoff 仍教空 continue 回灌按确认默认。不改 pause。"""
    hint = _CEO_CORE_HINT
    assert "【ask 未结算】" not in hint
    assert "promote_product" not in hint
    assert "标假设" not in hint
    worker = compose_worker_base_prompt(assemble_system_prompt())
    assert "【ask 未结算】" not in worker
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "promote_product" not in kickoff
    assert "按确认默认" in kickoff
    assert "未结算" not in kickoff
    assert "不得当已办完" not in kickoff


def test_shared_base_teaches_howto_stale_path_honesty():
    """逐步路径无现行可核 → 标易变；并进 delivery_honesty，不单开检查表。"""
    base = _DEFAULT_SYSTEM_PROMPT
    honesty = base.split("<delivery_honesty>", 1)[1].split("</delivery_honesty>", 1)[0]
    tool = base.split("<tool_use>", 1)[1].split("</tool_use>", 1)[0]
    assert "现行可核" in honesty
    assert "易变" in honesty
    assert "<claim_evidence>" not in base
    assert "易变" not in tool
    assert "当日" not in base
    assert "日历门槛" not in base
    assert "伪精确" not in honesty
    assert "零工具" not in honesty


def test_skill_teaches_constraint_vs_solution_boundary():
    # 认知分工边界（约束 vs 方案）: the CEO writes requirements/constraints into the
    # task, but leaves the deliverable's professional STRUCTURE (a paper's chapters /
    # argument, a codebase's architecture) to the expert worker — unless the user
    # fixed it. Moved to team_orchestration_advanced (P3). Pins the fix for the
    # 「CEO 替专家把方案定死、worker 沦为填字员」regression (法律论文案例).
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "专业方案" in skill
    assert "填字员" in skill
    # 审查 / 评估类「指路不代答」：重点关注进 team_brief，不写进 task 替答。
    assert "team_brief" in skill
    assert "heads_up" not in skill
    assert "替答" in skill
    assert "seed_notes" not in skill
    assert "引导性问题" in skill or "风险预判" in skill
    assert "已确认约束" in skill


def test_core_teaches_delegate_point_dont_answer():
    # task 长教法只在编排 skill；核不点名手册。
    hint = _CEO_CORE_HINT
    assert "目标·边界·验收" not in hint
    assert "team_orchestration_advanced" not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "施工图" in skill or "填字员" in skill
    assert "heads_up" not in skill
    assert "替答" in skill
    assert "seed_notes" not in skill
    assert "引导性问题" in skill or "风险预判" in skill


def test_core_teaches_execution_and_recall_routing():
    # HOW 在挂门手册 / 编排 skill；核不写执行判决树。
    hint = _CEO_CORE_HINT
    term_how = capability_how_suffix({"terminal"})
    assert "【执行 / 运行 / 打开】" not in hint
    assert "consult(terminal)" not in hint
    assert "consult(browser)" not in hint
    assert "跑起来" in term_how
    assert "报 URL" in term_how
    assert "wait_for" in term_how
    br_how = capability_how_suffix({"browser"})
    assert "右坞打开" in br_how or "用浏览器打开" in br_how or "开页" in br_how
    assert "delegate" in hint
    assert "假开页" not in hint
    mid = build_system_skill_registry().get("ask_user_midtask").body
    assert "已绑" in mid and "跑起来看一下" in mid
    assert "当前" in mid and "工作区" in mid
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "tsc -b" in orch
    assert "npm install" in orch
    assert "code_execute" in orch
    assert "wait_for" not in hint
    assert "口头假验收" not in hint
    assert "已登录，继续" not in hint
    assert "code_diagnostics" not in hint
    assert "【回忆 / 核实产出】" not in hint


def test_core_teaches_repair_code_ui_verify_routing():
    """白屏/挂载复现 HOW 在编排 skill；核只留修码 consult 钩（team_orchestration 已有则勿双写）。"""
    hint = _CEO_CORE_HINT
    assert "revising_a_product" not in hint
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "手写 1 人" in orch
    assert "form=workspace" in orch
    assert "短任务" in orch
    assert "diagnose_fix_verify" in orch
    assert "白屏" in orch or "挂载" in orch
    assert "browser" in orch
    assert "verify=" in orch
    assert "勿" in orch and ("tsc" in orch or "pytest" in orch)
    for token in ("complexity_hint", "result_handling", "require_upstream"):
        assert token not in hint, token


def test_core_teaches_code_audit_modules_fanout():
    """整仓审计填 modules 扇出；细则（2–3、配额、无主管）在编排 skill。"""
    hint = _CEO_CORE_HINT
    assert "code_audit" not in hint
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "playbook_args.modules" in orch or "modules" in orch
    assert "整仓" in orch and "多子系统" in orch
    assert "单缝省略" in orch
    # 上限 / 折叠 / 配额 HOW → team_orchestration_advanced，不占常驻核


def test_core_teaches_audit_narrow_scope():
    """审查收窄 HOW 在编排 skill；核只留钩。单点展示走 1 人，不属于探路摸底。"""
    hint = _CEO_CORE_HINT
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "审查收窄" in orch or "审查·收窄" in orch
    assert "单点展示" in orch
    assert "探路 ≠ 摸底" not in hint
    assert "1 人即可" not in hint


def test_core_teaches_outline_checkpoint_prefers_structured_path():
    # 主拍板细则在 ask_user_* / delegate_checkpoint；核不复述形状。
    hint = _CEO_CORE_HINT
    assert "主拍板" not in hint
    assert "仪式卡" not in hint
    mid = build_system_skill_registry().get("ask_user_midtask").body
    ckpt = build_system_skill_registry().get("delegate_checkpoint").body
    assert "主拍板" in mid
    assert "每任务恰好" in mid
    assert "发散挑选" in mid
    assert "continue_from_run_id" in mid
    assert "多选 choice" in mid or "勾选要处理" in mid
    assert "主拍板" in ckpt
    assert "每任务恰好" in ckpt


def test_core_worker_capability_follows_workspace_facts():
    # Prompt 事实对齐（能力闸门与交付诚实性）：不再宣称 worker「持全套工具」；以
    # <workspace_context> 的「本回合执行能力」行为准——code_execute=未装配 时 worker
    # 同样没有执行环境（能写文件、不能运行 / 生成二进制产物）。
    hint = _CEO_CORE_HINT
    assert "持全套工具" not in hint
    assert "本回合执行能力" not in hint
    assert "code_execute=未装配" not in hint
    assert "能写文件、不能运行" not in hint
    assert "可播放文件" not in hint
    delivery = _TEAM_DELIVERY_ENV
    assert "data_file_landing" not in hint
    catalog_data = build_system_skill_registry().get("data_file_landing")
    assert catalog_data is not None
    assert "表格 → `.csv`" not in hint
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "执行事实行" in delivery
    # 表质量 / 源数据下一步 HOW 在交付手册（核只留 consult 一句）
    assert "表质量基线" in delivery
    assert "冒充表结构" in delivery
    assert "源数据文件下一步" in delivery
    assert "无法可靠解析的源数据文件" in delivery
    assert "另编" in delivery
    assert "源数据文件下一步" not in orch
    # 工程/代码无执行补救权威在交付手册，不进事实行、不进核。
    assert "export_to_local" not in hint
    # 成品文件只装成品：全文在 long_form_writing，核不复述
    lf = build_system_skill_registry().get("long_form_writing").body
    assert "【成品文件只装成品】" in lf
    assert "【成品文件只装成品】" not in hint
    assert "使用前请核对" not in hint


def test_core_teaches_delivery_honesty_when_no_execution():
    # 云端无执行环境：核留结构指针；交付缺口细节在编排 skill。
    hint = _CEO_CORE_HINT
    assert "consult(browser)" not in hint
    assert "【执行 / 运行 / 打开】" not in hint
    skill = _TEAM_ORCHESTRATION_ADVANCED
    delivery = _TEAM_DELIVERY_ENV
    assert "test_run" in skill or "verify" in skill
    assert "未运行验证" in skill or "交付缺口" in delivery
    assert "form=files" in skill


def test_core_teaches_empty_desk_no_project_shell():
    """空桌勿套工程壳：唯一所有者 = team_delivery_env；核不复述。"""
    hint = _CEO_CORE_HINT
    assert "【空桌落盘】" not in hint
    delivery = _TEAM_DELIVERY_ENV
    assert "工程壳" in delivery
    assert "同名" in delivery and ("顶层" in delivery or "再套" in delivery)
    assert "要不要再套一层" not in delivery
    assert "mkdir" not in hint
    assert "create_folder" not in hint


def test_core_teaches_dispatch_landing_not_promote_ritual():
    """成品落点权威在 delegate schema + 编排 skill；assembled CEO 提示不得再出现归位仪式。"""
    hint = _CEO_CORE_HINT
    assert "【派单落点】" not in hint
    from agentcore.tools.builtin.delegate.schema import TASK_DELIVERABLE_SCHEMA

    pin = TASK_DELIVERABLE_SCHEMA["properties"]["form"]["description"]
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "【看】" in pin and "【存文档】" in pin and "【改工程】" in pin
    assert "prose" in pin and "files" in pin
    assert "workspace" in pin
    assert "只看" in orch and "prose" in orch
    assert "工作稿/" in orch or "工作稿" in pin
    assert "workspace_native" not in hint
    assert "写完再搬" not in hint
    assert "产物卡" not in hint
    assert "归位" not in hint
    assert "promote_product" not in hint
    assert "【成品归位】" not in hint
    ceo = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult", "ask_user", "promote_product"},
    )
    assert "promote_product" not in ceo
    assert "【成品归位】" not in ceo


def test_core_teaches_panel_path_not_workspace_root_jargon():
    """对用户指路禁说「工作区根」：HOW 在 product_help_map；核不枚举禁语。"""
    hint = _CEO_CORE_HINT
    assert "工作区根" not in hint
    assert "工作区根目录" not in hint
    help_map = build_system_skill_registry().get("product_help_map").body
    assert "工作区根" in help_map
    assert "工作区根目录" in help_map
    assert "禁止" in help_map
    assert "对用户说" not in hint
    assert "本文件夹根即工作区根" not in hint


def test_core_teaches_delivery_path_by_workspace_type():
    # 核留执行/打开钩；收口路径 HOW → product_help_map / 编排 skill。
    hint = _CEO_CORE_HINT
    help_map = build_system_skill_registry().get("product_help_map").body
    delivery = _TEAM_DELIVERY_ENV
    assert "【产物路径】" in delivery
    assert "完整" in delivery
    assert "交付下载·面板路径" in delivery
    assert "下载失败" in delivery or "404" in delivery
    assert "file_list" in delivery
    assert "file_list(pattern)" not in delivery
    assert "闷声" in delivery or "空泡" in delivery
    assert "执行位置分道" in help_map
    assert "文件」面板" in help_map or "文件面板" in help_map
    assert "完整预览" in help_map
    assert "双击打开" in help_map
    assert "禁止给本机磁盘路径" in help_map or "禁止给本机" in help_map
    assert "真实路径" in help_map
    assert "执行位置分道" not in hint
    assert "收口硬约束" not in hint
    assert "系统浏览器" not in hint
    assert "consult(browser)" not in hint
    how = _LOCAL_HOW
    assert "用浏览器打开" in how or "右坞打开" in how
    assert "跑起来" in how or "打开看一下" in how
    assert "delegate" in hint
    assert "右坞" in how
    assert "wait_for" not in hint
    assert "已登录，继续" not in hint
    assert "口头假验收" not in hint
    assert "未装配" in _DEFAULT_SYSTEM_PROMPT
    assert "不得声称" in _DEFAULT_SYSTEM_PROMPT
    assert "假开页" not in hint
    assert "read_url" in how and "已开页" in how
    assert "escalate → 右坞接管" not in hint
    assert "仅可作标明" not in hint
    assert "永不代填密码" in how
    assert "开页" in how or "帮我看页面" in how or "右坞打开" in how
    assert "自己" in how
    assert "read_url" in how
    assert "ask_user(browser_login=true)" in how


def test_shared_base_capability_honesty_does_not_share_circled_numbers():
    """基座能力诚实不用 ①–⑤，也不写未装配收口产线。"""
    honesty = _DEFAULT_SYSTEM_PROMPT.split("<capability_honesty>", 1)[1].split(
        "</capability_honesty>", 1
    )[0]
    for mark in ("①", "②", "③", "④", "⑤"):
        assert mark not in honesty
    assert "手脑" not in honesty
    assert "同轮可开工" not in honesty
    assert "否决论文" not in honesty


def test_shared_base_teaches_unassembled_capability_honesty():
    """未装配不许假装用过：双条件在共享基座，队员看得到；CEO 该段只出现一次，核只留禁派。"""
    base = assemble_system_prompt()
    worker = compose_worker_base_prompt(base)
    assert "未装配" in worker and "不得声称" in worker
    assert "<capability_honesty>" in worker
    assert "【能力未装配·统一姿势】" not in _DEFAULT_SYSTEM_PROMPT
    assert "【能力未装配·统一姿势】" not in _CEO_CORE_HINT
    assert "未装配能力" in _CEO_CORE_HINT
    assert "把该能力的动作写进给队员的任务" not in _CEO_CORE_HINT
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult", "ask_user"},
    )
    assert ceo.count("<capability_honesty>") == 1
    assert "未装配能力" in ceo


def test_shared_base_teaches_assembled_capability_not_a_refusal_essay():
    """已装配不许假装没有：同一能力行按格诚实；不在开场表 ≠ 未装配；邻格 ≠ 否决本格。"""
    base = assemble_system_prompt()
    worker = compose_worker_base_prompt(base)
    assert "【能力已装配·禁止否决论文】" not in _DEFAULT_SYSTEM_PROMPT
    assert "【能力已装配·禁止否决论文】" not in _CEO_CORE_HINT
    assert "已装配" in worker and "通道在" in worker
    assert "邻格" in worker and "≠" in worker
    from agentcore.tools.builtin.consult import ConsultTool

    assert "本回合下一模型轮" in ConsultTool(source=None).schema.description  # type: ignore[arg-type]
    hint = _CEO_CORE_HINT
    assert "问方法" not in hint
    assert "capability_honesty" not in hint
    ceo = compose_ceo_chat_prompt(
        base,
        skill_registry=build_system_skill_registry(),
        ceo_tool_names={"delegate", "consult", "ask_user"},
    )
    assert ceo.count("<capability_honesty>") == 1


def test_core_teaches_presentation_honesty():
    # 第六刀：核只留开火短卡；替代表 / Marp / Presentation() 长 HOW 钉编排 skill。
    # 案 0a71：核不枚举后缀、不散文断言导出器装配态。
    hint = _CEO_CORE_HINT
    orch = _TEAM_ORCHESTRATION_ADVANCED
    delivery = _TEAM_DELIVERY_ENV
    assert "产物格式" in hint
    assert "不可产" not in hint
    assert "已落盘可直接使用" not in hint
    assert "先干再问" not in hint
    assert "点名载体" not in hint
    assert "form/artifacts" in delivery
    assert "不可产" in delivery and "等效替代" in delivery
    assert "静默降级" in delivery
    assert "确定性导出器" in delivery
    assert "说满" in delivery and "空派" in delivery
    assert "pptx" not in hint.lower() and "xlsx" not in hint.lower()
    assert "SmartArt" not in hint and "DrawingML" not in hint
    assert "Presentation()" not in hint
    assert "Marp 语法" not in hint
    assert "图形组织图" not in hint
    assert "data_file_landing" not in hint
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "format_options" not in kickoff
    assert "style_options" not in kickoff
    assert "python-pptx" in delivery
    assert "代写全章节大纲" in orch
    assert "Marp" not in orch
    assert "file_copy" in delivery
    assert "当模板" in delivery
    assert "Presentation()" in delivery
    assert "再派" in delivery and "跑脚本" in delivery
    assert ".py" in delivery and "不算" in delivery
    assert "压体积" in delivery and "模板保真" in delivery
    assert "*_slim.pptx" in delivery or "slim.pptx" in delivery
    assert "图形组织图" in delivery
    assert "直接拒" in delivery
    assert "文本" in delivery and "表格版" in delivery
    assert "说满" in delivery and "空派" in delivery
    assert "先干再问" in delivery
    assert "文档 → `.md`" in delivery or "文档 →" in delivery


def test_core_defers_format_capability_to_facts_not_prose():
    """`.docx`/`.pdf` 与执行正交这条知识，权威在事实行 + 编排 skill，不在常驻核。

    原为 test_core_teaches_word_pdf_orthogonal_to_execution：核里散文断言
    「md_to_docx / md_to_pdf 无条件装配」，而 CEO 不持这两把工具、在自己的工具列表里
    看不到它们（案 0a71 的思考链有三轮在猜 worker 有没有）。现在装配态由
    `<workspace_context>` 的 `产物格式：` 行按注册表真实闸算出来，核只负责怎么用那行。
    """
    hint = _CEO_CORE_HINT
    assert "md_to_docx" not in hint and "md_to_pdf" not in hint
    assert "产物格式" in hint
    orch = _TEAM_ORCHESTRATION_ADVANCED
    delivery = _TEAM_DELIVERY_ENV
    assert "确定性导出器" in delivery
    assert "md_to_docx" in delivery and "md_to_pdf" in delivery
    assert "与执行正交" in delivery
    assert "产物格式" in delivery
    assert "无条件装配" not in delivery
    assert "与执行正交" not in orch


def test_core_teaches_review_sections_in_task_body():
    """第七刀：审查章节 HOW 在编排 skill；⑥ 后不再常驻开火短卡。"""
    hint = _CEO_CORE_HINT
    assert "审查章节" not in hint
    assert "同字面" not in hint
    assert "近义改写" not in hint
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "审查章节" in orch
    assert "task 正文" in orch
    assert "同字面" in orch or "同一套原文" in orch
    assert "近义" in orch
    assert "裸报错" in orch or "藏契约" in orch


def test_core_teaches_short_edit_not_m2a_kickoff_template():
    """案 7e9d2d4b：核留钩；开工模板原话 HOW 在 ask_user_kickoff。"""
    hint = _CEO_CORE_HINT
    assert "短改稿" not in hint
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "短改稿" in kickoff
    assert "任务卡" in kickoff
    assert "ask_user_kickoff" not in hint
    assert "收到：任务编号" not in hint
    assert "M2A" not in hint
    assert "收到：任务编号" in kickoff
    assert "规格已冻结" in kickoff
    assert "开工模板" in kickoff
    assert "任务卡结构字段" in kickoff or "显式点名" in kickoff


def test_core_teaches_explicit_confirm_before_disk_write():
    """HOW 在 ask_user_midtask【落盘前对齐】；核不写落盘前对齐判决。"""
    hint = _CEO_CORE_HINT
    assert "明示确认后再落盘" not in hint
    assert "ask_user_midtask" not in hint
    assert "落盘前对齐" not in hint
    mid = build_system_skill_registry().get("ask_user_midtask").body
    assert "落盘前对齐" in mid
    assert "确认后再存" in mid or "先对齐再写" in mid
    assert "阻塞短问" in mid
    assert "default" in mid
    assert "本回合明示" in mid


def test_windows_bat_how_lives_in_work_discipline_not_shared_base():
    """案 261bfc46 A：Windows .bat HOW 在 work_discipline；基座不常驻；目录摘要只写这是什么。"""
    from agentcore.runtime.resolve.prompt import _DEFAULT_SYSTEM_PROMPT
    from agentcore.runtime.skills.work_discipline import _WORK_DISCIPLINE

    base = _DEFAULT_SYSTEM_PROMPT
    assert "<cross_platform_scripts>" not in base
    assert "ASCII-only" not in base
    wd = _WORK_DISCIPLINE
    assert ".bat" in wd and "CRLF" in wd
    assert "ASCII" in wd
    assert ".ps1" in wd
    assert "不" in wd and ("转码" in wd or "改换行" in wd)
    hint = _CEO_CORE_HINT
    assert "work_discipline" not in hint
    assert ".bat" not in hint
    assert "CRLF" not in hint
    catalog = build_system_skill_registry().get("work_discipline")
    assert catalog is not None
    assert ".bat" in catalog.summary or "Windows" in catalog.summary
    delivery = _TEAM_DELIVERY_ENV
    assert "work_discipline" in delivery and ".bat" in delivery
    assert "双击即用" in delivery


def test_core_teaches_image_gen_egress_and_key_boundary():
    """案 20260803-image-gen-byok-egress-boundary A+B：无 egress 禁代调出图；Key 不落盘。

    出图边界对照事实行「出站网络」；凭据本身怎么处理归共享基座 ``<credential_hygiene>``。
    """
    hint = _CEO_CORE_HINT
    assert "出站网络" in hint
    assert "生图" not in hint
    assert "credential_hygiene" not in hint
    delivery = _TEAM_DELIVERY_ENV
    assert "本机脚本" in delivery or "只写本机" in delivery or "只帮写" in delivery
    shared = assemble_system_prompt()
    assert "<credential_hygiene>" in shared
    hygiene = shared.split("<credential_hygiene>", 1)[1].split("</credential_hygiene>", 1)[0]
    assert "密钥" in hygiene and "明文" in hygiene
    assert "已识别凭据" in hygiene
    assert "索要" in hygiene and "明文" in hygiene
    assert "自己执行" in hygiene
    assert "带入当次进程" not in hygiene
    assert "自己开终端" not in hygiene
    assert "自己开终端" not in hint
    assert "自己机器上自备" not in shared
    assert "curl / 脚本自测" not in shared
    assert "环境变量占位" not in shared
    # 共享基座不点名某一端的设置页。
    assert "设置 · 服务商" not in shared
    assert "设置 · 模型" not in shared
    assert "handoff" not in hygiene
    assert "跨窗" not in hygiene
    # 凭据禁令不得在核里第二次落地
    assert "API Key" not in hint and "明文" not in hint
    assert "跨会话凭据脱敏" not in delivery
    assert "URL→工作区文件" not in delivery
    assert "生图" in delivery
    assert "出站网络" in delivery or "egress" in delivery.lower() or "HTTPS" in delivery
    assert "本机脚本" in delivery or "只写本机" in delivery or "只帮写" in delivery


def test_credential_hygiene_forbids_reask_plaintext_and_user_self_run():
    """基座凭据卫生原则仍在：禁止再索要明文、禁止把代跑写成用户自己执行。"""
    shared = assemble_system_prompt()
    hint = _CEO_CORE_HINT
    start = shared.index("<credential_hygiene>")
    end = shared.index("</credential_hygiene>")
    hygiene = shared[start:end]
    assert "索要" in hygiene and "明文" in hygiene
    assert "自己执行" in hygiene
    assert "自己开终端" not in hygiene
    assert "索要" not in hint
    assert "自己开终端" not in hint


def test_core_teaches_cloud_web_install_verify_honesty():
    """装包/验绿 HOW 在 building_software；核不写验绿检查表。"""
    hint = _CEO_CORE_HINT
    assert "结构自检" not in hint
    assert "export_to_local" not in hint
    build = build_system_skill_registry().get("building_software").body
    assert "外环验绿对账" in build
    assert "test_run" in build
    assert "N/N OK" in build or "passed" in build
    assert "分轴" in build or "零写盘" in build


def test_core_teaches_short_clarify_not_scene_ledger():
    hint = _CEO_CORE_HINT
    assert "短问" not in hint
    assert "提案墙" not in hint
    assert "可只带" not in hint
    kickoff = build_system_skill_registry().get("ask_user_kickoff").body
    assert "短问" in kickoff or "短澄清" in kickoff
    assert "开工提案卡" not in kickoff
    assert "一键开做" not in kickoff
    assert "缺信息" in kickoff and "短问" in kickoff


def test_skill_teaches_environment_capability_constraint():
    # 编排 skill：无执行环境时改交付形态、显式标缺口（S3：无 kind 硬拒文案）。
    # 轻对齐：跑/验终向靠提示词对照 workspace（引擎不扫用户文硬分叉）。
    skill = _TEAM_ORCHESTRATION_ADVANCED
    delivery = _TEAM_DELIVERY_ENV
    assert "环境能力约束" in skill
    assert "code_execute=未装配" in delivery
    assert "交付缺口" in delivery
    assert "bind_local_folder" in _TEAM_CROSS_FOLDER
    assert "consult(ask_user_midtask)" in _TEAM_CROSS_FOLDER
    assert "导入到云" not in _TEAM_CROSS_FOLDER
    assert "导入到云" in delivery
    assert "consult(ask_user_midtask)" in delivery
    assert "连接 Git" not in delivery
    assert "合法非默认" in delivery or "非默认" in delivery or "本机传统" in delivery
    assert "ask_user" in skill
    assert "form=files" in skill
    assert "能力策略收口" not in skill


def test_shared_base_teaches_delivery_honesty():
    # 共享基座只留队员也会过的诚实元规则（围栏 + #rN + 只读口径）。
    # 综述对账 / 口头验收 / 可用性短问 / 概览契约是队长收口：核留元规则，手册不再堆这些标题。
    from agentcore.runtime.resolve.prompt import _DEFAULT_SYSTEM_PROMPT

    assert "<delivery_honesty>" in _DEFAULT_SYSTEM_PROMPT
    assert "<delivery_baseline>" not in _DEFAULT_SYSTEM_PROMPT
    assert "已登记" in _DEFAULT_SYSTEM_PROMPT
    assert "#rN" in _DEFAULT_SYSTEM_PROMPT
    assert "围栏" in _DEFAULT_SYSTEM_PROMPT and "成对" in _DEFAULT_SYSTEM_PROMPT
    assert "真假引擎查" not in _DEFAULT_SYSTEM_PROMPT
    assert "搜到 ≠ 可挂来源号" not in _DEFAULT_SYSTEM_PROMPT
    assert "search-only 不可" not in _DEFAULT_SYSTEM_PROMPT
    assert "引擎会核验" not in _DEFAULT_SYSTEM_PROMPT
    assert "成稿可引用集" not in _DEFAULT_SYSTEM_PROMPT
    assert "写报告" in _DEFAULT_SYSTEM_PROMPT and "写工具" in _DEFAULT_SYSTEM_PROMPT
    assert "交付验收对照" not in _DEFAULT_SYSTEM_PROMPT
    assert "禁口头验收" not in _DEFAULT_SYSTEM_PROMPT
    assert "可用性短问" not in _DEFAULT_SYSTEM_PROMPT
    assert "概览契约" not in _DEFAULT_SYSTEM_PROMPT
    assert "派持" not in _DEFAULT_SYSTEM_PROMPT
    hint = _CEO_CORE_HINT
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "对照本回合结构面" in hint
    assert "未对照则不得声称" in hint
    assert "队员交卷" not in hint
    assert "交付验收对照" not in orch
    assert "可用性短问" not in orch
    assert "概览契约" not in orch


def test_worker_opening_drops_captain_only_context():
    """队员开场不含派单/协调/跨路复核语境；叶子与嵌套 lead 目录同名（前缀一致）。"""
    from agentcore.runtime.context.consultable import ConsultDirectoryEntry
    from agentcore.runtime.skills.registry import AUDIENCE_WORKER

    base = assemble_system_prompt()
    worker_bare = compose_worker_base_prompt(base)
    for token in (
        "交付验收对照",
        "禁口头验收",
        "可用性短问",
        "概览契约",
        "派持",
        "team_orchestration_advanced",
        "team_cross_folder",
        "team_delivery_env",
        "build_website",
        "build_app",
        "building_software",
        "deep_multi_lens_research",
    ):
        assert token not in worker_bare, token

    reg = build_system_skill_registry()
    leaf_names = {s.name for s in reg.available(set(), audience=AUDIENCE_WORKER)}
    lead_names = {s.name for s in reg.available({"delegate"}, audience=AUDIENCE_WORKER)}
    assert leaf_names == lead_names
    for captain_manual in (
        "team_orchestration_advanced",
        "team_cross_folder",
        "team_delivery_env",
        "build_website",
        "build_app",
        "building_software",
        "deep_multi_lens_research",
    ):
        assert captain_manual not in leaf_names
    worker_dir = compose_worker_base_prompt(
        base,
        on_demand_entries=[
            ConsultDirectoryEntry(name=s.name, summary=s.summary)
            for s in reg.available(set(), audience=AUDIENCE_WORKER)
        ],
    )
    assert "team_orchestration_advanced" not in worker_dir
    assert "team_cross_folder" not in worker_dir
    assert "team_delivery_env" not in worker_dir
    assert "work_discipline" in worker_dir
    assert "long_form_landing" in worker_dir


def test_shared_base_teaches_claim_evidence_soft_constraint():
    # 引用即出处：主张对照台账；废名（暂靠提醒 / 辩词式 #eN / 机械闸对照 / 旧分段）缺席。
    from agentcore.runtime.resolve.prompt import _DEFAULT_SYSTEM_PROMPT

    assert "<claim_evidence>" not in _DEFAULT_SYSTEM_PROMPT
    assert "主张须证" not in _DEFAULT_SYSTEM_PROMPT
    assert "暂靠提醒" not in _DEFAULT_SYSTEM_PROMPT
    assert "本条暂无机械闸" not in _DEFAULT_SYSTEM_PROMPT
    assert "辩词式" not in _DEFAULT_SYSTEM_PROMPT
    assert "#eN" not in _DEFAULT_SYSTEM_PROMPT
    assert "待核实" in _DEFAULT_SYSTEM_PROMPT
    assert "#rN" in _DEFAULT_SYSTEM_PROMPT
    assert "search-only 不可" not in _DEFAULT_SYSTEM_PROMPT


def test_shared_base_teaches_work_authority():
    # 全局工作纪律：本回合指令优先 + `<rules>` 平权 + 冲突通道 + 决策权限（CEO+worker 共享）。
    from agentcore.runtime.resolve.prompt import _DEFAULT_SYSTEM_PROMPT

    assert "<work_authority>" in _DEFAULT_SYSTEM_PROMPT
    assert "读侧平权" in _DEFAULT_SYSTEM_PROMPT
    assert "用户规则硬胜" not in _DEFAULT_SYSTEM_PROMPT
    assert "软线索" not in _DEFAULT_SYSTEM_PROMPT
    assert "用户硬" not in _DEFAULT_SYSTEM_PROMPT
    assert "AI 软" not in _DEFAULT_SYSTEM_PROMPT
    assert "不自动升权威" in _DEFAULT_SYSTEM_PROMPT
    assert "escalate" in _DEFAULT_SYSTEM_PROMPT
    assert "ask_user" in _DEFAULT_SYSTEM_PROMPT
    assert "禁静默改权威稿" in _DEFAULT_SYSTEM_PROMPT
    assert "扩范围" in _DEFAULT_SYSTEM_PROMPT
    assert "工作区" in _DEFAULT_SYSTEM_PROMPT
    assert "正在做" in _DEFAULT_SYSTEM_PROMPT
    assert "没有现场" in _DEFAULT_SYSTEM_PROMPT
    assert "上一题残留" not in _DEFAULT_SYSTEM_PROMPT
    assert "旧项目名" not in _DEFAULT_SYSTEM_PROMPT


def test_ceo_core_workspace_outranks_global_current_project_memory():
    """继续项目 / 汇报现状：工作区优先在基座 `<work_authority>`，核不复述。"""
    from agentcore.runtime.resolve.prompt import _DEFAULT_SYSTEM_PROMPT

    hint = _CEO_CORE_HINT
    assert "【继续项目 / 汇报现状】" not in hint
    assert "上一题残留" not in hint
    assert "旧项目名" not in hint
    assert "工作区" in _DEFAULT_SYSTEM_PROMPT
    assert "正在做" in _DEFAULT_SYSTEM_PROMPT
    assert "没有现场" in _DEFAULT_SYSTEM_PROMPT
    # 权威线索 / 未定案 HOW 在 skill，不进核。
    assert "权威线索" not in hint
    assert "未定案·窄" not in hint
    assert "读全局规则" not in hint
    from agentcore.runtime.skills.work_discipline import _WORK_DISCIPLINE

    assert "未定案·窄" in _WORK_DISCIPLINE
    assert "架构" in _WORK_DISCIPLINE and "不可逆" in _WORK_DISCIPLINE
    assert "权威线索" in _TEAM_ORCHESTRATION_ADVANCED


def test_ceo_core_teaches_empty_shell_dual_folder_kickoff():
    """跨文件夹：场面 WHEN 在目录摘要；对照 HOW 在 team_cross_folder；核不复述。"""
    hint = _CEO_CORE_HINT
    assert "【跨文件夹】" not in hint
    assert "team_cross_folder" not in hint
    assert "写仍派工换桌" not in hint
    assert "摸已登记文件夹用只读跨桌" not in hint
    cross = _TEAM_CROSS_FOLDER
    assert "list_folder_dir" in cross and "read_folder_file" in cross
    assert "轻量认桌" in cross or "认桌/抽样" in cross
    assert "出生桌" in cross
    assert "云端读不到本地" in cross and "禁止" in cross
    assert "读写" in cross or "只读摸底" in cross
    assert "空 scratch" in cross or "不填" in cross
    assert "file_list" in cross
    assert "file_list(pattern)" not in cross
    assert "external_mount_readonly" in cross
    assert "开发双仓" in cross or "乱挂" in cross or "冒充" in cross
    assert "先建后派" in cross or "先建齐" in cross
    assert "拒后禁塌缩" in cross


def test_core_guides_out_of_workspace_absolute_paths():
    """区外路径：常驻只留底线 + 指针；可履约的授权手册跟 ``external_mount_readonly`` 装配走。

    授权全流程（挂载 / 升整理 / well_known 选点 / 失败分型）只有桌面回填通道在线才做得成，
    而该工具是 ``desktop_online_class``——装配即通道在线。通道不在的回合把这 900 字符手册
    常驻，等于让模型读一份本回合证明履行不了的操作说明。底线相反：它恰在通道缺失时才生效，
    所以不进常驻核。底线并进「未装配能力勿派进队员任务」。
    """
    hint = _CEO_CORE_HINT
    assert "工作区外" not in hint
    assert "host=未装配" not in hint
    assert "勿挂载" not in hint
    assert "未装配能力" in hint
    # 可履约手册不常驻：唯一所有者是 consult（``capability_how_suffix``），含授权后两步交付。
    for manual_only in ("well_known", "口头同意", "先写工作区", "只读已挂"):
        assert manual_only not in hint, f"{manual_only} 应只在 consult 手册里"
    granted = capability_how_suffix({"external_mount_readonly"})
    assert "external_mount_readonly" in granted
    assert "grant_organize_folder" in granted
    assert "grant_attach_folder" in granted
    assert "well_known" in granted
    assert "口头同意" in granted
    assert "只读已挂" in granted
    assert "grant_readonly_folder" not in granted
    assert "先写工作区" in granted and "file_copy" in granted
    # 不得无条件鼓动「立即发卡」——本机 Host/区外叙述只留在 workspace_context。
    assert "立即发卡" not in hint
    assert "立即发卡" not in granted
    mid = build_system_skill_registry().get("ask_user_midtask")
    assert mid is not None
    assert "external_mount_readonly" in mid.body or "区外目录" in mid.body
    assert "organize_plan" in mid.body
    assert "consult(external_mount_readonly)" in mid.body
    assert "授权后发现" not in mid.body
    assert "选择器兜底" not in mid.body
    assert "grant_readonly_folder" not in mid.body
    assert "口头同意" not in mid.body
    assert "失败分型" not in mid.body
    assert "well_known" in granted


def test_core_teaches_narrowed_attachment_scope_must_start():
    # 定案 A：用户收窄为本轮附件/工作区已有产物时须先动手。
    # HOW 场面门（同构 cold_start）：常驻核不载全文，仅本回合有附件块 / [resident missing] 时注入。
    hint = _CEO_CORE_HINT
    assert "【本轮材料收窄】" not in hint
    assert "【附件驻留·缺件】" not in hint
    gated = _ATTACHMENT_MATERIAL_HINT
    assert "本轮材料收窄" in gated
    assert "缺口分析" in gated or "改一版" in gated
    assert "open_local_project" not in gated
    assert "退役" not in gated
    assert "[resident missing]" in gated
    assert "先读" in gated and "已给材料" in gated
    assert "重传" not in gated
    assert "ask_user" not in gated
    assert "file_list" not in gated
    assert "[binary]" in gated and "≠ 缺件" in gated
    # 禁止旧契约：用 file_list/exists「证实」路径不在当缺件触发条件。
    assert "file_list / exists 证实" not in gated
    assert "exists 证实" not in gated
    mid = build_system_skill_registry().get("ask_user_midtask")
    assert mid is not None
    assert "先读材料" in mid.body or "收窄本轮" in mid.body
    assert "开工前置" in mid.body

    names = {"consult", "delegate", "ask_user"}
    without = compose_ceo_chat_prompt(
        "BASE",
        ceo_tool_names=names,
        attachment_material=False,
    )
    with_flag = compose_ceo_chat_prompt(
        "BASE",
        ceo_tool_names=names,
        attachment_material=True,
    )
    assert "<attachment_material>" not in without
    assert "<attachment_material>" in with_flag
    assert "【本轮材料收窄】" in with_flag
    assert "[resident missing]" in with_flag
    assert attachment_material_scene("<attached_files>\nfoo\n</attached_files>") is True
    assert attachment_material_scene("--- File: a.zip [resident missing] ---") is True
    assert attachment_material_scene(None) is False
    assert attachment_material_scene("") is False
    assert attachment_material_scene("<agent_mentions/>") is False
    assert attachment_material_scene("<pinned_entries>\n设定\n</pinned_entries>") is False


def test_core_points_to_consult_and_directory():
    # 提示词瘦身 P2: the slim core must point the CEO at consult + the 按需目录
    # so it knows the advanced「怎么做」guidance is pull-on-demand, not missing.
    hint = _CEO_CORE_HINT
    assert "consult" in hint
    assert "按需目录" in hint


def test_core_drops_advanced_mechanism_detail():
    # Regression guard for P2: the rarely-used machinery now lives in system Skills,
    # so its DETAIL must not creep back into the always-on core (that would re-inflate
    # the per-turn prompt). These tokens are unique to the moved-out skill bodies.
    hint = _CEO_CORE_HINT
    # 核心也不点 checkpoint_after（同步阻塞只写嵌套 lead / 成篇套餐提纲把关）。
    for token in (
        "多轮辩论",
        "跨轮",
        "stance",
        "采纳正方",
        "target_run_id",
        "checkpoint_after",
        "complexity_hint",
        "result_handling",
        "require_upstream",
    ):
        assert token not in hint, f"advanced detail '{token}' leaked back into the core"


def test_resident_prompt_has_no_citing_sources_block():
    """#rN 诚实在共享 <delivery_honesty>；不另立 citing 段。队员继承 HOW 在调研 skill。"""
    assert "<citing_sources>" not in _DEFAULT_SYSTEM_PROMPT
    assert "<citing_sources>" not in _CEO_CORE_HINT
    honesty = _DEFAULT_SYSTEM_PROMPT.split("<delivery_honesty>", 1)[1].split(
        "</delivery_honesty>", 1
    )[0]
    assert "#rN" in honesty
    assert "已登记" in honesty
    assert "待核实" in honesty
    assert "搜到 ≠ 可挂来源号" not in _DEFAULT_SYSTEM_PROMPT
    assert "read_url 深读（或已 selected）" not in _DEFAULT_SYSTEM_PROMPT
    assert "综述若继承队员" not in _CEO_CORE_HINT
    research = build_system_skill_registry().get("deep_multi_lens_research").body
    assert "继承" in research and "#rN" in research


def test_memory_rules_fence_blocks_routing_by_topic_preference():
    """M1 教法围栏：题材偏好不得改变本回合路由——钉在平权 ``<rules>`` 模板。"""
    out = assemble_system_prompt(rules_markdown="- 用中文\n- 偏好法律分析\n")
    assert "<rules>" in out
    assert "题材/领域偏好与历史任务" in out
    assert "不得改变本回合路由" in out
    assert "直答/委派/调研/辩论以用户当前话为准" in out
    assert "与本回合用户直接指令冲突时" not in out
    assert "本回合用户直接指令优先于常驻" in out


def test_ceo_core_teaches_memory_must_not_override_routing():
    """M1：核心不再双写路由围栏（唯一所有者=平权 ``<rules>`` 模板）。"""
    hint = _CEO_CORE_HINT
    assert "长期记忆与路由" not in hint
    assert "不得改变本回合" not in hint


def test_ceo_core_teaches_memory_history_user_facing_framing():
    """记忆/历史对外口径 HOW → product_help / faq；派查阅走 delegate，核不写跨会话判决。"""
    hint = _CEO_CORE_HINT
    assert "跨会话原文" not in hint
    assert "查阅员" not in hint
    assert "空口编" not in hint
    help_body = build_system_skill_registry().get("product_help").body
    faq = build_system_skill_registry().get("product_help_faq").body
    assert "记忆/历史·对外口径" in help_body
    assert "禁止报工具名" in help_body or "禁止报工具名与内部角色名" in help_body
    assert "画像细节" in help_body
    assert "能不能读历史对话" in faq or "有没有记忆" in faq


def test_ceo_core_teaches_user_rules_framing():
    """用户规则：载体对照 / 对外口径 HOW → product_help / faq；核不复述百科。"""
    hint = _CEO_CORE_HINT
    assert "【用户规则·载体对照】" not in hint
    assert "硬约束清单" not in hint
    assert "记忆偏好=软" not in hint
    help_body = build_system_skill_registry().get("product_help").body
    faq = build_system_skill_registry().get("product_help_faq").body
    assert "用户规则·对外口径" in help_body
    assert "用户规则·内部" in help_body
    assert "可增" in help_body and "可改" in help_body and "可删" in help_body
    assert "只追加却声称" in help_body
    assert "文件页规则本" in help_body
    assert "平权注入" in help_body
    internal = help_body.split("【用户规则·内部】", 1)[1].split("【用户规则·对外口径】", 1)[0]
    assert "action=" in internal
    assert "replace" in internal and "forget" in internal
    external = help_body.split("【用户规则·对外口径】", 1)[1].split("【", 1)[0]
    assert "action=" not in external
    assert "replace" not in external and "forget" not in external
    assert "你能改规则吗" in faq
    assert "AgentCore/规则/" in faq or "AgentCore/规则/" in help_body
    assert ".mdc" in faq
    assert "remember" in faq or "remember" in help_body


def test_ceo_core_splits_routing_tree_from_acting_tree():
    """how_you_work 答身份级工具边界；何时派在 delegate description；how_you_act 答诚实。不写编号判决树。"""
    from agentcore.tools.builtin.delegate.schema import DELEGATE_DESCRIPTION

    hint = _CEO_CORE_HINT
    assert "<how_you_act>" in hint and "</how_you_act>" in hint
    work = hint.split("<how_you_work>", 1)[1].split("</how_you_work>", 1)[0]
    act = hint.split("<how_you_act>", 1)[1].split("</how_you_act>", 1)[0]
    assert "<platform_knowledge>" not in hint
    assert "只读" in work and "超规模" in work and "团队" in work
    assert "delegate" not in work
    for leaked in ("改文件", "Git 写", "跑测试", "成篇", "闲聊", "自己回", "窗口里已有"):
        assert leaked not in work
        assert leaked not in hint
    assert "改产物" in DELEGATE_DESCRIPTION
    assert "成规模" in DELEGATE_DESCRIPTION
    assert "闲聊" in DELEGATE_DESCRIPTION and "不必派" in DELEGATE_DESCRIPTION
    assert "成规模查证" not in work
    assert "对照本回合结构面" in act
    assert "可见打算" in act
    assert "consult(name)" in act
    for mark in ("①", "②", "③", "④", "⑤", "甲", "戊"):
        assert mark not in hint
    assert "【执行 / 运行 / 打开】" not in work
    assert "【执行 / 运行 / 打开】" not in act


def test_ceo_core_whether_and_headcount_are_separate_owners():
    """编制 / 探路 / 成文细则在编排 skill；核不写 ③④⑤ 判决树。"""
    hint = _CEO_CORE_HINT
    work = hint.split("<how_you_work>", 1)[1].split("</how_you_work>", 1)[0]
    assert "③ 干活默认拉人" not in work
    assert "④ 拉几人" not in work
    assert "⑤ 不要拉人" not in work
    skill = _TEAM_ORCHESTRATION_ADVANCED
    assert "成规模取证" in skill or "编制自选" in skill
    assert "结局分层" in skill or "明示成文" in skill
    assert "点名开辩" in skill or "debate" in skill


def test_ceo_core_gathers_evidence_before_workspace_judgment():
    """工作区课题取证细则在编排 skill；核不写「先取证」判决树。废名缺席。"""
    hint = _CEO_CORE_HINT
    assert "先取证再开口" not in hint
    assert "讨论/判断默认自己答不必查" not in hint
    assert "讨论对齐时读设计文档" not in hint
    assert "用户项目整仓摸底" not in hint
    assert "先不成文 ≠ 自己做完" not in hint


def test_ceo_core_platform_knowledge_two_way_routing():
    """FAQ 百科不常驻；身份在 <role>；检索政策在 skill。"""
    hint = _CEO_CORE_HINT
    assert "<platform_knowledge>" not in hint
    role = hint.split("<role>", 1)[1].split("</role>", 1)[0]
    assert "AgentCore" in role and "Multi-Agent" in role
    assert "https://fashitianxia.xyz" not in role
    assert "我的官网" not in hint
    assert "【品类】" not in hint
    assert "【产品面地图·高频入口】" not in hint
    assert "consult(product_help_map)" not in hint
    assert "【两分路由】" not in hint
    assert "consult(product_help)" not in hint
    assert "consult(product_bug_triage)" not in hint
    assert "四类结论" not in hint
    assert "复现要点" not in hint
    assert "【用户规则·载体对照】" not in hint
    catalog = build_system_skill_registry()
    help_sum = catalog.get("product_help").summary
    faq_sum = catalog.get("product_help_faq").summary
    bug_sum = catalog.get("product_bug_triage").summary
    assert "官网" in help_sum
    assert "product_help_map" not in help_sum
    assert "product_help_faq" not in help_sum
    assert "Cursor" in faq_sum or ".mdc" in help_sum
    assert "故障" in bug_sum or "Bug" in bug_sum
    faq = catalog.get("product_help_faq").body
    assert "AgentCore/规则/" in faq
    assert ".mdc" in faq
    assert "skills/*.json" in faq
    assert "Cursor" in faq and "AgentCore" in faq


def test_ceo_core_teaches_identity_question_answers_our_product_first():
    """身份问走自己答：可见正文用 `<role>` 定位；禁把第三方 Skill 仓当成本项目落地。"""
    hint = _CEO_CORE_HINT
    assert "【身份问·先答我方】" not in hint
    role = hint.split("<role>", 1)[1].split("</role>", 1)[0]
    assert "这是什么项目" in role
    assert "不必先查阅" not in role
    assert "第三方" in role and "Skill" in role
    assert "落地" in role
    worker = compose_worker_base_prompt(assemble_system_prompt())
    assert "这是什么项目" not in worker
    assert "https://fashitianxia.xyz" not in role
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "附件·勿否认" not in orch
    assert "没看到照片" not in hint


def test_core_teaches_closing_does_not_upsell_unrelated_topics():
    """收口勿推销不进常驻检查表；核留对照结构面。行为回归在 closing_posture。"""
    hint = _CEO_CORE_HINT
    assert "【收口·勿推销】" not in hint
    assert "推销" not in hint
    assert "无关题" not in hint
    worker = compose_worker_base_prompt(assemble_system_prompt())
    assert "【收口·勿推销】" not in worker
    assert "推销本轮未点名" not in worker


def test_ceo_core_teaches_existing_tool_results_must_not_be_denied():
    """收口对照已有结果：核留元规则；手册不再堆禁语表。"""
    hint = _CEO_CORE_HINT
    orch = _TEAM_ORCHESTRATION_ADVANCED
    assert "对照本回合结构面" in hint
    assert "工具回执" in hint
    assert "主张对照本回合结构真相" not in hint
    assert "【已有结果·勿否认】" not in orch
    assert "可见症状·勿报已修" not in orch
    worker = compose_worker_base_prompt(assemble_system_prompt())
    assert "【已有结果·勿否认】" not in worker
    assert "没看到照片" not in hint
    assert "没有附带图片" not in hint
    assert "空口说读不了" not in hint


def test_ceo_core_cross_product_rule_paradigm_routing_hook():
    """跨产品规则范式不进常驻核（不是①短问）；HOW 在 product_help / faq。"""
    hint = _CEO_CORE_HINT
    assert "【跨产品规则范式】" not in hint
    work = hint.split("<how_you_work>", 1)[1].split("</how_you_work>", 1)[0]
    assert "Cursor" not in work
    assert ".mdc" not in work
    help_body = build_system_skill_registry().get("product_help").body
    faq = build_system_skill_registry().get("product_help_faq").body
    assert "至多一次窄 list `.cursor/rules`" in help_body or "至多一次窄 list" in help_body
    assert "多轮 list / 通读 `.mdc`" in help_body
    assert "AgentCore/规则/" in faq or "AgentCore/规则/" in help_body
    assert "skills/*.json" in faq or "skills/*.json" in help_body
    assert "skill JSON" in help_body or "skill JSON" in faq
    assert "未钉死目标载体" in faq or "未钉死目标载体" in help_body
    assert "consult(product_help)" in faq or "必查 `product_help`" in faq
    faq_sum = build_system_skill_registry().get("product_help_faq").summary
    assert "Cursor" in faq_sum
    assert "意图分类器" not in hint
    assert "凡写「短问 / `ask_user`」处一律适用" not in hint


def test_ceo_core_teaches_intent_routing_for_adversarial_entry():
    """对抗入口 HOW 在 skill / 目录；核不写判决树。"""
    hint = _CEO_CORE_HINT
    assert "debate_and_review" not in hint
    assert "deep_multi_lens_research" not in hint
    assert "MLR → 命题卡 → 推进卡" not in hint
    assert "庭前取证由辩论机制保证" not in hint
    catalog = build_system_skill_registry()
    debate_sum = catalog.get("debate_and_review").summary
    assert "辩论" in debate_sum
    assert "deep_multi_lens_research" not in debate_sum


def test_ceo_prompt_with_legal_pack_keeps_intent_adversarial_routing():
    """回归钉：含 legal 包时 CEO 系统提示仍可路由对抗入口（核心短牌 + 目录）。"""
    from agentcore.runtime.skills import MULTI_LENS_COURTROOM_TRIGGERS, render_skill_directory

    reg = build_system_skill_registry(include_legal=True)
    tools = {"delegate", "debate", "ask_user", "consult", "web_search"}
    ceo = compose_ceo_chat_prompt(
        assemble_system_prompt(),
        skill_registry=reg,
        ceo_tool_names=tools,
    )
    assert ceo.count("<按需目录>") == 1 and ceo.count("</按需目录>") == 1
    assert "<role>" in ceo and "</role>" in ceo
    assert "<how_you_work>" in ceo and "</how_you_work>" in ceo
    assert "<how_you_act>" in ceo and "</how_you_act>" in ceo
    directory = render_skill_directory(reg, tools)
    assert "deep_multi_lens_research" in directory
    assert "debate_and_review" in directory
    deep_line = next(
        line for line in directory.splitlines() if line.startswith("- deep_multi_lens_research：")
    )
    debate_line = next(
        line for line in directory.splitlines() if line.startswith("- debate_and_review：")
    )
    assert not any(t in deep_line for t in MULTI_LENS_COURTROOM_TRIGGERS)
    deep_body = reg.get("deep_multi_lens_research").body
    assert any(t in deep_body for t in MULTI_LENS_COURTROOM_TRIGGERS)
    assert "deep_multi_lens_research" not in debate_line
    assert "deep_multi_lens_research" in reg.get("debate_and_review").body
    assert "deep_multi_lens_research" in ceo
    assert "debate_and_review" in ceo
    assert "对抗入口" not in _CEO_CORE_HINT
    assert "点名开辩" not in _CEO_CORE_HINT
