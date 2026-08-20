"""Playbook 路由回归（报告型，不卡门禁）.

把「普通人怎么说 → CEO 落到哪」变成可复跑观测：是否派团队、``playbook`` /
``intensity``、是否发卡、思考里点名却没做（think/act 分歧）。教科书措辞是对照
基线（线通不通）；口语措辞才是真实说法到不到。

真跑 LLM（dev BYOK / ``EVAL_DEEPSEEK_*``），禁止默认踩 ``PLATFORM_API_KEY``，
不许 mock 模型。默认每场景 3 次采样，输出分布而不是一次快照。落盘基线后
下次跑出「哪些场景的落点变了」。

从 ``apps/server``::

    uv run python -m agentcore.evals --playbook-routing
    uv run python -m agentcore.evals --playbook-routing --lint-only
    uv run python -m agentcore.evals --playbook-routing --samples 5
        --keys audit_check_bugs_save_file

花费量级（2026-08-20 手搓 9 场景 × 1 采样、economy / deepseek-v4-flash）：全套约
43 万 input / 0.9 万 output tokens，审计场景因探路最贵。默认 ``samples=3`` 约
×3；``samples=5`` 约 ×5。免费档连跑可能撞小时限流，宜等不宜硬重试。
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentcore.evals.types import EvalConfigError
from agentcore.runtime.runs.playbooks import PLAYBOOKS

_CODEBASE_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "playbook_routing_codebase"

DEFAULT_SAMPLES = 3
DEFAULT_ROUNDS = 6
DEFAULT_RETRIES = 0

# 口语场景禁止出现的提示词术语 / playbook 名（对照基线可以有，用来区分线断了还是说法到不了）。
_COLLOQUIAL_BAN = frozenset(
    {
        "playbook",
        "intensity",
        "模块流水线",
        "绿场",
        "spa",
        "多 agent",
        "多agent",
        "finalize",
        "consult_skill",
        "ask_user",
        "delegate",
        "落盘",
        "代码审计",
        *PLAYBOOKS.keys(),
    }
)

_INTENSITIES = frozenset({"lean", "full", "solo", "standard"})
_NONE_PLAYBOOK = frozenset({"", "none"})

_STRONG_PLAYBOOK = re.compile(
    r"playbook\s*=\s*[\"']?(?P<eq>[a-z][a-z0-9_]*)"
    r"|playbook\s*[\"'](?P<quoted>[a-z][a-z0-9_]*)[\"']"
    r"|用\s+(?P<use_pb>[a-z][a-z0-9_]*)\s+playbook"
    r"|派\s+(?P<pai>[a-z][a-z0-9_]*)"
    r"|delegate\s+(?P<dtool>[a-z][a-z0-9_]*)",
    re.IGNORECASE,
)
_STRONG_INTENSITY = re.compile(
    r"intensity\s*=\s*[\"']?(?P<v>lean|full|solo|standard)[\"']?",
    re.IGNORECASE,
)
_STRONG_ASK = re.compile(r"(?:调用|调|用)\s*ask_user|发卡")
_NEG_TAIL = re.compile(r"(?:不要用|不(?:要|用|再|必)|勿|禁(?:止)?|别|非)\s*$")


@dataclass(frozen=True)
class RoutingScenario:
    key: str
    phrasing: str  # textbook | colloquial
    category: str
    expect_playbook: str
    user_message: str
    workspace: str  # empty | codebase
    code_execute: bool = False
    browser: bool = False


# 教科书措辞 = 提示词触发句的对照基线（允许含内部用语）。口语 = 这次手搓原句，禁止改措辞追结果。
SCENARIOS: tuple[RoutingScenario, ...] = (
    RoutingScenario(
        key="research_brief_parallel",
        phrasing="textbook",
        category="research_brief",
        expect_playbook="parallel_brief",
        user_message=(
            "帮我调研一下开源协议选型，多 Agent 对比摸清许可证兼容和商业闭源风险，"
            "先不要写成正式报告。"
        ),
        workspace="empty",
    ),
    RoutingScenario(
        key="code_audit_report",
        phrasing="textbook",
        category="code_audit",
        expect_playbook="code_audit",
        user_message="请对当前工作区做一次代码审计，找出 bug，并把审计报告落盘。",
        workspace="codebase",
    ),
    RoutingScenario(
        key="greenfield_spa_build_app",
        phrasing="textbook",
        category="greenfield_app",
        expect_playbook="build_app",
        user_message="帮我从零做一个完整可跑的 SPA 待办应用，模块流水线一次做完。",
        workspace="empty",
        code_execute=True,
    ),
    RoutingScenario(
        key="research_mit_vs_gpl_chat",
        phrasing="colloquial",
        category="research_brief",
        expect_playbook="parallel_brief",
        user_message=(
            "我们公司项目准备开源，我纠结该用 MIT 还是 GPL，"
            "你帮我把各自限制和风险讲清楚就行，先别写成文档。"
        ),
        workspace="empty",
    ),
    RoutingScenario(
        key="research_knowledge_base_chat",
        phrasing="colloquial",
        category="research_brief",
        expect_playbook="parallel_brief",
        user_message=(
            "我想先搞明白现在做个人知识库的几个主流产品和我们差在哪，"
            "先不用出报告，跟我对着聊。"
        ),
        workspace="empty",
    ),
    RoutingScenario(
        key="audit_check_bugs_save_file",
        phrasing="colloquial",
        category="code_audit",
        expect_playbook="code_audit",
        user_message=(
            "帮我把这个项目好好检查一遍，看看有没有明显的 bug，"
            "最后写一份检查报告存成文件给我。"
        ),
        workspace="codebase",
    ),
    RoutingScenario(
        key="audit_find_issues_workspace_doc",
        phrasing="colloquial",
        category="code_audit",
        expect_playbook="code_audit",
        user_message=(
            "这堆代码我不太放心，你帮忙找找问题，"
            "整理成文档放到工作区里，我之后还要看。"
        ),
        workspace="codebase",
    ),
    RoutingScenario(
        key="app_todo_website_usable",
        phrasing="colloquial",
        category="greenfield_app",
        expect_playbook="build_app",
        user_message="我想从零做一个待办清单网站，打开就能用，能加任务、勾掉、删除。",
        workspace="empty",
        code_execute=True,
    ),
    RoutingScenario(
        key="app_todo_web_must_run",
        phrasing="colloquial",
        category="greenfield_app",
        expect_playbook="build_app",
        user_message=(
            "帮我做一个全新的网页待办应用，登录、列表、勾选完成都要有，"
            "做完我要能真的跑起来用，别只给个静态页。"
        ),
        workspace="empty",
        code_execute=True,
    ),
)


def named_playbook(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text.lower() in _NONE_PLAYBOOK:
        return None
    return text


def parse_delegate_rich(args_json: str) -> dict[str, Any]:
    """Archive summary plus playbook_args / intensity / tasks preview."""
    try:
        raw = json_loads_obj(args_json)
    except ValueError:
        return {
            "task_count": 0,
            "roles": [],
            "playbook": None,
            "playbook_args": None,
            "intensity": None,
            "tasks_preview": None,
            "parse_error": True,
        }
    if not isinstance(raw, dict):
        return {
            "task_count": 0,
            "roles": [],
            "playbook": None,
            "playbook_args": None,
            "intensity": None,
            "tasks_preview": None,
            "parse_error": True,
        }
    tasks = raw.get("tasks") or []
    if isinstance(tasks, str):
        try:
            parsed = json_loads_obj(tasks)
            tasks = parsed if isinstance(parsed, list) else []
        except ValueError:
            tasks = []
    if not isinstance(tasks, list):
        tasks = []
    roles: list[str] = []
    preview: list[dict[str, Any]] = []
    for t in tasks[:12]:
        if not isinstance(t, dict):
            continue
        role = str(t.get("role") or "").strip()
        if role:
            roles.append(role)
        preview.append(
            {k: t.get(k) for k in ("role", "task", "title", "form") if t.get(k) not in (None, "")}
        )
    playbook = named_playbook(raw.get("playbook"))
    args = raw.get("playbook_args")
    intensity = None
    if isinstance(args, dict):
        intensity = named_playbook(args.get("intensity"))
    return {
        "task_count": len(tasks),
        "roles": roles,
        "playbook": playbook,
        "playbook_field": raw.get("playbook"),
        "playbook_args": args if isinstance(args, dict) else args,
        "intensity": intensity,
        "tasks_preview": preview,
        "parse_error": False,
    }


def json_loads_obj(text: str) -> Any:
    import json

    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(str(e)) from e


def classify_landing(
    *,
    action: str,
    playbook: str | None,
    expect: str,
    offered: bool,
    task_count: int,
) -> dict[str, Any]:
    """终向落点分类（观测标签，不是 pass/fail 门禁）。"""
    if not offered:
        landing, note = "not_offered", "工具面上没有该 playbook 槽/枚举，属于选不到"
    elif action != "DELEGATE":
        landing, note = "no_delegate", f"终向是 {action}，没有发出 delegate"
    elif playbook == expect:
        landing, note = "selected_expected", f"选了期望的 {expect}"
    elif playbook:
        landing, note = "selected_other", f"选了别的 playbook={playbook!r}（期望 {expect}）"
    elif task_count >= 1:
        landing, note = (
            "handwritten_tasks",
            f"未填具名 playbook，改走手写 tasks（n={task_count}）",
        )
    else:
        landing, note = "empty_delegate", "发出了 delegate，但既无具名 playbook 也无 tasks"
    return {
        "playbook_offered": offered,
        "playbook": playbook,
        "landing": landing,
        "note": note,
    }


def _negated(text: str, start: int) -> bool:
    return bool(_NEG_TAIL.search(text[max(0, start - 12) : start]))


def extract_think_mentions(reasoning: str, *, known_playbooks: Sequence[str] | None = None) -> dict:
    """从思考文本抽出强意图落点（赋值 / 派 / 用 playbook），忽略否定。"""
    known = frozenset(known_playbooks or PLAYBOOKS.keys())
    text = reasoning or ""
    playbooks: list[str] = []
    for m in _STRONG_PLAYBOOK.finditer(text):
        name = next((g for g in m.groups() if g), None)
        if not name:
            continue
        key = name.strip().lower()
        if key not in known or _negated(text, m.start()):
            continue
        if key not in playbooks:
            playbooks.append(key)
    intensities: list[str] = []
    for m in _STRONG_INTENSITY.finditer(text):
        val = (m.group("v") or "").lower()
        if val not in _INTENSITIES or _negated(text, m.start()):
            continue
        if val not in intensities:
            intensities.append(val)
    ask = False
    for m in _STRONG_ASK.finditer(text):
        if not _negated(text, m.start()):
            ask = True
            break
    return {
        "playbooks": playbooks,
        "intensities": intensities,
        "ask_user": ask,
    }


def think_act_divergences(
    mentions: dict,
    *,
    action: str,
    playbook: str | None,
    intensity: str | None,
) -> list[dict[str, str]]:
    """思考里点名的落点 vs 实际终向。空列表 = 未检出分歧。"""
    out: list[dict[str, str]] = []
    actual_pb = playbook or ""
    for mentioned in mentions.get("playbooks") or []:
        if mentioned != actual_pb:
            out.append(
                {
                    "kind": "playbook",
                    "mentioned": mentioned,
                    "actual": actual_pb or action,
                }
            )
    actual_int = intensity or ""
    for mentioned in mentions.get("intensities") or []:
        if action == "DELEGATE" and mentioned != actual_int:
            out.append(
                {
                    "kind": "intensity",
                    "mentioned": mentioned,
                    "actual": actual_int or "(none)",
                }
            )
    if mentions.get("ask_user") and action != "ASK":
        out.append({"kind": "card", "mentioned": "ask_user", "actual": action})
    return out


def landing_fingerprint(agg: dict[str, Any]) -> dict[str, Any]:
    """跨运行对比用的落点指纹（不含 trace / 思考原文 / token）。"""
    return {
        "n": agg["n"],
        "action_counts": dict(sorted(agg["action_counts"].items())),
        "playbook_counts": dict(sorted(agg["playbook_counts"].items())),
        "intensity_counts": dict(sorted(agg["intensity_counts"].items())),
        "landing_counts": dict(sorted(agg["landing_counts"].items())),
        "delegated_n": agg["delegated_n"],
        "card_n": agg["card_n"],
        "expected_n": agg["expected_n"],
        "divergence_n": agg["divergence_n"],
    }


def aggregate_samples(samples: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(samples)
    actions: Counter[str] = Counter()
    playbooks: Counter[str] = Counter()
    intensities: Counter[str] = Counter()
    landings: Counter[str] = Counter()
    delegated_n = card_n = expected_n = divergence_n = error_n = 0
    for s in samples:
        if not s.get("ok"):
            error_n += 1
            actions["ERROR"] += 1
            continue
        action = str(s.get("action") or "UNKNOWN")
        actions[action] += 1
        pb = s.get("playbook") or "none"
        playbooks[str(pb)] += 1
        inten = s.get("intensity") or "none"
        intensities[str(inten)] += 1
        landings[str((s.get("outcome") or {}).get("landing") or "unknown")] += 1
        if s.get("delegated"):
            delegated_n += 1
        if s.get("card_issued"):
            card_n += 1
        if (s.get("outcome") or {}).get("landing") == "selected_expected":
            expected_n += 1
        if s.get("think_act_divergences"):
            divergence_n += 1
    agg = {
        "n": n,
        "error_n": error_n,
        "delegated_n": delegated_n,
        "card_n": card_n,
        "expected_n": expected_n,
        "divergence_n": divergence_n,
        "delegated": f"{delegated_n}/{n}",
        "card_issued": f"{card_n}/{n}",
        "expected_playbook": f"{expected_n}/{n}",
        "think_act_divergence": f"{divergence_n}/{n}",
        "action_counts": dict(actions),
        "playbook_counts": dict(playbooks),
        "intensity_counts": dict(intensities),
        "landing_counts": dict(landings),
    }
    agg["fingerprint"] = landing_fingerprint(agg)
    return agg


def diff_fingerprints(
    previous: dict[str, Any] | None,
    current_scenarios: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """相对上次基线：哪些场景的落点指纹变了。无基线则 available=False。"""
    if not previous:
        return {"available": False, "changed": [], "unchanged": [], "added": [], "removed": []}
    prev_map: dict[str, Any] = {}
    for row in previous.get("scenarios") or []:
        key = row.get("key")
        fp = row.get("fingerprint") or (row.get("aggregate") or {}).get("fingerprint")
        if isinstance(key, str) and isinstance(fp, dict):
            prev_map[key] = fp
    cur_map = {row["key"]: row["aggregate"]["fingerprint"] for row in current_scenarios}
    changed: list[dict[str, Any]] = []
    unchanged: list[str] = []
    for key, fp in cur_map.items():
        if key not in prev_map:
            continue
        if fp != prev_map[key]:
            changed.append({"key": key, "previous": prev_map[key], "current": fp})
        else:
            unchanged.append(key)
    added = sorted(k for k in cur_map if k not in prev_map)
    removed = sorted(k for k in prev_map if k not in cur_map)
    return {
        "available": True,
        "changed": changed,
        "unchanged": unchanged,
        "added": added,
        "removed": removed,
        "n_changed": len(changed),
    }


def lint_scenarios(scenarios: Sequence[RoutingScenario] = SCENARIOS) -> None:
    """零 LLM：结构 + 口语禁术语 + 教科书对照组存在。"""
    if len(scenarios) < 8:
        raise EvalConfigError(f"playbook_routing 场景不足 8 条（got {len(scenarios)}）")
    keys = [s.key for s in scenarios]
    if len(keys) != len(set(keys)):
        raise EvalConfigError("playbook_routing 场景 key 不唯一")
    phrasings = {s.phrasing for s in scenarios}
    if phrasings != {"textbook", "colloquial"}:
        raise EvalConfigError(f"须同时有 textbook 与 colloquial（got {phrasings}）")
    n_text = sum(1 for s in scenarios if s.phrasing == "textbook")
    n_col = sum(1 for s in scenarios if s.phrasing == "colloquial")
    if n_text < 3:
        raise EvalConfigError(f"教科书对照至少 3 条（got {n_text}）")
    if n_col < 6:
        raise EvalConfigError(f"口语场景至少 6 条（got {n_col}）")
    known = set(PLAYBOOKS.keys())
    workspaces = {s.workspace for s in scenarios}
    if "codebase" not in workspaces:
        raise EvalConfigError("至少要有一档 workspace=codebase（真实代码量，避免空仓假象）")
    for s in scenarios:
        if s.expect_playbook not in known:
            raise EvalConfigError(f"{s.key}: 未知 expect_playbook {s.expect_playbook!r}")
        if s.phrasing not in {"textbook", "colloquial"}:
            raise EvalConfigError(f"{s.key}: phrasing 非法")
        if s.workspace not in {"empty", "codebase"}:
            raise EvalConfigError(f"{s.key}: workspace 非法 {s.workspace!r}")
        if not s.user_message.strip():
            raise EvalConfigError(f"{s.key}: user_message 为空")
        if s.category == "code_audit" and s.workspace != "codebase":
            raise EvalConfigError(f"{s.key}: code_audit 必须用 codebase 工作区")
        if s.phrasing == "colloquial":
            lowered = s.user_message.lower()
            hits = [term for term in sorted(_COLLOQUIAL_BAN) if term.lower() in lowered]
            if hits:
                raise EvalConfigError(f"{s.key}: 口语场景含提示词术语 {hits}")
    lint_codebase_fixture()


def lint_codebase_fixture(root: Path | None = None) -> None:
    """审计档工作区必须有真实代码量，避免空临时目录假象。"""
    path = root or _CODEBASE_FIXTURE
    if not path.is_dir():
        raise EvalConfigError(f"codebase 夹具目录不存在: {path}")
    py_files = [p for p in path.rglob("*.py") if p.is_file()]
    py_bytes = sum(p.stat().st_size for p in py_files)
    if len(py_files) < 10 or py_bytes < 8000:
        raise EvalConfigError(
            f"codebase 夹具代码量不够（py_files={len(py_files)} py_bytes={py_bytes}）"
        )


def slim_baseline(report: dict[str, Any]) -> dict[str, Any]:
    """基线只留落点指纹与分布，不复读写满的思考原文。"""
    meta = report.get("meta") or {}
    keep = (
        "timestamp",
        "samples",
        "model",
        "credential_source",
        "tokens",
        "scenario_count",
        "cost_note",
        "report_only",
    )
    return {
        "meta": {k: meta[k] for k in keep if k in meta},
        "scenarios": [
            {
                "key": row["key"],
                "phrasing": row.get("phrasing"),
                "expect_playbook": row.get("expect_playbook"),
                "fingerprint": row.get("fingerprint")
                or (row.get("aggregate") or {}).get("fingerprint"),
                "aggregate": {
                    k: (row.get("aggregate") or {}).get(k)
                    for k in (
                        "n",
                        "delegated",
                        "card_issued",
                        "expected_playbook",
                        "think_act_divergence",
                        "action_counts",
                        "playbook_counts",
                        "intensity_counts",
                        "landing_counts",
                    )
                },
            }
            for row in report.get("scenarios") or []
        ],
    }


def format_playbook_routing_report(report: dict[str, Any]) -> str:
    lines = [
        "=" * 64,
        "AgentCore playbook 路由回归（报告型，不卡门禁）",
        "=" * 64,
    ]
    meta = report.get("meta") or {}
    lines.append(
        f"  模型 {meta.get('model')}  source={meta.get('credential_source')}  "
        f"samples={meta.get('samples')}  scenarios={meta.get('scenario_count')}"
    )
    tokens = meta.get("tokens") or {}
    lines.append(f"  tokens input={tokens.get('input', 0)} output={tokens.get('output', 0)}")
    lines.append(f"  花费量级 {meta.get('cost_note', '')}")
    lines.append("-" * 64)
    for row in report.get("scenarios") or []:
        agg = row.get("aggregate") or {}
        lines.append(
            f"  [{row['key']}] {row.get('phrasing')} expect={row.get('expect_playbook')}  "
            f"派团队 {agg.get('delegated')}  期望playbook {agg.get('expected_playbook')}  "
            f"发卡 {agg.get('card_issued')}  分歧 {agg.get('think_act_divergence')}"
        )
        pb = agg.get("playbook_counts") or {}
        if pb:
            parts = [f"{k}={v}" for k, v in sorted(pb.items())]
            lines.append(f"      playbook: {', '.join(parts)}")
        acts = agg.get("action_counts") or {}
        if acts:
            parts = [f"{k}={v}" for k, v in sorted(acts.items())]
            lines.append(f"      action: {', '.join(parts)}")
    diff = report.get("diff") or {}
    lines.append("-" * 64)
    if not diff.get("available"):
        lines.append("  无上次基线（本次可作为首跑快照；--update-baseline 落盘）")
    else:
        changed = diff.get("changed") or []
        if not changed:
            lines.append("  相对上次基线：所有场景落点指纹未变")
        else:
            lines.append(f"  相对上次基线：{len(changed)} 个场景落点变了")
            for item in changed:
                lines.append(f"    ~ {item['key']}")
                lines.append(f"        was {item['previous']}")
                lines.append(f"        now {item['current']}")
        if diff.get("added"):
            lines.append(f"  新增场景: {diff['added']}")
        if diff.get("removed"):
            lines.append(f"  基线有、本次无: {diff['removed']}")
    lines.append("=" * 64)
    return "\n".join(lines)


def select_scenarios(
    scenarios: Sequence[RoutingScenario] = SCENARIOS,
    *,
    keys: str | None = None,
    phrasing: str | None = None,
) -> list[RoutingScenario]:
    selected = list(scenarios)
    if phrasing:
        selected = [s for s in selected if s.phrasing == phrasing]
    if keys:
        wanted = {k.strip() for k in keys.split(",") if k.strip()}
        selected = [s for s in selected if s.key in wanted]
        missing = wanted - {s.key for s in selected}
        if missing:
            raise EvalConfigError(f"--keys 未匹配: {sorted(missing)}")
    if not selected:
        raise EvalConfigError("过滤后没有场景")
    return selected


@dataclass
class PlaybookRoutingRunConfig:
    samples: int = DEFAULT_SAMPLES
    rounds: int = DEFAULT_ROUNDS
    retries: int = DEFAULT_RETRIES
    mode: str = "economy"
    quiet: bool = False


COST_NOTE = (
    "手搓 9×1 约 43 万 in / 0.9 万 out；默认 samples=3 约 ×3；samples=5 约 ×5。"
    "审计场景因探路最贵。免费档连跑可能撞小时限流。"
)
