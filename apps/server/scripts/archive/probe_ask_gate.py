"""探针：测「发问优先」路由门——给 CEO 真实系统提示词 + 真实工具 schema，发一条用户消息，
只做【一次】非流式 complete()，看 CEO 的【第一个动作】是什么（ask_user / delegate /
consult_skill / 只读探路 / 直接作答）。

为何这样测：ask_user 只在 live 路径（checkpoint_enabled=True）才装配，离线 eval harness 强制
approvals_enabled=False 关掉它，故 eval 套件测不到「发问门」。本脚本【复用 pipeline 真实装配】
（``_assemble_ceo_toolset`` + ``compose_ceo_chat_prompt``），但不跑 ReAct / 不执行工具、不派
worker——只读模型这一步选了哪个工具，即路由决策本身。便宜（每场景 1 次 LLM 调用）。

从 apps/server 跑::

    uv run python scripts/archive/probe_ask_gate.py
    uv run python scripts/archive/probe_ask_gate.py --samples 2     # 每场景采样 2 次治非确定性
    uv run python scripts/archive/probe_ask_gate.py --mode quality  # 用 Pro 档（更贵）

凭据走 settings.platform_api_key（.env 填 ``PLATFORM_API_KEY``）。仅 dev 探针，无任何旁路。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

from agentcore.core.types import new_id
from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
from agentcore.llm.factory import build_provider
from agentcore.llm.profiles import build_request
from agentcore.llm.provider.protocol import LLMMessage, TokenUsage
from agentcore.runtime.engine.governance import resolve_openai_tool_defs
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline import _assemble_ceo_toolset
from agentcore.runtime.resolve.prompt import assemble_system_prompt, compose_ceo_chat_prompt
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import build_builtin_registry
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

# 只读探路类工具（多轮模式下执行它们、把结果喂回、继续看 CEO 下一步真正的决策）。
_RECON = {"file_read", "file_list", "grep", "web_search", "web_fetch"}
# 决策类工具（命中即 CEO 已做出路由抉择，探针就地记录、不执行）。
_DECISION = {"ask_user", "delegate", "consult_skill", "debate", "replan"}


@dataclass
class Scenario:
    key: str
    user_message: str
    expect: str  # 人读的期望（不是断言，仅对照）


# 三类：① 没说全的产出请求（期望 ASK 开工提案卡）；② 已说全的产出（期望 DELEGATE，不该 ASK）；
# ③ 轻量问答（期望 DIRECT，不该 ASK 也不该 DELEGATE）。
SCENARIOS: list[Scenario] = [
    Scenario("vague_homepage", "帮我做个个人主页网站", "ASK（笼统、受众/内容/技术都没说）"),
    Scenario("vague_todo", "做一个待办事项 App", "ASK（笼统、平台/功能/形态没说）"),
    Scenario("vague_deck", "帮我写一份公司产品介绍的 PPT", "ASK（受众/产品/篇幅没说）"),
    Scenario(
        "spec_webapp",
        "做一个简单的番茄钟网页（HTML + CSS + JavaScript 单页面），支持开始 / 暂停 / 重置，"
        "并把它保存成可直接打开运行的文件。",
        "DELEGATE（已说全，不该 ASK）",
    ),
    Scenario(
        "spec_article",
        "写一篇面向初学者、约 1500 字的科普文，讲什么是向量数据库、解决什么问题、典型应用场景，"
        "用 Markdown 存成文件。",
        "DELEGATE（已说全，不该 ASK）",
    ),
    Scenario("qa_listtuple", "Python 里 list 和 tuple 有什么区别？", "DIRECT（轻量问答）"),
    Scenario(
        "borderline_essay",
        "给我写一篇关于咖啡历史的短文，500 字左右。",
        "校准位（主题+篇幅已给、受众/角度没给——看会不会过度发问）",
    ),
]


def _classify(first_tool: str | None) -> str:
    if first_tool is None:
        return "DIRECT 直接作答"
    if first_tool == "ask_user":
        return "ASK 发问/开工提案卡"
    if first_tool == "delegate":
        return "DELEGATE 委派团队"
    if first_tool == "consult_skill":
        return "CONSULT 查能力(将编排)"
    if first_tool in _RECON:
        return f"RECON 只读探路({first_tool})"
    return f"OTHER {first_tool}"


def _ask_detail(args_json: str) -> str:
    try:
        a = json.loads(args_json or "{}")
    except json.JSONDecodeError:
        return "(args 非法 JSON)"
    qs = a.get("questions") or []
    asm = a.get("assumptions") or []
    so = a.get("style_options") or []
    blocking = a.get("blocking", True)
    return (
        f"questions={len(qs)} assumptions={len(asm)} style_options={len(so)} "
        f"blocking={blocking} | msg: {(a.get('message') or '')[:50]}"
    )


async def _build_ceo_context(mode: str):
    """复用 pipeline 真实装配，返回 (provider, profile, ceo_prompt, tool_defs)。"""
    from agentcore.llm.resolve import platform_llm_credentials
    creds = platform_llm_credentials()
    if creds is None:
        raise RuntimeError('PLATFORM_API_KEY required (no silent build_provider fallback)')
    provider = build_provider(creds)
    profiles = resolve_profile_set(mode, custom_modes={}, ceiling=frozenset(KNOWN_MODELS))
    chat_profile = profiles.get("chat")
    chat_model = profiles.model_for("chat")
    base = assemble_system_prompt()
    skill_registry = build_system_skill_registry()

    backend = ServerWorkspace(root=Path(tempfile.mkdtemp(prefix="probe-")), sandbox=SubprocessSandbox())
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
        checkpoint_enabled=True,  # 关键：装配 ask_user（live 路径）
        message_id=new_id(),
        suspension_saver=None,
        suspension_deleter=None,
        backend_location="local",
        skill_registry=skill_registry,
    )
    ceo_tool_names = set(chat_tools.names)
    ceo_prompt = compose_ceo_chat_prompt(
        base, skill_registry=skill_registry, ceo_tool_names=ceo_tool_names
    )
    tool_defs = resolve_openai_tool_defs(chat_tools, None, set())
    return (
        provider,
        chat_profile,
        chat_model,
        ceo_prompt,
        tool_defs,
        sorted(ceo_tool_names),
        chat_tools,
        ctx,
    )


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
):
    """跑到 CEO 做出真正决策（ask/delegate/consult/direct）为止：只读探路工具就执行、喂回、续跑。"""
    messages = [
        LLMMessage(role="system", content=ceo_prompt),
        LLMMessage(role="user", content=user_message),
    ]
    usage = TokenUsage()
    trail: list[str] = []
    for r in range(max_rounds):
        request = build_request(
            profile, messages, tools=tool_defs, tool_choice="auto", stream=False, model=model
        )
        resp = await provider.complete(request)
        usage = usage + resp.usage
        calls = resp.tool_calls or []
        if not calls:
            detail = "正文: " + (resp.content or "").replace("\n", " ")[:60]
            return _classify(None), detail, trail, r + 1, usage
        decision = [c for c in calls if c.function.name in _DECISION]
        if decision:
            dc = decision[0]
            name = dc.function.name
            detail = (
                _ask_detail(dc.function.arguments)
                if name == "ask_user"
                else (dc.function.arguments or "")[:60]
            )
            return _classify(name), detail, trail, r + 1, usage
        # 本轮全是只读探路：执行后把结果喂回，继续看下一步真正的决策。
        messages.append(LLMMessage(role="assistant", content=resp.content or None, tool_calls=calls))
        for tc in calls:
            trail.append(tc.function.name)
            try:
                tool = chat_tools.get(tc.function.name)
                args = json.loads(tc.function.arguments or "{}")
                tres = await tool.execute(args, ctx)
                out = tres.output if tres.success else f"(error: {tres.error})"
            except Exception as e:  # noqa: BLE001 - 探针容错，执行失败也喂回让 CEO 继续
                out = f"(probe exec error: {e})"
            messages.append(
                LLMMessage(role="tool", tool_call_id=tc.id, content=(out or "(empty)")[:1500])
            )
    return "RECON 未决(达轮上限)", "(仍在探路)", trail, max_rounds, usage


async def main_async(args: argparse.Namespace) -> None:
    selected = SCENARIOS
    if args.keys:
        wanted = {k.strip() for k in args.keys.split(",") if k.strip()}
        selected = [s for s in SCENARIOS if s.key in wanted]
        if not selected:
            raise SystemExit(f"--keys 没匹配到任何场景；可选: {[s.key for s in SCENARIOS]}")

    provider, profile, chat_model, ceo_prompt, tool_defs, names, chat_tools, ctx = (
        await _build_ceo_context(args.mode)
    )
    print(f"模型档: {args.mode}  chat.model={chat_model}  thinking={profile.thinking}  最多 {args.rounds} 轮")
    print(f"CEO 提示词长度: {len(ceo_prompt)} 字符  |  装配工具({len(names)}): {', '.join(names)}")
    print(f"ask_user_kickoff 在能力目录: {'ask_user_kickoff' in ceo_prompt}")
    print("=" * 92)

    tot_in = tot_out = 0
    for sc in selected:
        print(f"\n[{sc.key}] {sc.user_message}")
        print(f"  期望: {sc.expect}")
        for i in range(args.samples):
            cls, detail, trail, rounds, usage = await _run_one(
                provider,
                profile,
                chat_model,
                ceo_prompt,
                tool_defs,
                chat_tools,
                ctx,
                sc.user_message,
                args.rounds,
            )
            tot_in += usage.input_tokens
            tot_out += usage.output_tokens
            tag = f"  -> 第{i + 1}次: " if args.samples > 1 else "  -> 实际: "
            trail_s = " -> ".join(trail) if trail else "(无探路，直接决策)"
            print(f"{tag}{cls}  @第{rounds}轮   [探路链: {trail_s}]")
            if detail:
                print(f"        {detail}")
    print("\n" + "=" * 92)
    print(f"累计 tokens: input={tot_in} output={tot_out}  (调用 {len(selected) * args.samples} 次)")


def main() -> None:
    p = argparse.ArgumentParser(description="探针：CEO 发问优先路由门的第一动作")
    p.add_argument("--mode", default="economy", help="质量档 economy/quality（默认 economy=Flash）")
    p.add_argument("--samples", type=int, default=1, help="每场景采样次数（默认 1）")
    p.add_argument("--keys", default=None, help="只跑指定场景 key（逗号分隔），默认全部")
    p.add_argument("--rounds", type=int, default=3, help="每场景最多跑几轮（执行只读探路续跑，默认 3）")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
