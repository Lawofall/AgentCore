"""探针：Cursor 规则 ↔ AgentCore 用户规则 多角度 E2E（L1 合成夹具）。

跑 ``evals/cases/product_rules/``：歧义迁移 / FAQ 对照 / 钉死载体 / 反陷阱 skill JSON。
工作区夹具 ``cursor_rules_trap`` 故意放诱饵 ``.cursor/rules/*.mdc`` + ``skills/*.json``、
空 ``.agentcore/rules/`` 提示词条目（不在工作区树）——验收真模型是否查 ``product_help*``、禁默迁 skill JSON。

标准套件经 ``EvalHarness``（``approvals_enabled=False``，无 ``ask_user``）。
**额外**：对歧义原句再走 ``run_chat_pipeline(..., approvals_enabled=True)``，观察是否
出现 ``ask_user`` / ``consult_skill``（审批开时才有 ask_user）。

从 apps/server 跑::

    uv run python scripts/archive/probe_product_rules_e2e.py --help
    uv run python scripts/archive/probe_product_rules_e2e.py              # 说明 + 真跑（有 key）
    uv run python scripts/archive/probe_product_rules_e2e.py --suite-only # 只跑 harness 套件
    uv run python scripts/archive/probe_product_rules_e2e.py --approvals-only  # 只跑审批探针

凭据：优先 ``EVAL_DEEPSEEK_API_KEY``；否则本地测试账号（``dev`` / ``DEV_USERNAME``）
已配的 OpenCode Zen BYOK（与桌面 / ``probe_turn`` 同路）；最后才 ``PLATFORM_API_KEY``。
无凭据时只检测并打印启用指引，**不空跑 LLM**。仅 dev 探针；勿挂 CI 硬门。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import tempfile
import time
from pathlib import Path

from agentcore.core.log_context import log_context, new_trace_id
from agentcore.core.types import new_id
from agentcore.evals.checks import build_check
from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
from agentcore.evals.harness import (
    EvalHarness,
    _ms,
    eval_credentials,
    team_outcome,
)
from agentcore.evals.recording_sink import RecordingSink
from agentcore.evals.runner import load_cases
from agentcore.evals.types import EvalCase, TurnOutcome
from agentcore.runtime.pipeline import run_chat_pipeline
from agentcore.runtime.turn.snapshot import ConversationSnapshot
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

_SUITE = "product_rules"
_AMBIGUOUS_MSG = "帮我把cursor规则改成agentcore的规则"
_WATCH = ("consult_skill", "ask_user", "delegate", "write")
_EVAL_USER_ID = "e7a10000-0000-4000-8000-000000000000"
_FIXTURES = Path(__file__).resolve().parents[2] / "agentcore" / "evals" / "fixtures"
_TRAP = "cursor_rules_trap"

_USAGE = """\
Cursor 规则 ↔ AgentCore 用户规则 · E2E 探针（L1 合成，非真实用户数据）

套件：cases/product_rules/（workspace_fixture=cursor_rules_trap）
  1) 歧义原句「改成 agentcore 的规则」→ NotDelegated + consult_skill + 禁迁 skills
  2) FAQ 区别 → 同上 + 正文含 .agentcore/rules
  3) 钉死迁到 .agentcore/rules/ → 允许解释/委派；禁迁 skills
  4) 反陷阱「.mdc 改成 skill JSON」→ 纠偏，禁照做

额外：歧义原句 + approvals_enabled=True → 观察 ask_user / consult_skill

示例：
  uv run python scripts/archive/probe_product_rules_e2e.py --help
  uv run python scripts/archive/probe_product_rules_e2e.py
  uv run python scripts/archive/probe_product_rules_e2e.py --suite-only
  uv run python scripts/archive/probe_product_rules_e2e.py --approvals-only

结构校验（零 LLM）：
  uv run python -m agentcore.evals lint --suite product_rules
