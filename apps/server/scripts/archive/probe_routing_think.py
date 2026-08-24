"""探针：测 CEO「是否/如何委派」的决策 + 思维链是否打转。

装配路径与 ``probe_ask_gate.py`` 同构（真实 ``compose_ceo_chat_prompt`` +
``_assemble_ceo_toolset``，``checkpoint_enabled=True``），不跑完整 ReAct worker；
只读探路与 ``consult_skill`` 会执行续跑，直到问你 / 派人 / 开辩 / 直答。

三层记账：
- **终向**：最终落到 ASK / DIRECT / DELEGATE / DEBATE 是否符合期望
- **直达**：终向对、且中间没有先 ``consult_skill`` 绕说明书
- **打转**：只看「第一下行动前」的思考启发式 flags（与对错分开）；开卡 / 闸后纯「长」降权不计入门禁

成功线（报告对照）：终向 ≥80%、直达 ≥60%（在终向对的样本内）、打转 ≤15%。

从 apps/server 跑::

    uv run python scripts/archive/probe_routing_think.py
    uv run python scripts/archive/probe_routing_think.py --samples 2
    uv run python scripts/archive/probe_routing_think.py --keys vague_website,design_to_api --quiet

凭据走 settings.platform_api_key（.env 填 ``PLATFORM_API_KEY``）。仅 dev 探针。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentcore.core.types import new_id
from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
from agentcore.llm.factory import build_provider
from agentcore.llm.profiles import build_request
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.context import build_workspace_context
from agentcore.runtime.engine.governance import (
    LOCAL_RECON_TOOLS,
    create_loop_controller,
    resolve_openai_tool_defs,
)
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline import _assemble_ceo_toolset
from agentcore.runtime.resolve.prompt import assemble_system_prompt, compose_ceo_chat_prompt
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_builtin_registry
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

_RECON = {"file_read", "file_list", "grep", "web_search", "read_url", "git"}
# 终向决策：到此记账结束。consult_skill 不算终向——执行后继续看下一步。
_TERMINAL = frozenset({"ask_user", "delegate", "debate", "replan"})
_DECISION = _TERMINAL | {"consult_skill"}

# 软桶 expect：报告单独统计，不算硬错
_SOFT_EXPECTS = frozenset({"CALIBRATE", "BIND_OR_ASK", "DEBATE_OR_DELEGATE", "ASK_OR_DELEGATE"})
# 具名 playbook 展开后才有 tasks；探针只看 call args，故用 playbook 名当「多人」代理
_MULTI_PLAYBOOKS = frozenset({"research_report", "multi_lens_research", "compare_options"})

# 成功线（报告对照，非硬退出码）
_GATE_TERMINAL = 0.80
_GATE_DIRECT = 0.60  # 分母=终向对的样本
_GATE_SPIN = 0.15

_JARGON_TERMS = ("直答", "委派", "门槛", "finalize", "质量面", "组队", "探路")
_DIRECT_CUE = re.compile(r"直答|自己答|直接回答|不委派|不必委派|无需委派")
_DELEGATE_CUE = re.compile(r"委派|组队|delegate|拆给|派给|交给.?worker|交给.?团队")
_CODE_BLOCK = re.compile(r"```[\s\S]{40,}```")
_STEP_HEAVY = re.compile(
    r"(?:步骤\s*[1-9]|第[一二三四五六七八九十\d]+步|TODO:|def\s+\w+\(|class\s+\w+|function\s+\w+)"
)


@dataclass
class Scenario:
    key: str
    bucket: str  # DIRECT / ASK / DELEGATE / DELEGATE_MULTI / CALIBRATE / BIND_OR_ASK / …
    user_message: str
    expect: str  # DIRECT | ASK | DELEGATE | DELEGATE_MULTI | DELEGATE_SOLO | CALIBRATE | …
    # 覆盖生产能力行（None=按 backend 谓词；探针默认可显式钉死缺能力 / 有能力路径）
    code_execute: bool | None = None
    browser: bool | None = None


SCENARIOS: list[Scenario] = [
    # ── DIRECT ×7 ──────────────────────────────────────────────
    Scenario("greet", "DIRECT", "你好呀，今天怎么样？", "DIRECT"),
    Scenario("thanks", "DIRECT", "谢谢你刚才帮我理清思路，辛苦了。", "DIRECT"),
    Scenario(
        "one_liner_concept",
        "DIRECT",
        "用一句话解释什么是幂等性（idempotency）。",
        "DIRECT",
    ),
    Scenario(
        "two_concept_diff",
        "DIRECT",
        "Python 里 list 和 tuple 有什么区别？",
        "DIRECT",
    ),
    Scenario(
        "short_error_meaning",
        "DIRECT",
        "终端里出现 `ModuleNotFoundError: No module named 'requests'`，这是什么意思？",
        "DIRECT",
    ),
    Scenario(
        "casual_followup",
        "DIRECT",
        "哈哈懂了。那你平时更喜欢咖啡还是茶？",
        "DIRECT",
    ),
    Scenario(
        "essay_no_file_direct",
        "DIRECT",
        "用三四百字聊聊为什么开源许可证要分清 MIT 和 GPL，不用写成文件，直接回复就行。",
        "DIRECT",
    ),
    # ── ASK ×5 ────────────────────────────────────────────────
    Scenario("vague_website", "ASK", "帮我做个网站吧", "ASK"),
    Scenario("vague_app", "ASK", "我想做一个 App，你帮我弄一下", "ASK"),
    Scenario("vague_ppt", "ASK", "帮我写一份公司产品介绍的 PPT", "ASK"),
    Scenario("vague_system", "ASK", "帮我搭一个内部管理系统", "ASK"),
    Scenario("vague_report", "ASK", "写一份行业分析报告给我", "ASK"),
    # ── DELEGATE ×10 ──────────────────────────────────────────
    Scenario(
        "spec_pomodoro",
        "DELEGATE",
        "做一个简单的番茄钟网页（HTML + CSS + JavaScript 单页面），支持开始 / 暂停 / 重置，"
        "并把它保存成可直接打开运行的文件。",
        "DELEGATE",
    ),
    Scenario(
        "spec_md_article",
        "DELEGATE",
        "写一篇面向初学者、约 1500 字的科普文，讲什么是向量数据库、解决什么问题、典型应用场景，"
        "用 Markdown 存成文件。",
        "DELEGATE",
    ),
    Scenario(
        "readme_tweak",
        "DELEGATE",
        "帮我改一下项目根目录的 README.md：在最上面加一小节「快速开始」，写三条安装命令，"
        "其余内容别动。",
        "DELEGATE",
    ),
    Scenario(
        "paste_code_bug",
        "DELEGATE",
        "下面这段 Python 跑起来报错，帮我找出坑并给出能跑的修复版，改完写回同文件：\n\n"
        "```python\ndef avg(nums):\n    return sum(nums) / len(nums)\n\n"
        "print(avg([]))\n```",
        "DELEGATE",
    ),
    Scenario(
        "multi_file_refactor",
        "DELEGATE",
        "把 utils/ 下的 format_date.py、format_money.py、format_phone.py 三个小工具合并成一个 "
        "utils/formatters.py，并更新所有 import，保持行为不变。",
        "DELEGATE",
    ),
    Scenario(
        "landing_3_styles",
        "ASK_OR_DELEGATE",
        "给我做一页产品落地页，要三种风格可选（极简白、深色科技、温暖手绘），"
        "点名用 HTML/CSS，单文件落地，交互只要导航锚点跳转。",
        "ASK_OR_DELEGATE",  # 中性：三份vs切换/产品未说清可问；勿先 consult
    ),
    Scenario(
        "competitor_abc",
        "DELEGATE",
        "调研一下 Notion、Obsidian、Logseq 三家在个人知识管理上的定位差异，"
        "整理成一份 Markdown 对比表（功能、定价、适合谁），落盘到 AgentCore/文档/research/km-compare.md。",
        "DELEGATE",
    ),
    Scenario(
        "design_to_api",
        "DELEGATE",
        "先根据「待办事项」画一版简洁 REST API 设计（资源、动词、状态码），"
        "再按设计实现一份 FastAPI 骨架，路由要能跑起来，代码放 api/todos/。",
        "DELEGATE",
    ),
    Scenario(
        "tiny_config_line",
        "DELEGATE",
        "把配置文件 config.yaml 里的 `debug: false` 改成 `debug: true`，只改这一行，别动别的。",
        "DELEGATE",
    ),
    Scenario(
        "short_essay_file",
        "DELEGATE",
        "写一篇约 800 字的短文《为什么本地优先软件又火了》，Markdown 存成 essays/local-first.md。",
        "DELEGATE",
    ),
    # ── DELEGATE_MULTI ×4 ─────────────────────────────────────
    Scenario(
        "db_triple_compare",
        "DELEGATE_MULTI",
        "帮我系统对比 PostgreSQL、MySQL、SQLite 三者在中小型 Web 项目里的取舍"
        "（性能、运维、生态、适用场景），最后给一份选型建议，写成 Markdown。",
        "DELEGATE_MULTI",
    ),
    Scenario(
        "cross_domain_synth",
        "ASK_OR_DELEGATE",
        "我想做「AI 会议纪要」方向：一边调研市面三款产品，一边给一段 Python 录音转写样例代码，"
        "再请人点评一下产品交互设计，最后合成一篇完整 Markdown 报告落盘。",
        "ASK_OR_DELEGATE",  # 中性：未点名品牌可开卡带默认或派时写明自选；勿先 consult
    ),
    Scenario(
        "multi_stage_site",
        "ASK_OR_DELEGATE",
        "帮我建一个个人作品集站：先定信息架构和文案大纲，再出视觉风格板，"
        "最后用 HTML/CSS/JS 实现首页+作品列表两页，分阶段推进。",
        "ASK_OR_DELEGATE",  # 阶段已齐可直派；缺主题/占位内容先开卡也算过
    ),
    Scenario(
        "parallel_tech_pick",
        "DELEGATE_MULTI",
        "我们要给内部工具选前端栈，请并行从「上手成本」「生态」「长期维护」三个角度"
        "分别评估 React、Vue、Svelte，再汇总一份选型备忘录。",
        "DELEGATE_MULTI",
    ),
    # 成篇调研报告软偏好：明示落盘成文 → 应多角/research_report，勿一人包办
    Scenario(
        "research_mid_lawsuit",
        "DELEGATE_MULTI",
        "写一篇关于起诉第三者如何才能立案的实务研究，婚姻家事领域，实务指南，"
        "中等篇幅 4000–6000 字，Markdown 落盘。"
        "请覆盖立案要件、证据与证明、管辖与诉讼地位等可独立取证的角度。",
        "DELEGATE_MULTI",
    ),
    # A 档对齐推进：多路摸清未提成文 → 应并行摸底，勿一人、勿误判为直答
    Scenario(
        "align_brief_multi_angle",
        "DELEGATE_MULTI",
        "帮我把「开源协议选型」这件事理清：从许可证兼容、商业闭源风险、社区生态三个方向"
        "多 Agent 并行摸底，先对齐结论，先不要写成正式报告。",
        "DELEGATE_MULTI",
    ),
    # 材料已齐的中篇扩写：应单写手，勿被调研流水线吸走
    Scenario(
        "research_materials_ready",
        "DELEGATE_SOLO",
        "工作区 AgentCore/文档/research/notes.md 已整理好要点与法条摘录。请据此写成约 4000 字实务指南"
        "落盘为 report.md，不要再联网检索，材料已齐只扩写。",
        "DELEGATE_SOLO",
    ),
    # ── CALIBRATE ×3 ──────────────────────────────────────────
    Scenario(
        "calib_500_words",
        "CALIBRATE",
        "给我写一篇关于咖啡历史的短文，500 字左右。",
        "CALIBRATE",
    ),
    Scenario(
        "calib_quick_rewrite",
        "CALIBRATE",
        "把下面这句话改得更口语一点，并顺便译成自然的英文：\n"
        "「协作，是更高级的智能。」",
        "CALIBRATE",
    ),
    Scenario(
        "calib_half_spec_todo",
        "CALIBRATE",
        "做一个待办 App，平台先做 Web，功能就增删改查和勾选完成，样式随便简洁点就行。",
        "CALIBRATE",
    ),
    # ── BIND_OR_ASK ×2（缺执行/浏览器 → 能力策略收口 ASK）────────────────
    Scenario(
        "bind_run_script",
        "BIND_OR_ASK",
        "帮我跑一下 scripts/smoke_test.py，看看报什么错，有问题就修。",
        "BIND_OR_ASK",
        code_execute=False,
        browser=False,
    ),
    Scenario(
        "bind_open_verify",
        "BIND_OR_ASK",
        "刚才做好的那个页面，你能在本地直接打开浏览器帮我验证一下能不能用吗？",
        "BIND_OR_ASK",
        code_execute=False,
        browser=False,
    ),
    # 有执行能力路径：跑脚本 → 能力策略收口 DELEGATE
    Scenario(
        "run_script_with_exec",
        "DELEGATE",
        "帮我跑一下 scripts/smoke_test.py，看看报什么错，有问题就修。",
        "DELEGATE",
        code_execute=True,
        browser=False,
    ),
    # ── DEBATE_OR_DELEGATE ×2 ─────────────────────────────────
    Scenario(
        "debate_redis_vs_memcached",
        "DEBATE_OR_DELEGATE",
        "我们缓存层要在 Redis 和 Memcached 里拍板，两边都有人坚持，帮我吵清楚再给结论。",
        "DEBATE_OR_DELEGATE",
    ),
    Scenario(
        "debate_microservices",
        "DEBATE_OR_DELEGATE",
        "团队在吵要不要上微服务：一边说拆了才好扩，一边说单体更稳。帮我组织正反方把利弊吵透。",
        "DEBATE_OR_DELEGATE",
    ),
]


def _truncate_reasoning(text: str, limit: int = 4000) -> dict[str, object]:
    """落盘用：短则全文，长则前 limit 字 + 总长。"""
    if not text:
        return {"full": "", "chars": 0, "truncated": False}
    n = len(text)
    if n <= limit:
        return {"full": text, "chars": n, "truncated": False}
    return {"preview": text[:limit], "chars": n, "truncated": True}


def _print_reasoning(text: str, *, quiet: bool, limit: int = 4000) -> None:
    if quiet:
        print(f"        reasoning: {len(text)} 字")
        return
    n = len(text)
    body = text if n <= limit else text[:limit] + f"\n…（截断，共 {n} 字）"
    print("        ── reasoning ──")
    for line in body.splitlines() or [""]:
        print(f"        {line}")
    print("        ── /reasoning ──")


def _classify_action(first_tool: str | None) -> str:
    if first_tool is None:
        return "DIRECT"
    if first_tool == "ask_user":
        return "ASK"
    if first_tool == "delegate":
        return "DELEGATE"
    if first_tool == "consult_skill":
        return "CONSULT"
    if first_tool == "debate":
        return "DEBATE"
    if first_tool in _RECON:
        return "RECON_UNDECIDED"
    return "OTHER"


def _looks_like_proxy_answer(task_text: str) -> bool:
    """启发式：task 里塞了大段实现步骤/代码骨架 → 代答方案 FLAG。"""
    if not task_text:
        return False
    if len(task_text) > 1200:
        return True
    if _CODE_BLOCK.search(task_text):
        return True
    return len(_STEP_HEAVY.findall(task_text)) >= 3


def _parse_delegate(args_json: str) -> dict[str, object]:
    try:
        a = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return {
            "task_count": 0,
            "roles": [],
            "finalize": None,
            "playbook": None,
            "proxy_answer_flag": False,
            "parse_error": True,
        }
    tasks = a.get("tasks") or []
    if not isinstance(tasks, list):
        tasks = []
    roles: list[str] = []
    proxy = False
    for t in tasks:
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or "").strip()
        if role:
            roles.append(role)
        blob = " ".join(
            str(t.get(k) or "") for k in ("task", "objective") if t.get(k)
        )
        if _looks_like_proxy_answer(blob):
            proxy = True
    return {
        "task_count": len(tasks),
        "roles": roles,
        "finalize": a.get("finalize"),
        "playbook": a.get("playbook") or a.get("playbook_id"),
        "proxy_answer_flag": proxy,
        "parse_error": False,
    }


_SPIN_FLAG_NAMES = ("长", "翻转", "术语堆", "自检违规")


def _spin_flags(reasoning: str) -> list[str]:
    """思维链打转启发式：命中记 flags（应对「第一动前」思考文本调用）。"""
    flags: list[str] = []
    if not reasoning:
        return flags
    if len(reasoning) > 800:
        flags.append("长")

    # 翻转：同段里「直答」与「委派」对立推演 ≥2 次（按句/行粗切，看交替出现）
    flips = 0
    last: str | None = None
    for chunk in re.split(r"[。！？\n；;]", reasoning):
        chunk = chunk.strip()
        if not chunk:
            continue
        has_d = bool(_DIRECT_CUE.search(chunk))
        has_g = bool(_DELEGATE_CUE.search(chunk))
        if has_d and has_g:
            flips += 1
            last = "both"
        elif has_d:
            if last == "delegate":
                flips += 1
            last = "direct"
        elif has_g:
            if last == "direct":
                flips += 1
            last = "delegate"
    if flips >= 2:
        flags.append("翻转")

    jargon_hits = sum(1 for t in _JARGON_TERMS if t in reasoning)
    if jargon_hits >= 4:
        flags.append("术语堆")

    # 自检违规：明显多段路由推演（多句同时谈直答/委派/门槛），而非一句话定夺
    route_sentences = 0
    for chunk in re.split(r"[。！？\n]", reasoning):
        c = chunk.strip()
        if not c:
            continue
        if any(t in c for t in ("直答", "委派", "门槛", "组队", "finalize", "探路", "ASK", "delegate")):
            route_sentences += 1
    if route_sentences >= 4:
        flags.append("自检违规")

    return flags


def _spin_counts_for_gate(
    flags: list[str],
    *,
    action: str,
    first_action: str,
    trail: list[str],
) -> bool:
    """是否计入打转门禁。

    定案 A：只盯第一动前思考的 flags；以下降权、不计入 spin_rate（flags 仍保留诊断）：
    - 纯「长」且终向/首动为开卡
    - 能力/组队闸已触发后的 ASK/DIRECT 收口（含自检违规等，属闸后说明而非路由打转）
    """
    spinish = [f for f in flags if f in _SPIN_FLAG_NAMES]
    if not spinish:
        return False
    act = (action or "").upper()
    fa = (first_action or "").upper()
    only_long = set(spinish) <= {"长"}
    return not (
        only_long and (act == "ASK" or fa == "ASK" or fa.startswith("ASK"))
    )


def _expect_match(expect: str, action: str, delegate_summary: dict[str, object] | None) -> str:
    """返回 matched / soft_ok / mismatch / soft_mismatch。"""
    soft = expect in _SOFT_EXPECTS
    if expect == "DIRECT":
        ok = action == "DIRECT"
    elif expect == "ASK":
        ok = action == "ASK"
    elif expect == "DELEGATE":
        ok = action == "DELEGATE"
    elif expect == "DELEGATE_MULTI":
        n = int((delegate_summary or {}).get("task_count") or 0)
        pb = (delegate_summary or {}).get("playbook")
        multi_pb = isinstance(pb, str) and pb in _MULTI_PLAYBOOKS
        ok = action == "DELEGATE" and (n >= 2 or multi_pb)
    elif expect == "DELEGATE_SOLO":
        n = int((delegate_summary or {}).get("task_count") or 0)
        pb = (delegate_summary or {}).get("playbook")
        multi_pb = isinstance(pb, str) and pb in _MULTI_PLAYBOOKS
        # 单人：≤1 task 且未套多人 playbook；0 task 且无 playbook 视为未成形 → 不过
        ok = action == "DELEGATE" and not multi_pb and n == 1
    elif expect == "CALIBRATE":
        ok = action in {"DIRECT", "ASK", "DELEGATE"}
    elif expect == "BIND_OR_ASK":
        ok = action in {"ASK", "DELEGATE", "CONSULT"}
    elif expect == "DEBATE_OR_DELEGATE":
        ok = action in {"DEBATE", "DELEGATE"}
    elif expect == "ASK_OR_DELEGATE":
        # 中性问派：允许 ASK 或 DELEGATE；先 consult 不算过
        ok = action in {"ASK", "DELEGATE"}
    else:
        ok = False

    if soft:
        return "soft_ok" if ok else "soft_mismatch"
    return "matched" if ok else "mismatch"


async def _build_ceo_context(
    mode: str,
    *,
    code_execute: bool | None = None,
    browser: bool | None = None,
):
    """复用 pipeline 真实装配，返回 (provider, profile, model, ceo_prompt, tool_defs, …)。

    ``workspace_context`` 与 prepare 同形注入；``code_execute`` / ``browser`` 覆盖能力行
    （测缺能力 ASK / 有能力 DELEGATE 分叉）。
    """
    from agentcore.llm.resolve import platform_llm_credentials
    creds = platform_llm_credentials()
    if creds is None:
        raise RuntimeError('PLATFORM_API_KEY required (no silent build_provider fallback)')
    provider = build_provider(creds)
    profiles = resolve_profile_set(mode, custom_modes={}, ceiling=frozenset(KNOWN_MODELS))
    chat_profile = profiles.get("chat")
    chat_model = profiles.model_for("chat")
    skill_registry = build_system_skill_registry()

    root = Path(tempfile.mkdtemp(prefix="probe-routing-"))
    _seed_probe_workspace(root)
    backend = ServerWorkspace(root=root, sandbox=SubprocessSandbox())
    # 与 prepare 同形：显式 workspace_context（能力行可覆盖，供 BIND / 有执行分叉）
    workspace_facts = build_workspace_context(
        backend,
        desktop_online=True,
        code_execute_enabled=code_execute,
        browser_enabled=browser,
    )
    base = assemble_system_prompt()
    ctx = ToolContext.create(
        execution_id=new_id(), run_id=new_id(), agent_id="probe", backend=backend, user_id="probe"
    )
    _delegate, _debate, chat_tools = _assemble_ceo_toolset(
        llm=provider,
        sink=EventSink(),
        base_system_prompt=base,
        user_message="",
        history=[],
        worker_tools=build_builtin_registry(),
        base_tool_context=ctx,
        profiles=profiles,
        approval_gate=None,
        session_store=None,
        session_saver=None,
        session_loader=None,
        conversation_id=new_id(),
        captain_run_id=new_id(),
        checkpoint_enabled=True,
        message_id=new_id(),
        suspension_saver=None,
        suspension_deleter=None,
        backend_location="local",
        skill_registry=skill_registry,
    )
    ceo_tool_names = set(chat_tools.names)
    ceo_prompt = compose_ceo_chat_prompt(
        base,
        skill_registry=skill_registry,
        ceo_tool_names=ceo_tool_names,
        workspace_context=workspace_facts,
    )
    tool_defs = resolve_openai_tool_defs(chat_tools, None, set())
    # 闸用能力：覆盖优先，否则跟 backend 谓词（与 workspace 行一致）
    from agentcore.tools.builtin import (
        browser_execution_enabled_for,
        code_execution_enabled_for,
    )

    gate_code = (
        code_execute if code_execute is not None else code_execution_enabled_for(backend)
    )
    gate_browser = (
        browser if browser is not None else browser_execution_enabled_for(backend)
    )
    return (
        provider,
        chat_profile,
        chat_model,
        ceo_prompt,
        tool_defs,
        sorted(ceo_tool_names),
        chat_tools,
        ctx,
        gate_code,
        gate_browser,
    )


def _seed_probe_workspace(root: Path) -> None:
    """给改文件类场景预置假文件，避免空仓探路偏置。"""
    (root / "README.md").write_text(
        "# Demo Project\n\n## 说明\n\n已有正文，探针勿整篇覆盖。\n",
        encoding="utf-8",
    )
    (root / "config.yaml").write_text("debug: false\napp: demo\n", encoding="utf-8")
    utils = root / "utils"
    utils.mkdir(parents=True, exist_ok=True)
    for name in ("format_date", "format_money", "format_phone"):
        (utils / f"{name}.py").write_text(
            f'"""tiny helper."""\n\ndef {name}(value):\n    return value\n',
            encoding="utf-8",
        )
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "smoke_test.py").write_text("raise SystemExit('boom')\n", encoding="utf-8")
    (root / "avg.py").write_text(
        "def avg(nums):\n    return sum(nums) / len(nums)\n\nprint(avg([]))\n",
        encoding="utf-8",
    )
    research = root / "AgentCore" / "文档" / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "notes.md").write_text(
        "# 已整理要点\n\n- 立案登记制要点\n- 案由与管辖摘录\n- 证据清单草稿\n",
        encoding="utf-8",
    )


@dataclass
class SampleResult:
    sample_index: int
    action: str  # 终向动作
    tool_name: str | None
    rounds: int
    trail: list[str]
    reasoning: str
    reasoning_meta: dict[str, object]
    flags: list[str]
    delegate_summary: dict[str, object] | None = None
    detail: str = ""
    match: str = ""  # 终向 vs expect
    usage: dict[str, int] = field(default_factory=dict)
    first_action: str = ""
    detour: list[str] = field(default_factory=list)  # consult 过的 skill 名
    direct: bool = True  # 终向前未 consult
    terminal_ok: bool = False


async def _feed_tool_calls(
    messages, calls, chat_tools, ctx, trail: list[str], *, assistant_content: str | None = None
) -> None:
    """把本轮 tool calls 执行结果喂回 messages（探路或 consult）。"""
    messages.append(
        LLMMessage(role="assistant", content=assistant_content or None, tool_calls=calls)
    )
    for tc in calls:
        trail.append(tc.function.name)
        try:
            tool = chat_tools.get(tc.function.name)
            args = json.loads(tc.function.arguments or "{}")
            tres = await tool.execute(args, ctx)
            out = tres.output if tres.success else f"(error: {tres.error})"
        except Exception as e:  # noqa: BLE001 - 探针容错
            out = f"(probe exec error: {e})"
        # consult_skill 正文可能较长，多留一点
        cap = 6000 if tc.function.name == "consult_skill" else 1500
        messages.append(
            LLMMessage(role="tool", tool_call_id=tc.id, content=(out or "(empty)")[:cap])
        )


def _consult_names(calls) -> list[str]:
    names: list[str] = []
    for tc in calls:
        if tc.function.name != "consult_skill":
            continue
        try:
            a = json.loads(tc.function.arguments or "{}")
            n = str(a.get("name") or "").strip() or "(unnamed)"
        except json.JSONDecodeError:
            n = "(bad_args)"
        names.append(n)
    return names


async def _run_one(
    provider,
    profile,
    model: str,
    ceo_prompt: str,
    tool_defs,
    chat_tools,
    ctx,
    user_message: str,
    max_rounds: int,
    *,
    quiet: bool,
    code_execute: bool = False,
    browser: bool = False,
) -> SampleResult:
    """跑到终向决策；consult_skill / 只读探路会执行续跑。"""
    messages = [
        LLMMessage(role="system", content=ceo_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    usage = TokenUsage()
    trail: list[str] = []
    detour: list[str] = []
    first_action = ""
    last_reasoning = ""
    pre_action_reasoning = ""
    pre_action_frozen = False
    # 与生产 CEO 环同步：探路累计≥5 一律硬收调查工具
    # （成篇意图另追加形状句；本地改文件 → 摸仓≥2 独立硬催派）
    inv_tools = frozenset(_RECON)
    gate_controller = create_loop_controller(inv_tools)
    live_tool_defs = tool_defs
    # code_execute / browser 仍由调用方注入 workspace 上下文；引擎不再做 exec_verify 硬闸。
    _ = (code_execute, browser)

    def _freeze_pre_action(reasoning: str) -> None:
        nonlocal pre_action_reasoning, pre_action_frozen
        if pre_action_frozen:
            return
        text = (reasoning or "").strip() or last_reasoning
        pre_action_reasoning = text
        pre_action_frozen = True

    def _pack(
        action: str,
        *,
        tool_name: str | None,
        rounds: int,
        detail: str,
        delegate_summary: dict[str, object] | None = None,
        flags: list[str] | None = None,
    ) -> SampleResult:
        if not pre_action_frozen:
            _freeze_pre_action(last_reasoning)
        spin_text = pre_action_reasoning or last_reasoning
        fl = flags if flags is not None else _spin_flags(spin_text)
        _print_reasoning(spin_text, quiet=quiet)
        fa = first_action or action
        return SampleResult(
            sample_index=0,
            action=action,
            tool_name=tool_name,
            rounds=rounds,
            trail=list(trail),
            reasoning=spin_text,
            reasoning_meta={
                **_truncate_reasoning(spin_text),
                "spin_window": "pre_first_action",
                "spin_counts": _spin_counts_for_gate(
                    fl, action=action, first_action=fa, trail=trail
                ),
            },
            flags=fl,
            delegate_summary=delegate_summary,
            detail=detail,
            usage={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
            first_action=fa,
            detour=list(detour),
            direct=not detour,
        )

    for r in range(max_rounds):
        request = build_request(
            profile,
            messages,
            tools=live_tool_defs,
            tool_choice="auto",
            stream=False,
            model=model,
        )
        resp = await provider.complete(request)
        usage = usage + resp.usage
        reasoning = resp.reasoning_content or ""
        if reasoning:
            last_reasoning = reasoning

        calls = resp.tool_calls or []
        if not calls:
            if not first_action:
                first_action = "DIRECT"
            _freeze_pre_action(reasoning or last_reasoning)
            return _pack(
                "DIRECT",
                tool_name=None,
                rounds=r + 1,
                detail="正文: " + (resp.content or "").replace("\n", " ")[:80],
            )

        # 第一动（任一工具）当轮思考冻结为打转窗口
        _freeze_pre_action(reasoning or last_reasoning)
        terminal = [c for c in calls if c.function.name in _TERMINAL]
        consults = [c for c in calls if c.function.name == "consult_skill"]
        recon_only = (
            not terminal
            and not consults
            and all(c.function.name in _RECON for c in calls)
        )

        if terminal:
            dc = terminal[0]
            name = dc.function.name
            action = _classify_action(name)
            if not first_action:
                first_action = action
            flags = _spin_flags(pre_action_reasoning or last_reasoning)
            delegate_summary = None
            detail = ""
            if name == "delegate":
                try:
                    dargs = json.loads(dc.function.arguments or "{}")
                except json.JSONDecodeError:
                    dargs = {}
                if not isinstance(dargs, dict):
                    dargs = {}
                # 部分模型会把 tasks 序列化成 JSON 字符串——先摊平再过闸。
                raw_tasks = dargs.get("tasks")
                if isinstance(raw_tasks, str):
                    try:
                        parsed_tasks = json.loads(raw_tasks)
                    except json.JSONDecodeError:
                        parsed_tasks = None
                    if isinstance(parsed_tasks, list):
                        dargs = {**dargs, "tasks": parsed_tasks}
                # named_entity_fanout 用户扫硬拒已移除；点名对比扇出靠提示词。
                delegate_summary = _parse_delegate(json.dumps(dargs, ensure_ascii=False))
                if delegate_summary.get("proxy_answer_flag"):
                    flags = [*flags, "代答方案"]
                detail = (
                    f"tasks={delegate_summary['task_count']} "
                    f"roles={delegate_summary['roles']} "
                    f"finalize={delegate_summary['finalize']}"
                )
            elif name == "ask_user":
                try:
                    a = json.loads(dc.function.arguments or "{}")
                    qs = a.get("questions") or []
                    detail = f"questions={len(qs)} | {(a.get('message') or '')[:50]}"
                except json.JSONDecodeError:
                    detail = "(ask args 非法 JSON)"
            else:
                detail = (dc.function.arguments or "")[:80]
            if detour:
                detail = (detail + f" | detour={detour}").strip(" |")
            return _pack(
                action,
                tool_name=name,
                rounds=r + 1,
                detail=detail,
                delegate_summary=delegate_summary,
                flags=flags,
            )

        if consults:
            names = _consult_names(consults)
            if not first_action:
                first_action = "CONSULT"
            detour.extend(names)
            # 若同轮还夹了只读，一并执行
            await _feed_tool_calls(
                messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
            )
            continue

        if recon_only:
            if not first_action:
                first_action = f"RECON({calls[0].function.name})"
            await _feed_tool_calls(
                messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
            )
            for tc in calls:
                name = tc.function.name
                if name in inv_tools:
                    gate_controller._investigation_calls += 1
                    if name in LOCAL_RECON_TOOLS:
                        gate_controller._local_recon_calls += 1
            continue

        # 其它工具：也喂回继续，避免卡死
        if not first_action:
            first_action = _classify_action(calls[0].function.name)
        await _feed_tool_calls(
            messages, calls, chat_tools, ctx, trail, assistant_content=resp.content
        )
        for tc in calls:
            name = tc.function.name
            if name in inv_tools:
                gate_controller._investigation_calls += 1
                if name in LOCAL_RECON_TOOLS:
                    gate_controller._local_recon_calls += 1
        continue

    return _pack(
        "RECON_UNDECIDED",
        tool_name=None,
        rounds=max_rounds,
        detail=f"(未决，达轮上限) detour={detour}" if detour else "(仍在探路，达轮上限)",
    )


def _default_out_path() -> Path:
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return Path("eval-out") / f"routing-think-{ts}.json"


def _confusion_overview(rows: list[dict[str, object]]) -> dict[str, object]:
    """三层记账：终向 / 直达 / 打转；附成功线对照。"""
    hard: dict[str, dict[str, int]] = {}
    soft_stats: dict[str, object] = {"soft_ok": 0, "soft_mismatch": 0, "by_expect": {}}
    hard_matched = hard_mismatch = 0
    spin_hit = 0
    spin_raw_hit = 0
    spin_excluded = 0
    terminal_ok_n = 0
    direct_ok_n = 0
    detour_n = 0

    for row in rows:
        expect = str(row["expect"])
        action = str(row["action"])
        match = str(row["match"])
        flags = list(row.get("flags") or [])
        terminal_ok = bool(row.get("terminal_ok"))
        direct = bool(row.get("direct"))
        detour = list(row.get("detour") or [])
        trail = list(row.get("trail") or [])
        first_action = str(row.get("first_action") or "")

        raw = any(f in flags for f in _SPIN_FLAG_NAMES)
        if raw:
            spin_raw_hit += 1
        counted = _spin_counts_for_gate(
            flags, action=action, first_action=first_action, trail=trail
        )
        # 兼容旧 JSON：若已写入 spin_counts 则以之为准
        meta = row.get("reasoning_meta")
        if isinstance(meta, dict) and "spin_counts" in meta:
            counted = bool(meta["spin_counts"])
        if counted:
            spin_hit += 1
        elif raw:
            spin_excluded += 1
        if terminal_ok:
            terminal_ok_n += 1
            if direct:
                direct_ok_n += 1
        if detour:
            detour_n += 1

        if expect in _SOFT_EXPECTS:
            soft_stats[match] = int(soft_stats.get(match, 0) or 0) + 1
            by = soft_stats["by_expect"]
            assert isinstance(by, dict)
            slot = by.setdefault(expect, {"soft_ok": 0, "soft_mismatch": 0, "actions": {}})
            slot[match] = slot.get(match, 0) + 1
            acts = slot["actions"]
            acts[action] = acts.get(action, 0) + 1
        else:
            cell = hard.setdefault(expect, {})
            cell[action] = cell.get(action, 0) + 1
            if match == "matched":
                hard_matched += 1
            else:
                hard_mismatch += 1

    total = len(rows) or 1
    terminal_rate = round(terminal_ok_n / total, 3)
    # 直达率：分母=终向对的样本（无终向对时记 0）
    direct_rate = round(direct_ok_n / terminal_ok_n, 3) if terminal_ok_n else 0.0
    spin_rate = round(spin_hit / total, 3)
    spin_raw_rate = round(spin_raw_hit / total, 3)

    return {
        "hard_matrix": hard,
        "hard_matched": hard_matched,
        "hard_mismatch": hard_mismatch,
        "soft": soft_stats,
        "layers": {
            "terminal_ok": terminal_ok_n,
            "terminal_rate": terminal_rate,
            "direct_ok": direct_ok_n,
            "direct_rate": direct_rate,
            "detour_hits": detour_n,
            "spin_hits": spin_hit,
            "spin_rate": spin_rate,
            "spin_raw_hits": spin_raw_hit,
            "spin_raw_rate": spin_raw_rate,
            "spin_excluded": spin_excluded,
            "spin_metric": "pre_first_action_v1",
            "total_samples": len(rows),
        },
        "gates": {
            "terminal": _GATE_TERMINAL,
            "direct": _GATE_DIRECT,
            "spin": _GATE_SPIN,
            "terminal_pass": terminal_rate >= _GATE_TERMINAL,
            "direct_pass": direct_rate >= _GATE_DIRECT,
            "spin_pass": spin_rate <= _GATE_SPIN,
        },
        # 兼容旧字段
        "spin_rate": spin_rate,
        "spin_hits": spin_hit,
        "total_samples": len(rows),
    }


async def main_async(args: argparse.Namespace) -> None:
    selected = SCENARIOS
    if args.keys:
        wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
        selected = [s for s in SCENARIOS if s.key in wanted]
        if not selected:
            raise SystemExit(
                f"--keys 没匹配到任何场景；可选: {[s.key for s in SCENARIOS]}"
            )

    out_path = Path(args.out) if args.out else _default_out_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # 默认能力行：云端缺执行/浏览器（与多数 BIND 场景对齐）；有覆盖的场景按需重建。
    (
        provider,
        profile,
        chat_model,
        default_ceo_prompt,
        default_tool_defs,
        names,
        default_chat_tools,
        default_ctx,
        default_gate_code,
        default_gate_browser,
    ) = await _build_ceo_context(args.mode, code_execute=False, browser=False)
    if not args.quiet:
        assert "<workspace_context>" in default_ceo_prompt
        print(
            f"模型档: {args.mode}  chat.model={chat_model}  "
            f"thinking={profile.thinking}  最多 {args.rounds} 轮"
        )
        print(f"CEO 提示词长度: {len(default_ceo_prompt)} 字符  |  装配工具({len(names)}): {', '.join(names)}")
        print(f"场景数: {len(selected)}  每场景采样: {args.samples}  输出: {out_path}")
        print("=" * 96)

    scenario_rows: list[dict[str, object]] = []
    flat_for_confusion: list[dict[str, object]] = []
    tot_in = tot_out = 0
    # 按能力覆盖缓存 (code_execute, browser) → context bundle
    ctx_cache: dict[tuple[bool, bool], tuple] = {
        (False, False): (
            default_ceo_prompt,
            default_tool_defs,
            default_chat_tools,
            default_ctx,
            default_gate_code,
            default_gate_browser,
        )
    }

    for sc in selected:
        run_code = False if sc.code_execute is None else bool(sc.code_execute)
        run_browser = False if sc.browser is None else bool(sc.browser)
        caps_key = (run_code, run_browser)
        if caps_key not in ctx_cache:
            (
                _p,
                _pr,
                _m,
                ceo_prompt,
                tool_defs,
                _n,
                chat_tools,
                ctx,
                gate_code,
                gate_browser,
            ) = await _build_ceo_context(
                args.mode,
                code_execute=run_code,
                browser=run_browser,
            )
            ctx_cache[caps_key] = (
                ceo_prompt,
                tool_defs,
                chat_tools,
                ctx,
                gate_code,
                gate_browser,
            )
        ceo_prompt, tool_defs, chat_tools, ctx, run_code, run_browser = ctx_cache[caps_key]

        sample_payloads: list[dict[str, object]] = []
        for i in range(args.samples):
            try:
                result = await _run_one(
                    provider,
                    profile,
                    chat_model,
                    ceo_prompt,
                    tool_defs,
                    chat_tools,
                    ctx,
                    sc.user_message,
                    args.rounds,
                    quiet=args.quiet,
                    code_execute=run_code,
                    browser=run_browser,
                )
            except Exception as exc:  # noqa: BLE001 — 探针容错，单场景上游失败不整跑中断
                result = SampleResult(
                    sample_index=i + 1,
                    action="ERROR",
                    tool_name=None,
                    rounds=0,
                    trail=[],
                    reasoning="",
                    reasoning_meta={"full": "", "chars": 0, "truncated": False},
                    flags=[],
                    detail=f"{type(exc).__name__}: {exc}"[:200],
                    usage={},
                )
            result.sample_index = i + 1
            result.match = _expect_match(sc.expect, result.action, result.delegate_summary)
            result.terminal_ok = result.match in {"matched", "soft_ok"}
            # 直达：终向对且未 consult 绕路
            result.direct = result.terminal_ok and not result.detour
            tot_in += result.usage.get("input_tokens", 0)
            tot_out += result.usage.get("output_tokens", 0)

            # 一行摘要：终向 + 直达 + flags
            flag_s = ",".join(result.flags) if result.flags else "-"
            trail_s = "->".join(result.trail) if result.trail else "-"
            detour_s = ",".join(result.detour) if result.detour else "-"
            tag = f"s{i + 1}" if args.samples > 1 else "  "
            direct_s = "直达" if result.direct else ("绕路" if result.detour else "未直达")
            print(
                f"[{sc.key}]{tag} expect={sc.expect} final={result.action} "
                f"first={result.first_action or '-'} {direct_s} "
                f"terminal={'ok' if result.terminal_ok else 'no'} "
                f"flags=[{flag_s}] rounds={result.rounds} "
                f"detour=[{detour_s}] trail={trail_s}"
            )
            if result.detail and (not args.quiet or result.action == "ERROR"):
                print(f"        {result.detail}")

            payload = {
                "sample_index": result.sample_index,
                "action": result.action,
                "first_action": result.first_action,
                "detour": result.detour,
                "direct": result.direct,
                "terminal_ok": result.terminal_ok,
                "tool_name": result.tool_name,
                "rounds": result.rounds,
                "trail": result.trail,
                "reasoning": result.reasoning_meta,
                "flags": result.flags,
                "delegate_summary": result.delegate_summary,
                "detail": result.detail,
                "match": result.match,
                "usage": result.usage,
            }
            sample_payloads.append(payload)
            flat_for_confusion.append(
                {
                    "key": sc.key,
                    "expect": sc.expect,
                    "action": result.action,
                    "match": result.match,
                    "flags": result.flags,
                    "direct": result.direct,
                    "terminal_ok": result.terminal_ok,
                    "detour": result.detour,
                    "first_action": result.first_action,
                    "trail": result.trail,
                }
            )

        scenario_rows.append(
            {
                "key": sc.key,
                "bucket": sc.bucket,
                "expect": sc.expect,
                "user_message": sc.user_message,
                "samples": sample_payloads,
                # 方便消费：首样本顶层展开
                "action": sample_payloads[0]["action"],
                "first_action": sample_payloads[0].get("first_action"),
                "detour": sample_payloads[0].get("detour"),
                "direct": sample_payloads[0].get("direct"),
                "terminal_ok": sample_payloads[0].get("terminal_ok"),
                "reasoning": sample_payloads[0]["reasoning"],
                "flags": sample_payloads[0]["flags"],
                "delegate_summary": sample_payloads[0]["delegate_summary"],
                "match": sample_payloads[0]["match"],
            }
        )

    overview = _confusion_overview(flat_for_confusion)
    report = {
        "meta": {
            "mode": args.mode,
            "model": chat_model,
            "thinking": getattr(profile, "thinking", None),
            "rounds": args.rounds,
            "samples": args.samples,
            "scenario_count": len(selected),
            "timestamp": datetime.now(UTC).isoformat(),
            "tokens": {"input": tot_in, "output": tot_out},
            "accounting": "terminal/direct/spin",
            "gates": {
                "terminal": _GATE_TERMINAL,
                "direct": _GATE_DIRECT,
                "spin": _GATE_SPIN,
            },
        },
        "scenarios": scenario_rows,
        "overview": overview,
    }
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    layers = overview["layers"]
    gates = overview["gates"]
    print("=" * 96)
    print("混淆概览（硬桶 expect→终向 action）:")
    for expect, cells in sorted(overview["hard_matrix"].items()):
        parts = [f"{a}={n}" for a, n in sorted(cells.items())]
        print(f"  {expect}: {', '.join(parts)}")
    print(
        f"硬桶终向: matched={overview['hard_matched']} mismatch={overview['hard_mismatch']}  |  "
        f"软桶: soft_ok={overview['soft'].get('soft_ok', 0)} "
        f"soft_mismatch={overview['soft'].get('soft_mismatch', 0)}"
    )
    print(
        f"终向命中: {layers['terminal_ok']}/{layers['total_samples']} "
        f"= {layers['terminal_rate']:.1%}  "
        f"（门禁 ≥{_GATE_TERMINAL:.0%} → {'过' if gates['terminal_pass'] else '未过'}）"
    )
    print(
        f"直达率: {layers['direct_ok']}/{layers['terminal_ok'] or 0} "
        f"= {layers['direct_rate']:.1%}  "
        f"（门禁 ≥{_GATE_DIRECT:.0%}·分母=终向对 → {'过' if gates['direct_pass'] else '未过'}；"
        f"绕路 {layers['detour_hits']} 次）"
    )
    print(
        f"打转率: {layers['spin_hits']}/{layers['total_samples']} "
        f"= {layers['spin_rate']:.1%}  "
        f"（门禁 ≤{_GATE_SPIN:.0%} → {'过' if gates['spin_pass'] else '未过'}；"
        f"原始 {layers.get('spin_raw_hits', layers['spin_hits'])}/"
        f"{layers['total_samples']}={layers.get('spin_raw_rate', layers['spin_rate']):.1%}，"
        f"开卡/闸后长想降权 {layers.get('spin_excluded', 0)}）"
    )
    print(f"累计 tokens: input={tot_in} output={tot_out}")
    print(f"已写入: {out_path.resolve()}")


def main() -> None:
    p = argparse.ArgumentParser(description="探针：CEO 委派终向/直达/打转三层记账")
    p.add_argument("--mode", default="economy", help="质量档 economy/quality（默认 economy）")
    p.add_argument("--samples", type=int, default=1, help="每场景采样次数（默认 1）")
    p.add_argument("--keys", default=None, help="只跑指定场景 key（逗号分隔），默认全部")
    p.add_argument(
        "--rounds",
        type=int,
        default=5,
        help="每场景最多轮数（探路+consult 续跑，默认 5）",
    )
    p.add_argument(
        "--out",
        default=None,
        help="JSON 输出路径（默认 apps/server/eval-out/routing-think-{timestamp}.json）",
    )
    p.add_argument("--quiet", action="store_true", help="少打正文/思考全文，仍写 JSON")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
