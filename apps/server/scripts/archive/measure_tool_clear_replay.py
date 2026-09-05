"""dev 实测（真实回合回放）：从 turn_journal 重建真实长 worker 回合的 LLM 窗口，
对它做 clear_tool_uses 的 A/B —— 比合成场景更有代表性（真实读取分布/大小/工具混合）。

与 measure_tool_clear.py 的区别：那条用合成的等长文件读；本条用 `window_from_journal`
把**真实跑过**的回合（含真实系统提示、真实 web_search/web_fetch/file_read 结果）原样
折回引擎当时喂给模型的 ``list[LLMMessage]``，再投影对比。

只读：不写库、不改任何回合；显式 platform_llm_credentials → build_provider。
跑法（在 apps/server 下）：
  ``uv run python scripts/archive/measure_tool_clear_replay.py --list``         # 列候选回合
  ``uv run python scripts/archive/measure_tool_clear_replay.py``                # 回放默认回合
  ``uv run python scripts/archive/measure_tool_clear_replay.py <turn_id>``      # 指定回合
  ``uv run python scripts/archive/measure_tool_clear_replay.py <turn_id> 6 4``  # 顺带扫阈值
"""

import asyncio
import sys

from sqlalchemy import text

from agentcore.config import settings
from agentcore.core.errors import LLMError
from agentcore.db.base import async_session_factory
from agentcore.db.repositories.runs import TurnJournalRepository
from agentcore.llm.factory import build_provider
from agentcore.llm.provider.protocol import LLMMessage, LLMRequest
from agentcore.runtime.engine.tool_clear import project_cleared_window
from agentcore.runtime.journal import window_from_journal

# 真实「单 run 多大只读结果」回合（探针所得，captain run，可被 window_from_journal 重建）。
DEFAULT_TURN = "a3aefcaf-7868-4a5a-9205-a452c8a37dcc"
# 只读可重取工具（NEVER-approval FILESYSTEM / SEARCH / RESEARCH）= 可清理集。
INVESTIGATION = frozenset({"file_read", "grep", "file_list", "web_search", "web_fetch"})


async def list_candidates() -> None:
    names_sql = ",".join(f"'{n}'" for n in INVESTIGATION)
    async with async_session_factory() as db:
        rows = (await db.execute(text(
            f"select turn_id, payload->>'run_id' as run_id, count(*) as n "
            f"from turn_journal where kind='tool_call' "
            f"and payload->>'name' in ({names_sql}) "
            f"and length(payload->>'result') >= {settings.engine_tool_clear_min_chars} "
            f"group by turn_id, run_id order by n desc limit 12"
        ))).all()
    print("单 run 内「≥min_chars 只读结果」Top（captain run = 无 del_ 前缀，可回放）：")
    for turn_id, run_id, n in rows:
        kind = "captain" if not (run_id or "").startswith("del_") else "worker"
        print(f"  big_reads={n:<3} {kind:<8} turn={turn_id}  run={run_id}")


def make_request(window: list[LLMMessage]) -> LLMRequest:
    """忠实回放：thinking=True（原回合即思考模式，且满足工具回合 reasoning 回传约束），
    tool_choice=none + max_tokens 极小把输出压到最低；input/cache token 计量与生产一致。"""
    return LLMRequest(
        messages=window,
        model="deepseek-v4-flash",
        max_tokens=24,
        tool_choice="none",
        stream=False,
        thinking=True,
        scenario="measure.tool_clear_replay",
    )


def clearable_count(window: list[LLMMessage]) -> int:
    cleared = project_cleared_window(
        window, clearable_tools=INVESTIGATION,
        keep_recent=0, min_chars=settings.engine_tool_clear_min_chars,
    )
    return 0 if cleared is window else sum(1 for a, b in zip(window, cleared, strict=True) if a.content != b.content)


async def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--list":
        await list_candidates()
        return

    turn_id = args[0] if args and not args[0].isdigit() else DEFAULT_TURN
    sweep_args = [a for a in args if a.isdigit()]
    sweep: list[int | None] = [None, *[int(a) for a in sweep_args]] if sweep_args else [
        None, settings.engine_tool_clear_keep_recent
    ]

    async with async_session_factory() as db:
        entries = await TurnJournalRepository(db).load(turn_id)
    if not entries:
        print(f"[skip] turn {turn_id} 无 journal 行（试 --list 看候选）。")
        return

    window = window_from_journal(entries, run_id=None)  # None → 推断 captain run
    if not window:
        print(f"[skip] turn {turn_id} 无法重建 captain 窗口（可能是 worker-only / 旧 display-only journal）。")
        return

    min_chars = settings.engine_tool_clear_min_chars
    n_tool = sum(1 for m in window if m.role == "tool")
    n_clearable = clearable_count(window)
    base_chars = sum(len(m.content or "") for m in window)

    print("=" * 80)
    print("clear_tool_uses · 真实回合回放 A/B（window_from_journal → 真实 DeepSeek）")
    print("=" * 80)
    print(f"turn={turn_id}")
    print(f"窗口：{len(window)} 条消息（{n_tool} 个工具结果，其中 {n_clearable} 个可清理）")
    print(f"原始字符总量：{base_chars:,}  min_chars={min_chars}")
    print("-" * 80)

    from agentcore.llm.resolve import platform_llm_credentials
    creds = platform_llm_credentials()
    if creds is None:
        raise RuntimeError('PLATFORM_API_KEY required (no silent build_provider fallback)')
    provider = build_provider(creds)
    rows: list[tuple[int | None, int, int, int, int, int]] = []
    try:
        for kr in sweep:
            if kr is None:
                proj, n_cleared = window, 0
            else:
                proj = project_cleared_window(
                    window, clearable_tools=INVESTIGATION, keep_recent=kr, min_chars=min_chars
                )
                n_cleared = 0 if proj is window else sum(
                    1 for a, b in zip(window, proj, strict=True) if a.content != b.content
                )
            chars = sum(len(m.content or "") for m in proj)
            await provider.complete(make_request(proj))  # cold（暖缓存）
            warm = (await provider.complete(make_request(proj))).usage  # warm = 稳态
            rows.append((kr, n_cleared, chars, warm.input_tokens, warm.cache_hit_tokens, warm.cache_miss_tokens))
    except LLMError as exc:
        print(f"[error] DeepSeek 调用失败：{exc}")
        return
    finally:
        await provider.close()

    full_input = next((r[3] for r in rows if r[0] is None), 0)
    print(f"{'keep_recent':<12}{'cleared':>8}{'win_chars省':>12}{'warm_input':>12}{'cache_hit':>11}{'cache_miss':>12}{'省 vs full':>13}")
    for kr, n_cleared, chars, inp, hit, miss in rows:
        label = "full" if kr is None else str(kr)
        saved = full_input - inp
        pct = f"{saved / full_input * 100:.1f}%" if full_input else "-"
        cell = "基线" if kr is None else f"{saved:,}/{pct}"
        print(f"{label:<12}{n_cleared:>8}{base_chars - chars:>12,}{inp:>12,}{hit:>11,}{miss:>12,}{cell:>13}")
    print("-" * 80)
    print("真实分布（混合 web_search/web_fetch/file_read，大小不一）下的稳态净收益。")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
