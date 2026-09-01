"""Playbook routing live runner: assemble CEO surface, run until first terminal move.

Credentials: ``eval_credentials()`` — refuses ``source=platform`` unless
``EVAL_DEEPSEEK_API_KEY`` is set. Does not call ``platform_llm_credentials``.
Decision loop lives in ``playbook_routing_decision`` (eval package), not archive scripts.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from agentcore.core.log_context import log_context, new_trace_id
from agentcore.core.types import new_id
from agentcore.evals.eval_modes import KNOWN_MODELS, resolve_profile_set
from agentcore.evals.harness import eval_credentials
from agentcore.evals.playbook_routing import (
    COST_NOTE,
    SCENARIOS,
    PlaybookRoutingRunConfig,
    RoutingScenario,
    aggregate_samples,
    classify_landing,
    diff_fingerprints,
    extract_think_mentions,
    named_playbook,
    select_scenarios,
    think_act_divergences,
)
from agentcore.evals.playbook_routing_decision import run_until_terminal
from agentcore.evals.types import EvalConfigError
from agentcore.llm.factory import build_provider
from agentcore.llm.profiles import TurnProfiles
from agentcore.llm.provider.protocol import LLMMessage
from agentcore.runtime.context import build_workspace_context, collect_outlet_inventory
from agentcore.runtime.engine.governance import resolve_openai_tool_defs
from agentcore.runtime.events import EventSink
from agentcore.runtime.pipeline import _assemble_ceo_toolset
from agentcore.runtime.resolve.prompt import assemble_system_prompt, compose_ceo_chat_prompt
from agentcore.runtime.skills import build_system_skill_registry
from agentcore.tools.builtin import (
    browser_execution_enabled_for,
    build_builtin_registry,
    code_execution_enabled_for,
)
from agentcore.tools.ceo_toolset import wire_ceo_consult
from agentcore.tools.protocol import ToolContext
from agentcore.tools.sandbox.subprocess import SubprocessSandbox
from agentcore.workspace.server import ServerWorkspace

_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_CODEBASE_FIXTURE = _FIXTURES / "playbook_routing_codebase"


def _host_only(url: str) -> str:
    try:
        p = urlparse(url)
        return p.netloc or url
    except Exception:  # noqa: BLE001
        return "(unparsed)"


def _credential_lane() -> str:
    if os.environ.get("EVAL_DEEPSEEK_API_KEY", "").strip():
        return "eval_env"
    return "dev_byok"


def _workspace_stats(root: Path, *, tier: str) -> dict:
    files = [p for p in root.rglob("*") if p.is_file()]
    py_files = [p for p in files if p.suffix == ".py"]
    return {
        "tier": tier,
        "file_count": len(files),
        "py_count": len(py_files),
        "py_bytes": sum(p.stat().st_size for p in py_files),
    }


def _seed_workspace(dest: Path, *, tier: str) -> dict:
    if tier == "codebase":
        if not _CODEBASE_FIXTURE.is_dir():
            raise EvalConfigError(f"codebase 夹具不存在: {_CODEBASE_FIXTURE}")
        shutil.copytree(
            _CODEBASE_FIXTURE,
            dest,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    elif tier != "empty":
        raise EvalConfigError(f"未知 workspace tier: {tier}")
    stats = _workspace_stats(dest, tier=tier)
    if tier == "codebase" and (stats["py_count"] < 10 or stats["py_bytes"] < 8000):
        raise EvalConfigError(
            f"codebase 夹具代码量不够（py_count={stats['py_count']} "
            f"py_bytes={stats['py_bytes']}），会重现空仓假象"
        )
    return stats


def _extract_playbook_surface(tool_defs: list[dict] | None, expect: str) -> dict:
    names: list[str] = []
    delegate = None
    for d in tool_defs or []:
        fn = (d.get("function") or {}) if isinstance(d, dict) else {}
        n = fn.get("name")
        if isinstance(n, str):
            names.append(n)
        if fn.get("name") == "delegate":
            delegate = fn
    props = ((delegate or {}).get("parameters") or {}).get("properties") or {}
    playbook = props.get("playbook") if isinstance(props, dict) else None
    enum = (playbook or {}).get("enum") if isinstance(playbook, dict) else None
    enum_list = list(enum) if isinstance(enum, list) else []
    expect_ok = (not expect) or (expect in enum_list)
    return {
        "delegate_on_surface": delegate is not None,
        "playbook_property_present": isinstance(playbook, dict),
        "playbook_enum": enum_list,
        "expected_in_enum": expect_ok,
        "tool_names": names,
        "offered": bool(delegate is not None and isinstance(playbook, dict) and expect_ok),
    }


async def _build_ceo_context(
    *,
    provider,
    code_execute: bool,
    browser: bool,
    profiles,
    workspace: str,
    root: Path,
):
    skill_registry = build_system_skill_registry()
    stats = _seed_workspace(root, tier=workspace)
    backend = ServerWorkspace(root=root, sandbox=SubprocessSandbox())
    workspace_facts = build_workspace_context(
        backend,
        desktop_online=True,
        run_enabled=code_execute,
        browser_enabled=browser,
        outlet_inventory=await collect_outlet_inventory(backend),
    )
    base = assemble_system_prompt()
    ctx = ToolContext.create(
        execution_id=new_id(),
        run_id=new_id(),
        agent_id="eval-playbook-routing",
        backend=backend,
        user_id="eval-playbook-routing",
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
        desktop_online=True,
    )
    await wire_ceo_consult(
        chat_tools,
        skill_registry=skill_registry,
        folder_id=None,
        user_id="eval-playbook-routing",
    )
    ceo_tool_names = set(chat_tools.names)
    ceo_prompt = compose_ceo_chat_prompt(
        base,
        skill_registry=skill_registry,
        ceo_tool_names=ceo_tool_names,
        workspace_context=workspace_facts,
    )
    tool_defs = resolve_openai_tool_defs(chat_tools, None, set())
    gate_code = (
        code_execute if code_execute is not None else code_execution_enabled_for(backend)
    )
    gate_browser = (
        browser if browser is not None else browser_execution_enabled_for(backend)
    )
    return (
        provider,
        profiles.get("chat"),
        ceo_prompt,
        tool_defs,
        chat_tools,
        ctx,
        gate_code,
        gate_browser,
        stats,
        root,
    )


def _truncate_reasoning(text: str, limit: int = 4000) -> dict:
    if not text:
        return {"full": "", "chars": 0, "truncated": False}
    n = len(text)
    if n <= limit:
        return {"full": text, "chars": n, "truncated": False}
    return {"preview": text[:limit], "chars": n, "truncated": True}


def _pack_sample(
    *,
    sc: RoutingScenario,
    result,
    surface: dict,
    stats: dict,
    trace_id: str,
    attempt: int,
    error: str | None = None,
) -> dict:
    if error:
        return {
            "ok": False,
            "error": error,
            "trace_id": trace_id,
            "attempt": attempt,
            "reruns": attempt,
            "action": "ERROR",
            "delegated": False,
            "card_issued": False,
            "playbook": None,
            "intensity": None,
            "think_act_divergences": [],
            "outcome": {"landing": "error", "note": error},
            "workspace": stats,
        }
    dsum = result.delegate_summary or {}
    playbook = named_playbook(dsum.get("playbook") if isinstance(dsum, dict) else None)
    intensity = None
    forms: list[str] = []
    max_workers = None
    if isinstance(dsum, dict):
        intensity = dsum.get("intensity")
        if not intensity and isinstance(dsum.get("playbook_args"), dict):
            intensity = named_playbook(dsum["playbook_args"].get("intensity"))
        forms = list(dsum.get("forms") or [])
        max_workers = dsum.get("max_workers")
    task_count = int((dsum or {}).get("task_count") or 0) if isinstance(dsum, dict) else 0
    form = None
    if isinstance(dsum, dict):
        form = dsum.get("form")
        if not form and len(set(forms)) == 1:
            form = forms[0]
    action = result.action
    outcome = classify_landing(
        action=action,
        playbook=playbook,
        expect=sc.expect_playbook,
        offered=bool(surface.get("offered")),
        task_count=task_count,
        form=form if isinstance(form, str) else None,
        max_workers=max_workers if isinstance(max_workers, int) else None,
        expect_action=sc.expect_action or None,
        expect_max_workers=sc.expect_max_workers,
        expect_min_workers=sc.expect_min_workers,
        expect_form=sc.expect_form,
        recon_rounds=int(result.recon_rounds or 0),
        expect_max_recon_rounds=sc.expect_max_recon_rounds,
    )
    reasoning = result.reasoning or ""
    mentions = extract_think_mentions(reasoning)
    divergences = think_act_divergences(
        mentions, action=action, playbook=playbook, intensity=intensity
    )
    return {
        "ok": True,
        "error": None,
        "trace_id": trace_id,
        "attempt": attempt,
        "reruns": attempt,
        "action": action,
        "first_action": result.first_action,
        "tool_name": result.tool_name,
        "rounds": result.rounds,
        "recon_rounds": result.recon_rounds,
        "trail": list(result.trail),
        "detour": list(result.detour),
        "delegated": action == "DELEGATE",
        "card_issued": action == "ASK",
        "playbook": playbook,
        "intensity": intensity,
        "task_count": task_count,
        "form": form,
        "forms": forms,
        "max_workers": max_workers,
        "delegate_summary": dsum,
        "outcome": outcome,
        "think_mentions": mentions,
        "think_act_divergences": divergences,
        "detail": result.detail,
        "usage": result.usage,
        "reasoning": _truncate_reasoning(reasoning),
        "workspace": stats,
        "tool_surface": {
            "offered": surface.get("offered"),
            "expected_in_enum": surface.get("expected_in_enum"),
            "playbook_enum": surface.get("playbook_enum"),
        },
    }


async def run_scripted_sample(
    provider,
    sc: RoutingScenario,
    *,
    profiles: TurnProfiles,
    model: str,
    rounds: int,
    attempt: int = 1,
    root: Path | None = None,
) -> dict:
    """Assemble the real CEO tool surface and run one decision.

    Caller supplies ``provider`` (live or stub). Used by the live runner and by
    the execution-entry unit test — module load + arg parse must be real.
    """
    trace_id = new_trace_id()
    own_root = root is None
    ws_root = root or Path(tempfile.mkdtemp(prefix="playbook-routing-"))
    try:
        with log_context(trace_id=trace_id, conversation_id=f"eval-playbook-{sc.key}"):
            bundle = await _build_ceo_context(
                provider=provider,
                code_execute=bool(sc.code_execute),
                browser=bool(sc.browser),
                profiles=profiles,
                workspace=sc.workspace,
                root=ws_root,
            )
            (
                _provider,
                profile,
                ceo_prompt,
                tool_defs,
                chat_tools,
                ctx,
                _gate_code,
                _gate_browser,
                stats,
                _ws_root,
            ) = bundle
            surface = _extract_playbook_surface(tool_defs, sc.expect_playbook)
            history = [
                LLMMessage(role=turn.role, content=turn.content)
                for turn in sc.prior_turns
            ]
            result = await run_until_terminal(
                provider,
                profile,
                model,
                ceo_prompt,
                tool_defs,
                chat_tools,
                ctx,
                sc.user_message,
                rounds,
                history=history or None,
            )
            packed = _pack_sample(
                sc=sc,
                result=result,
                surface=surface,
                stats=stats,
                trace_id=trace_id,
                attempt=attempt,
            )
            packed["prompt_chars"] = len(ceo_prompt)
            return packed
    except Exception as exc:  # noqa: BLE001 — 单样本失败不中断套件
        return _pack_sample(
            sc=sc,
            result=None,
            surface={},
            stats={"tier": sc.workspace},
            trace_id=trace_id,
            attempt=attempt,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
    finally:
        if own_root:
            shutil.rmtree(ws_root, ignore_errors=True)


async def _run_one_attempt(
    sc: RoutingScenario,
    *,
    provider,
    profiles,
    model: str,
    rounds: int,
    quiet: bool,
    attempt: int,
) -> dict:
    _ = quiet
    return await run_scripted_sample(
        provider,
        sc,
        profiles=profiles,
        model=model,
        rounds=rounds,
        attempt=attempt,
    )


async def run_playbook_routing(
    scenarios: list[RoutingScenario] | None = None,
    *,
    config: PlaybookRoutingRunConfig | None = None,
    previous_baseline: dict | None = None,
    keys: str | None = None,
    phrasing: str | None = None,
) -> dict:
    """真跑 LLM。返回报告 dict（恒为观测，不含硬闸）。"""
    cfg = config or PlaybookRoutingRunConfig()
    if cfg.samples < 1:
        raise EvalConfigError("--samples 须 >= 1")
    if cfg.retries < 0:
        raise EvalConfigError("--retries 须 >= 0")
    selected = scenarios or select_scenarios(SCENARIOS, keys=keys, phrasing=phrasing)

    lane = _credential_lane()
    creds = await eval_credentials()
    if creds.source == "platform" and not os.environ.get("EVAL_DEEPSEEK_API_KEY", "").strip():
        raise EvalConfigError(
            "eval_credentials 回落到 PLATFORM_API_KEY。本地 dogfood 禁止默认踩平台 key；"
            "请给 dev 账号配 OpenCode Zen BYOK，或设 EVAL_DEEPSEEK_API_KEY。"
        )

    profiles = resolve_profile_set(
        cfg.mode or "economy", custom_modes={}, ceiling=frozenset(KNOWN_MODELS)
    )
    chat_model = (creds.default_model or "").strip() or profiles.model_for("chat")
    if (creds.default_model or "").strip() and not os.environ.get("EVAL_BASE_MODEL", "").strip():
        profiles = TurnProfiles(
            model=creds.default_model.strip(),
            model_overrides=dict(profiles.model_overrides),
        )
        chat_model = creds.default_model.strip()

    provider = build_provider(creds)

    tot_in = tot_out = 0
    scenario_rows: list[dict] = []
    for sc in selected:
        sample_payloads: list[dict] = []
        for i in range(cfg.samples):
            attempts: list[dict] = []
            last: dict | None = None
            max_tries = 1 + cfg.retries
            for attempt in range(1, max_tries + 1):
                packed = await _run_one_attempt(
                    sc,
                    provider=provider,
                    profiles=profiles,
                    model=chat_model,
                    rounds=cfg.rounds,
                    quiet=cfg.quiet,
                    attempt=attempt,
                )
                attempts.append(packed)
                last = packed
                if packed.get("ok"):
                    break
            assert last is not None
            last = {**last, "attempts": attempts, "reruns": len(attempts)}
            sample_payloads.append(last)
            usage = last.get("usage") or {}
            tot_in += int(usage.get("input_tokens") or 0)
            tot_out += int(usage.get("output_tokens") or 0)
            if not cfg.quiet:
                div = last.get("think_act_divergences") or []
                print(
                    f"[{sc.key}] s{i + 1}/{cfg.samples} "
                    f"action={last.get('action')} playbook={last.get('playbook')!r} "
                    f"intensity={last.get('intensity')!r} "
                    f"card={last.get('card_issued')} "
                    f"landing={(last.get('outcome') or {}).get('landing')} "
                    f"div={len(div)} reruns={last.get('reruns')}"
                )
        agg = aggregate_samples(sample_payloads)
        scenario_rows.append(
            {
                "key": sc.key,
                "phrasing": sc.phrasing,
                "category": sc.category,
                "expect_playbook": sc.expect_playbook,
                "expect_action": sc.expect_action or None,
                "expect_form": sc.expect_form,
                "expect_max_workers": sc.expect_max_workers,
                "expect_min_workers": sc.expect_min_workers,
                "expect_max_recon_rounds": sc.expect_max_recon_rounds,
                "user_message": sc.user_message,
                "workspace": sc.workspace,
                "samples": sample_payloads,
                "aggregate": agg,
                "fingerprint": agg["fingerprint"],
            }
        )

    meta = {
        "timestamp": datetime.now(UTC).isoformat(),
        "gate": False,
        "report_only": True,
        "rerun_policy": "default retries=0; ERROR 才按 --retries 重试，且记下每一次",
        "samples": cfg.samples,
        "rounds": cfg.rounds,
        "retries": cfg.retries,
        "scenario_count": len(selected),
        "mode": cfg.mode,
        "model": chat_model,
        "credential_source": creds.source,
        "credential_lane": lane,
        "base_url_host": _host_only(creds.base_url),
        "tokens": {"input": tot_in, "output": tot_out},
        "cost_note": COST_NOTE,
        "decision_loop": "agentcore.evals.playbook_routing_decision.run_until_terminal",
        "credential_path": "eval_credentials (refuses platform unless EVAL_DEEPSEEK_API_KEY)",
    }
    diff = diff_fingerprints(previous_baseline, scenario_rows)
    return {
        "ok": True,
        "gate": False,
        "meta": meta,
        "scenarios": scenario_rows,
        "diff": diff,
    }


