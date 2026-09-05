"""Bench: run a team eval case N times through the REAL in-process pipeline and report
tool-call / investigation / cost / latency / quality means.

Used to measure the over-investigation fix (system-prompt research discipline + web_fetch
anti-crawl failure guidance + the demoted finalize safety net) against a recorded
baseline — the prompt fixes are global, so there is nothing meaningful to A/B per-arm;
this just characterizes the current code so the numbers can be compared to a prior run.
The harness is the same in-process path the offline suite uses.

Recorded baseline (pre-fix, team_energy_compare, 3 samples) for comparison:
    tool_calls~37.7  investigation~33.0  rounds~2.3  cost~$0.014  latency~140s  content~455

Reusable::

    uv run python scripts/archive/eval_team_bench.py                 # team_energy_compare, 3 samples
    uv run python scripts/archive/eval_team_bench.py --samples 5
    uv run python scripts/archive/eval_team_bench.py --case team_energy_compare

Needs a real LLM key (``PLATFORM_API_KEY`` in .env / ``settings.platform_api_key``, or
``EVAL_DEEPSEEK_API_KEY``) and a
healthy SearXNG (web_search).
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
from collections.abc import Iterable

from agentcore.evals.harness import EvalHarness
from agentcore.evals.runner import load_cases
from agentcore.evals.types import EvalCase, TurnOutcome

# Read-only investigation tools (mirror the engine's category-derived set) — the calls
# the over-investigation fix is meant to curb.
_INVESTIGATION = {"web_search", "web_fetch", "file_read", "file_list", "grep"}


def _find_case(case_id: str) -> EvalCase:
    for suite in ("core", "comparison"):
        try:
            cases = load_cases(suite=suite)
        except Exception:
            continue
        for c in cases:
            if c.id == case_id:
                return c
    raise SystemExit(f"[error] case not found in core/comparison suites: {case_id!r}")


def _investigation_calls(oc: TurnOutcome) -> int:
    return sum(1 for name, _ in oc.tool_calls if name in _INVESTIGATION)


def _tool_breakdown(oc: TurnOutcome) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, _ in oc.tool_calls:
        counts[name] = counts.get(name, 0) + 1
    return counts


def _fmt_breakdown(counts: dict[str, int]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(counts.items(), key=lambda x: -x[1]))


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return statistics.mean(xs) if xs else 0.0


def _summary(outcomes: list[TurnOutcome]) -> dict[str, float]:
    return {
        "tool_calls": _mean(len(o.tool_calls) for o in outcomes),
        "investigation": _mean(_investigation_calls(o) for o in outcomes),
        "rounds": _mean(o.rounds for o in outcomes),
        "cost_usd": _mean(o.cost_usd for o in outcomes),
        "latency_ms": _mean(o.latency_ms for o in outcomes),
        "content_len": _mean(len(o.content) for o in outcomes),
    }


async def main() -> int:
    p = argparse.ArgumentParser(description="team eval case metrics bench")
    p.add_argument("--case", default="team_energy_compare")
    p.add_argument("--samples", type=int, default=3)
    args = p.parse_args()

    case = _find_case(args.case)
    harness = EvalHarness()
    outcomes: list[TurnOutcome] = []
    print(f"BENCH case={case.id!r} path={case.path} mode={case.mode} samples={args.samples}\n")
    for i in range(args.samples):
        oc = await harness.run_case(case)
        outcomes.append(oc)
        print(
            f"  [#{i + 1}] finish={oc.finish_reason} delegated={oc.delegated} "
            f"roster={oc.roster} rounds={oc.rounds} "
            f"tool_calls={len(oc.tool_calls)} (investigation={_investigation_calls(oc)}) "
            f"cost=${oc.cost_usd:.4f} latency={oc.latency_ms}ms content_len={len(oc.content)}"
        )
        print(f"      tools: {_fmt_breakdown(_tool_breakdown(oc)) or '(none)'}")
        if oc.error:
            print(f"      ERROR: {oc.error}")

    s = _summary(outcomes)
    # Per-metric precision: cost is ~$0.01, so 2 decimals would round the signal away.
    fmt = {
        "tool_calls": "{:.2f}",
        "investigation": "{:.2f}",
        "rounds": "{:.2f}",
        "cost_usd": "${:.4f}",
        "latency_ms": "{:.0f}ms",
        "content_len": "{:.0f}",
    }
    print("\n=== MEANS ===")
    for k in ("tool_calls", "investigation", "rounds", "cost_usd", "latency_ms", "content_len"):
        print(f"{k:<14}{fmt[k].format(s[k]):>14}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