"""


async def _has_credentials() -> bool:
    """True when EVAL env, local dev BYOK, or PLATFORM can resolve."""
    try:
        await eval_credentials()
        return True
    except RuntimeError:
        return False


def _print_no_credentials() -> None:
    print("=" * 88)
    print("✗ 没有可用的 LLM 凭据，无法真跑（不会空调 LLM）。")
    print("  优先：本地测试账号 OpenCode Zen BYOK（与桌面 / probe_turn 同路）")
    print("   1) uv run python scripts/seed_dev_user.py   # 默认 dev / devpassword")
    print("   2) 设置页配 OpenCode Zen，或 uv run python scripts/set_dev_llm_key.py")
    print("   3) 可选校验：uv run python scripts/archive/probe_turn.py \"ping\"")
    print("  显式覆盖：导出 EVAL_DEEPSEEK_API_KEY（建议低额度账号）")
    print("  最后才用：apps/server/.env 的 PLATFORM_API_KEY（本地 dogfood 勿默认依赖）")
    print("  结构校验可零 LLM：python -m agentcore.evals lint --suite product_rules")
    print("=" * 88)


def _print_tool_chain(outcome: TurnOutcome, *, watch: tuple[str, ...] = _WATCH) -> None:
    print("-" * 88)
    print("工具调用链:")
    if not outcome.tool_calls:
        print("  (无)")
        return
    for name, args in outcome.tool_calls:
        mark = "  ★" if name in watch else "   "
        snippet = (args or "").replace("\n", " ")[:140]
        print(f"{mark} {name}  {snippet}")


def _run_checks(case: EvalCase, outcome: TurnOutcome) -> list[tuple[str, bool, str]]:
    rows: list[tuple[str, bool, str]] = []
    for spec in case.checks:
        check = build_check(spec)
        co = check.run(case, outcome)
        rows.append((co.name, co.passed, co.detail))
    return rows


def _print_checks(case_id: str, rows: list[tuple[str, bool, str]]) -> bool:
    all_ok = True
    print("-" * 88)
    print(f"Checks [{case_id}]:")
    for name, passed, detail in rows:
        flag = "PASS" if passed else "FAIL"
        if not passed:
            all_ok = False
        print(f"  [{flag}] {name}: {detail}")
    return all_ok


def _print_outcome_header(label: str, outcome: TurnOutcome) -> None:
    print("=" * 88)
    print(f"【{label}】")
    print(f"finish_reason : {outcome.finish_reason}")
    print(f"rounds        : {outcome.rounds}")
    print(f"delegated     : {outcome.delegated}")
    print(f"roster        : {outcome.roster}")
    print(f"cost_usd      : {outcome.cost_usd:.4f}")
    if outcome.error:
        print(f"error         : {outcome.error}")


async def _run_suite() -> bool:
    cases = load_cases(suite=_SUITE)
    harness = EvalHarness()
    print(f"套件 {_SUITE!r}：{len(cases)} 例（harness approvals_enabled=False）")
    all_pass = True
    for case in cases:
        print("\n" + "#" * 88)
        print(f"case={case.id}  msg={case.user_message!r}")
        outcome = await harness.run_case(case)
        _print_outcome_header(case.id, outcome)
        _print_tool_chain(outcome)
        rows = _run_checks(case, outcome)
        ok = _print_checks(case.id, rows)
        print("-" * 88)
        print("正文预览:")
        content = (outcome.content or "").strip()
        print((content[:800] + ("…" if len(content) > 800 else "")) or "(空)")
        if not ok:
            all_pass = False
    return all_pass


async def _run_approvals_probe() -> None:
    """歧义原句 + 开审批：观察 ask_user / consult_skill（标准 harness 关审批无 ask_user）。"""
    src = _FIXTURES / _TRAP
    if not src.is_dir():
        raise SystemExit(f"夹具不存在: {src}")
    dest = Path(tempfile.mkdtemp(prefix="agentcore-probe-rules-"))
    shutil.copytree(src, dest, dirs_exist_ok=True)
    backend = ServerWorkspace(root=dest, sandbox=SubprocessSandbox())
    sink = RecordingSink()
    creds = await eval_credentials()
    profiles = resolve_profile_set("economy", custom_modes={}, ceiling=frozenset(KNOWN_MODELS))
    # Same as EvalHarness: account default (often deepseek-v4-flash-free) unless EVAL_BASE_MODEL.
    if (creds.default_model or "").strip() and not os.environ.get("EVAL_BASE_MODEL", "").strip():
        from agentcore.llm.profiles import TurnProfiles

        profiles = TurnProfiles(
            model=creds.default_model.strip(),
            model_overrides=dict(profiles.model_overrides),
        )
    t0 = time.monotonic()
    print("\n" + "#" * 88)
    print("【额外·审批开】歧义原句 approvals_enabled=True")
    print(f"msg={_AMBIGUOUS_MSG!r}")
    print(f"model={profiles.model!r} base={creds.base_url!r}")
    with log_context(trace_id=new_trace_id(), user_id=_EVAL_USER_ID, case="product_rules_approvals"):
        result = await run_chat_pipeline(
            conversation_id=new_id(),
            user_message=_AMBIGUOUS_MSG,
            history=[],
            sink=sink,
            user_id=_EVAL_USER_ID,
            backend=backend,
            conversation=ConversationSnapshot.default(),
            approvals_enabled=True,
            profile_set=profiles,
            llm_credentials=creds,
        )
    outcome = team_outcome(result, sink, latency_ms=_ms(t0), workspace_root=str(dest))
    _print_outcome_header("approvals_probe", outcome)
    _print_tool_chain(outcome)
    called = {name for name, _ in outcome.tool_calls}
    print("-" * 88)
    print("审批探针观测（非硬门断言，供人眼对照）:")
    print(f"  consult_skill : {'consult_skill' in called}")
    print(f"  ask_user      : {'ask_user' in called}")
    print(f"  delegate      : {'delegate' in called}")
    print(f"  finish_reason : {outcome.finish_reason!r}（ask_user 挂起常见 paused）")
    print("-" * 88)
    print("正文预览:")
    content = (outcome.content or "").strip()
    print((content[:800] + ("…" if len(content) > 800 else "")) or "(空)")
    print("=" * 88)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="probe_product_rules_e2e.py",
        description=(
            "Cursor↔AgentCore 用户规则 E2E 探针：跑 product_rules 套件 + "
            "歧义原句审批开观测（L1 合成夹具 cursor_rules_trap）。"
        ),
        epilog=_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument(
        "--suite-only",
        action="store_true",
        help="只跑 EvalHarness 套件（approvals 关）",
    )
    g.add_argument(
        "--approvals-only",
        action="store_true",
        help="只跑歧义原句 approvals_enabled=True 观测",
    )
    return p


async def main_async(args: argparse.Namespace) -> int:
    print(_USAGE)
    if not await _has_credentials():
        _print_no_credentials()
        return 2

    # 预检凭据可解析，避免中途才炸
    await eval_credentials()

    suite_ok = True
    run_suite = not args.approvals_only
    run_approvals = not args.suite_only

    if run_suite:
        suite_ok = await _run_suite()
        print("\n" + "=" * 88)
        print(f"套件汇总: {'PASS' if suite_ok else 'FAIL'}")
        print("=" * 88)

    if run_approvals:
        await _run_approvals_probe()

    return 0 if suite_ok else 1


def main() -> None:
    parser = _build_parser()
    # 无参：打印说明后真跑（有 key）；--help 由 argparse 处理
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
