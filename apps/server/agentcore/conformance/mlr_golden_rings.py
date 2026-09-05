"""批 C · LV 案黄金场六环离线验收（纯判据，零 LLM / 零 HTTP）。

输入是归一化的 :class:`GoldenBundle`（事件流 + 工作区文件 + 可选费用）；
CLI / pytest 各自负责装载数据。判据与运行手册见 ``docs/02-架构/本地开发.md`` §黄金场验收
（多幕协作机制权威：``docs/03-AI核心/辩论编排设计.md`` §7.3 / §四之三）。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from agentcore.runtime.debate.research_dossier import format_research_dossier_index
from agentcore.runtime.runs.retrieval_budget import (
    DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER,
)
from agentcore.workspace.stage_dirs import (
    DEBATE_PREFIX,
    RESEARCH_DIR,
    RESEARCH_PREFIX,
)

RingStatus = Literal["PASS", "FAIL", "N/A"]

# 污染基线，仅存档观测：首验场旧口径（证人答问核实误记辩手）总量，不再推导门槛。
BASELINE_DEBATER_SEARCHES = 56
# 环4 检索硬判据 = 机制对齐（2026-07-20 产品确认）：任一辩手 run 检索数不得超其
# 机制预算（有约定文档路径恒为此常数；引擎侧 tool_exec 强制，这里抓预算被绕过/接线
# 断裂的回归）。场级总量/分侧数降级为观测指标，不判 FAIL——总量随轮次×子 run 数
# 线性膨胀，任何场级常数都会与轮次策略隐性耦合。
SEARCH_BUDGET_PER_RUN = DEFAULT_RETRIEVAL_BUDGET_DEBATER_WITH_DOSSIER

EXPECTED_RESEARCH_FILES: tuple[str, ...] = (
    f"{RESEARCH_DIR}/法律透镜报告.md",
    f"{RESEARCH_DIR}/品牌商业透镜报告.md",
    f"{RESEARCH_DIR}/舆情公关透镜报告.md",
    f"{RESEARCH_DIR}/文化社会透镜报告.md",
    f"{RESEARCH_DIR}/汇总与命题卡.md",
)

_SEARCH_TOOLS = frozenset({"web_search", "web_fetch"})
_FILE_READ_TOOLS = frozenset({"file_read"})
_LENS_RUN_PREFIXES = ("lens_0", "lens_1", "lens_2", "lens_3")
_BRIEF_SKELETON_KEYS = ("crux", "leaning", "confidence", "recommendation")

# 超笼统启发式：短句 + 终局对抗诉求词 → 期望 CEO ask；否则环1 可 N/A
_VAGUE_GOAL_MARKERS = ("模拟法庭", "开辩", "辩论", "正反辩", "庭审")
_VAGUE_MAX_CHARS = 80

# 命题保真：通用停用词之外，要求 motion 与用户原话共享足够关键词
_STOPWORDS = frozenset(
    [
        "的",
        "了",
        "是",
        "在",
        "和",
        "与",
        "或",
        "及",
        "对",
        "把",
        "被",
        "从",
        "向",
        "为",
        "以",
        "就",
        "都",
        "也",
        "很",
        "更",
        "最",
        "不",
        "没",
        "请",
        "帮",
        "我",
        "你",
        "们",
        "进行",
        "一下",
        "是否",
        "应该",
        "可以",
        "需要",
        "关于",
        "这个",
        "那个",
        "什么",
        "如何",
        "怎么",
        "一个",
        "我们",
        "他们",
        "她们",
        "因为",
        "所以",
        "但是",
        "如果",
        "已经",
        "还是",
    ]
)


@dataclass
class RingResult:
    ring: int
    name: str
    status: RingStatus
    detail: str
    checks: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {"PASS", "N/A"}


@dataclass
class GoldenBundle:
    """离线六环检查的统一输入。"""

    conversation_id: str = ""
    user_prompt: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    workspace_files: list[str] = field(default_factory=list)
    # message_id → messages.cost JSON（可缺）
    message_costs: dict[str, Any] = field(default_factory=dict)
    # turn_id → role hint（assistant turns carrying journal）
    turn_roles: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)


@dataclass
class GoldenReport:
    rings: list[RingResult]
    metrics: dict[str, Any]
    gaps: list[str]
    all_pass: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "all_pass": self.all_pass,
            "rings": [
                {
                    "ring": r.ring,
                    "name": r.name,
                    "status": r.status,
                    "detail": r.detail,
                    "checks": r.checks,
                }
                for r in self.rings
            ],
            "metrics": self.metrics,
            "gaps": self.gaps,
        }


def _event_type(ev: dict[str, Any]) -> str:
    return str(ev.get("type") or ev.get("kind") or "").strip()


def _payload(ev: dict[str, Any]) -> dict[str, Any]:
    p = ev.get("payload")
    return p if isinstance(p, dict) else {}


def _tool_name(payload: dict[str, Any]) -> str:
    return str(
        payload.get("tool_name") or payload.get("name") or payload.get("tool") or ""
    ).strip().lower()


def normalize_events(raw: list[Any]) -> list[dict[str, Any]]:
    """SSEEvent / (type, payload) / dict → ``{type, payload, turn_id?}``."""
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            et = _event_type(item)
            p = _payload(item)
            entry: dict[str, Any] = {"type": et, "payload": p}
            if item.get("turn_id"):
                entry["turn_id"] = str(item["turn_id"])
            if item.get("seq") is not None:
                entry["seq"] = item["seq"]
            out.append(entry)
            continue
        # SSEEvent dataclass
        et = getattr(item, "type", None)
        et_s = str(getattr(et, "value", et) or "")
        p = getattr(item, "payload", None)
        out.append({"type": et_s, "payload": p if isinstance(p, dict) else {}})
    return out


def extract_topic_keywords(prompt: str, *, min_len: int = 2, limit: int = 24) -> list[str]:
    """关键词级保真：从用户原话抽可匹配词（去停用、去过短）。

    纯中文无标点时整句会成一个超长 token——再切 3/4 字 n-gram，避免保真退化成整句精确匹配。
    """
    text = str(prompt or "").strip()
    if not text:
        return []
    tokens = re.findall(
        rf"[\u4e00-\u9fff]{{{min_len},}}|[A-Za-z]{{{min_len},}}",
        text,
    )
    seen: list[str] = []

    def _add(t: str) -> None:
        if not t or t in _STOPWORDS or t in seen:
            return
        seen.append(t)

    for t in tokens:
        if t in _STOPWORDS:
            continue
        _add(t)
        if re.fullmatch(r"[\u4e00-\u9fff]+", t) and len(t) >= 4:
            for n in (4, 3, 2):
                for i in range(0, len(t) - n + 1):
                    _add(t[i : i + n])
                    if len(seen) >= limit:
                        return seen
        if len(seen) >= limit:
            break
    return seen


def motion_preserves_topic(motion: str, user_prompt: str) -> bool:
    """命题与用户原话主题一致（关键词级：共享 ≥2 个词，或原话词极少时 ≥1）。"""
    m = str(motion or "").strip()
    p = str(user_prompt or "").strip()
    if not m:
        return False
    if not p:
        return True
    if p in m or m in p:
        return True
    kws = extract_topic_keywords(p)
    if not kws:
        return True
    # 优先计 ≥3 字命中，降低双字误碰
    strong = [k for k in kws if len(k) >= 3 and k in m]
    if len(strong) >= 2:
        return True
    hits = [k for k in kws if k in m]
    need = 2 if len(kws) >= 2 else 1
    return len(hits) >= need


def prompt_looks_ultra_vague(prompt: str) -> bool:
    """超笼统输入启发式：短 + 含终局对抗诉求。"""
    text = str(prompt or "").strip()
    if not text:
        return False
    has_goal = any(m in text for m in _VAGUE_GOAL_MARKERS)
    return has_goal and len(text) <= _VAGUE_MAX_CHARS


def _collect_motions(events: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for e in events:
        p = _payload(e)
        et = _event_type(e)
        if et == "stage_card_required":
            m = str(p.get("motion") or "").strip()
            if m:
                out.append(m)
            continue
        args = p.get("arguments") if isinstance(p.get("arguments"), dict) else None
        if args and isinstance(args.get("motion_card"), dict):
            m = str(args["motion_card"].get("motion") or "").strip()
            if m:
                out.append(m)
        deb = p.get("debrief") if isinstance(p.get("debrief"), dict) else None
        if deb and isinstance(deb.get("motion_card"), dict):
            m = str(deb["motion_card"].get("motion") or "").strip()
            if m:
                out.append(m)
    return out


def _mlr_roles_completed(events: list[dict[str, Any]]) -> dict[str, bool]:
    started: set[str] = set()
    completed: set[str] = set()
    for e in events:
        et = _event_type(e)
        p = _payload(e)
        rid = str(p.get("run_id") or p.get("agent_id") or "").strip()
        if not rid:
            continue
        key = rid.lower()
        if et == "run_started":
            started.add(key)
        elif et == "run_completed":
            completed.add(key)

    def _hit(prefix: str) -> bool:
        return any(r.startswith(prefix) or prefix in r for r in completed)

    return {
        "lens_0": _hit("lens_0"),
        "lens_1": _hit("lens_1"),
        "lens_2": _hit("lens_2"),
        "lens_3": _hit("lens_3"),
        "synthesizer": _hit("synth"),
    }


def _host_and_debate_execution(events: list[dict[str, Any]]) -> dict[str, Any]:
    host_eid: str | None = None
    debate_eid: str | None = None
    prev_eid: str | None = None
    debate_act: dict[str, Any] = {}
    graph_append: dict[str, Any] = {}
    for e in events:
        et = _event_type(e)
        p = _payload(e)
        if et == "run_plan":
            eid = str(p.get("execution_id") or "").strip()
            plan_type = str(p.get("plan_type") or "")
            act = p.get("act") if isinstance(p.get("act"), dict) else {}
            if plan_type == "multi_agent" and eid and host_eid is None:
                host_eid = eid
            if (plan_type == "debate" or str(act.get("kind") or "") == "debate") and eid:
                debate_eid = eid
                if act:
                    debate_act = act
                prev = str(p.get("prev_execution_id") or "").strip()
                if prev:
                    prev_eid = prev
        elif et == "graph_append":
            # 旧 divert 回放：同 eid 生长。
            graph_append = p
            eid = str(p.get("execution_id") or "").strip()
            if eid:
                debate_eid = debate_eid or eid
    return {
        "host_execution_id": host_eid,
        "debate_execution_id": debate_eid,
        "prev_execution_id": prev_eid,
        "debate_act": debate_act,
        "graph_append": graph_append,
    }


def _debater_run_ids(events: list[dict[str, Any]]) -> dict[str, str]:
    """run_id → label（stance / role / run_id）。"""
    labels: dict[str, str] = {}
    for e in events:
        if _event_type(e) != "run_started":
            continue
        p = _payload(e)
        rid = str(p.get("run_id") or p.get("agent_id") or "").strip()
        if not rid:
            continue
        stance = str(p.get("stance") or "").strip()
        role = str(p.get("role") or "").strip()
        group = str(p.get("group") or "")
        # 辩手：有 stance，或 group 含 debate，且非主持人
        if stance or ("debate" in group and "mod" not in rid.lower()):
            labels[rid] = stance or role or rid
    return labels


def _witness_exam_named(entries: Any) -> list[dict[str, Any]]:
    """从 ``witness_exam`` 列表抽出「主持人已点名」条目（至少一条带 question 的 exchange）。"""
    if not isinstance(entries, list):
        return []
    named: list[dict[str, Any]] = []
    for wx in entries:
        if not isinstance(wx, dict):
            continue
        exchanges = wx.get("exchanges") if isinstance(wx.get("exchanges"), list) else []
        has_q = any(
            isinstance(ex, dict) and str(ex.get("question") or "").strip() for ex in exchanges
        )
        if has_q:
            named.append(wx)
    return named


def _ledger_witness_entries(entries: Any) -> list[dict[str, Any]]:
    """场级台账中 ``side_key=witness:*`` 且含答问片段的登记。"""
    if not isinstance(entries, list):
        return []
    out: list[dict[str, Any]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        side = str(item.get("side_key") or "").strip()
        if not side.startswith("witness:"):
            continue
        snippet = str(item.get("snippet") or "").strip()
        title = str(item.get("title") or "").strip()
        if snippet or title:
            out.append(item)
    return out


def collect_witness_signals(events: list[dict[str, Any]]) -> dict[str, Any]:
    """环6 信号：点名（debate_round / debate_result.rounds）+ 台账（delta / ledger）。

    ``witnesses_roster``：``None``=journal 未声明；``[]``=探测为空（单独辩论/无透镜 session）；
    非空=有可用证人席位。
    """
    named: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    roster: list[dict[str, Any]] | None = None
    saw_debate_result = False
    saw_debate_round = False

    for e in events:
        et = _event_type(e)
        p = _payload(e)
        if et == "debate_round":
            saw_debate_round = True
            named.extend(_witness_exam_named(p.get("witness_exam")))
            ledger.extend(_ledger_witness_entries(p.get("evidence_ledger_delta")))
            continue
        if et != "debate_result":
            continue
        saw_debate_result = True
        if "witnesses" in p:
            raw = p.get("witnesses")
            roster = [w for w in raw if isinstance(w, dict)] if isinstance(raw, list) else []
        ledger.extend(_ledger_witness_entries(p.get("evidence_ledger")))
        rounds = p.get("rounds") if isinstance(p.get("rounds"), list) else []
        for rd in rounds:
            if not isinstance(rd, dict):
                continue
            named.extend(_witness_exam_named(rd.get("witness_exam")))
            ledger.extend(_ledger_witness_entries(rd.get("evidence_ledger_delta")))
        # 扁平 payload 也可能带 witness_exam（容错）
        named.extend(_witness_exam_named(p.get("witness_exam")))

    # 去重：同一 witness_key + 首问
    seen_keys: set[str] = set()
    unique_named: list[dict[str, Any]] = []
    for wx in named:
        key = str(wx.get("witness_key") or wx.get("lens_run_id") or wx.get("seat_run_id") or "")
        q0 = ""
        exs = wx.get("exchanges") if isinstance(wx.get("exchanges"), list) else []
        if exs and isinstance(exs[0], dict):
            q0 = str(exs[0].get("question") or "")[:40]
        dedupe = f"{key}|{q0}"
        if dedupe in seen_keys:
            continue
        seen_keys.add(dedupe)
        unique_named.append(wx)

    seen_eids: set[str] = set()
    unique_ledger: list[dict[str, Any]] = []
    for item in ledger:
        eid = str(item.get("id") or "")
        fingerprint = eid or f"{item.get('side_key')}|{item.get('title')}|{item.get('snippet')}"
        if fingerprint in seen_eids:
            continue
        seen_eids.add(fingerprint)
        unique_ledger.append(item)

    return {
        "named_exams": unique_named,
        "named_count": len(unique_named),
        "ledger_entries": unique_ledger,
        "ledger_count": len(unique_ledger),
        "witnesses_roster": roster,
        "saw_debate_result": saw_debate_result,
        "saw_debate_round": saw_debate_round,
    }


def collect_metrics(bundle: GoldenBundle) -> dict[str, Any]:
    """量化指标（§1.2 判断表回填用）。取不到的字段显式标 gap，不硬造。"""
    events = bundle.events
    gaps: list[str] = list(bundle.gaps)

    debater_labels = _debater_run_ids(events)
    search_by_debater: Counter[str] = Counter()
    search_by_debater_run: Counter[str] = Counter()
    charged_by_debater_run: Counter[str] = Counter()
    saw_search_end = False
    search_by_tool: Counter[str] = Counter()
    witness_search_total = 0
    other_search_total = 0
    file_read_research = 0
    background_len = 0
    saw_debate_tool = False

    def _classify(rid: str) -> str:
        """run → debater / witness / other / unmapped（向量无 stance 时）。"""
        rid_l = rid.lower()
        # 证人席位（幕1 透镜进幕2 答问核实）单列，不占辩手检索预算口径。
        if "_wit_" in rid_l or rid_l.startswith("wit_"):
            return "witness"
        if (
            rid in debater_labels
            or "pro" in rid_l
            or "con" in rid_l
            or "debater" in rid_l
        ):
            return "debater"
        return "unmapped" if not debater_labels else "other"

    active_run: str | None = None
    for e in events:
        et = _event_type(e)
        p = _payload(e)
        if et == "run_started":
            active_run = str(p.get("run_id") or p.get("agent_id") or "") or None
            continue

        # 预算记账口径（与机制同义）：成功完成才占预算槽，拒绝/失败不占——
        # 引擎在预算耗尽时拒绝调用并回「预算已尽」，那是机制在工作，不是超支。
        if et == "tool_use_end":
            name = _tool_name(p)
            if name in _SEARCH_TOOLS:
                rid = str(p.get("run_id") or active_run or "").strip()
                if _classify(rid) in ("debater", "unmapped"):
                    saw_search_end = True
                    if str(p.get("status") or "") == "success":
                        charged_by_debater_run[rid or "unknown"] += 1
            continue

        if et not in {"tool_use_start", "tool_call_started"}:
            continue
        name = _tool_name(p)
        rid = str(p.get("run_id") or active_run or "").strip()
        args = p.get("arguments") if isinstance(p.get("arguments"), dict) else {}

        if name == "debate" or name.endswith(".debate"):
            saw_debate_tool = True
            bg = str(args.get("background") or "")
            background_len = max(background_len, len(bg))

        if name in _SEARCH_TOOLS:
            label = debater_labels.get(rid) or rid or "unknown"
            bucket = _classify(rid)
            if bucket == "witness":
                witness_search_total += 1
            elif bucket == "debater":
                # 只计辩手（环4 预算合规的对象；幕1 透镜多检索是本职，不在此列）
                search_by_tool[name] += 1
                search_by_debater[label] += 1
                search_by_debater_run[rid or label] += 1
            elif bucket == "unmapped":
                # 向量未带 stance 时仍按 run 分列，避免指标空洞
                search_by_tool[name] += 1
                search_by_debater[rid or "unknown"] += 1
                search_by_debater_run[rid or "unknown"] += 1
            else:
                # 幕1 透镜 / CEO 等非辩手检索：只进 other 观测，不进辩手口径
                other_search_total += 1

        if name in _FILE_READ_TOOLS:
            blob = str(args.get("path") or args.get("file") or args.get("target") or "")
            if not blob:
                blob = str(args)
            if RESEARCH_PREFIX in blob.replace("\\", "/"):
                file_read_research += 1

    research_paths = [
        p.replace("\\", "/")
        for p in bundle.workspace_files
        if p.replace("\\", "/").startswith(RESEARCH_PREFIX)
    ]
    dossier_index = format_research_dossier_index(research_paths)
    dossier_index_len = len(dossier_index)

    if not saw_debate_tool and background_len == 0:
        gaps.append(
            "background_len：journal 未见 debate 工具 arguments.background"
            "（stage_card 机制直起路径常见为 0；约定文档注入见 dossier_index_len）"
        )

    # 分幕费用：按 turn 归属（含 multi_agent run_plan 的 turn = 幕1；含 debate act 的 = 幕2）
    act1_turns: set[str] = set()
    act2_turns: set[str] = set()
    for e in events:
        et = _event_type(e)
        p = _payload(e)
        tid = str(e.get("turn_id") or "").strip()
        if not tid:
            continue
        if et == "run_plan":
            if str(p.get("plan_type") or "") == "multi_agent":
                act1_turns.add(tid)
            act = p.get("act") if isinstance(p.get("act"), dict) else {}
            if str(p.get("plan_type") or "") == "debate" or str(act.get("kind") or "") == "debate":
                act2_turns.add(tid)
        if et == "graph_append":
            act2_turns.add(tid)

    def _cost_usd(cost: Any) -> float | None:
        if not isinstance(cost, dict):
            return None
        for key in ("total_usd", "usd_total", "total", "cost_usd", "amount", "cny_total"):
            if key in cost and cost[key] is not None:
                try:
                    return float(cost[key])
                except (TypeError, ValueError):
                    pass
        # nano-CNY 分量（兼容旧 nano-USD 键名）
        for key in ("total_nano", "total_nano_usd", "nano_usd", "nano_cny"):
            if key in cost and cost[key] is not None:
                try:
                    return float(cost[key]) / 1_000_000_000.0
                except (TypeError, ValueError):
                    pass
        return None

    cost_act1: float | None = None
    cost_act2: float | None = None
    cost_total: float | None = None
    if bundle.message_costs:
        c1 = 0.0
        c2 = 0.0
        ct = 0.0
        n1 = n2 = nt = 0
        for mid, cost in bundle.message_costs.items():
            usd = _cost_usd(cost)
            if usd is None:
                continue
            ct += usd
            nt += 1
            if mid in act1_turns:
                c1 += usd
                n1 += 1
            if mid in act2_turns:
                c2 += usd
                n2 += 1
        if nt:
            cost_total = ct
        if n1:
            cost_act1 = c1
        if n2:
            cost_act2 = c2
        if nt and (not n1 or not n2):
            gaps.append(
                "费用分幕：messages.cost 有总账，但无法完整映射幕1/幕2 turn"
                f"（act1_turns={len(act1_turns)} act2_turns={len(act2_turns)} "
                f"costed={nt}）"
            )
    else:
        gaps.append("费用：无 messages.cost（离线样本未提供 / DB 未回写）")

    # 预算合规首选「成功完成」口径（tool_use_end status=success，与机制记账同义）；
    # 合成向量常只有 start 事件 → 回落发起口径。
    budget_charged_by_run = (
        dict(charged_by_debater_run) if saw_search_end else dict(search_by_debater_run)
    )
    return {
        "debater_search_total": int(sum(search_by_tool.values())),
        "debater_search_by_tool": dict(search_by_tool),
        "debater_search_by_debater": dict(search_by_debater),
        "debater_search_by_run": dict(search_by_debater_run),
        "debater_search_charged_by_run": budget_charged_by_run,
        # 观测单列：证人答问核实 / 幕1 透镜与其他角色——不占辩手预算口径。
        "witness_search_total": witness_search_total,
        "non_debater_search_total": other_search_total,
        "debater_search_baseline_old": BASELINE_DEBATER_SEARCHES,
        "background_len": background_len,
        "dossier_index_len": dossier_index_len,
        "dossier_index_files": len(research_paths),
        "research_file_read_hits": file_read_research,
        "cost_act1": cost_act1,
        "cost_act2": cost_act2,
        "cost_total": cost_total,
        "gaps": gaps,
    }


def evaluate_rings(bundle: GoldenBundle) -> GoldenReport:
    """对 bundle 跑六环断言，返回人类可读报告结构。"""
    events = bundle.events
    files = [p.replace("\\", "/") for p in bundle.workspace_files]
    metrics = collect_metrics(bundle)
    gaps = list(metrics.pop("gaps", []))

    types = Counter(_event_type(e) for e in events)
    ask_events = types.get("ask_user_required", 0) + types.get("checkpoint_required", 0)
    ask_tool = any(
        _event_type(e) in {"tool_use_start", "tool_call_started"}
        and "ask_user" in _tool_name(_payload(e))
        for e in events
    )
    vague = prompt_looks_ultra_vague(bundle.user_prompt)
    if ask_events or ask_tool:
        ring1 = RingResult(
            1,
            "超笼统→CEO ask_user 澄清",
            "PASS",
            f"ask_events={ask_events} ask_user_tool={ask_tool}",
            {"ask_events": ask_events, "ask_user_tool": ask_tool, "vague": vague},
        )
    elif vague:
        ring1 = RingResult(
            1,
            "超笼统→CEO ask_user 澄清",
            "FAIL",
            "输入判定为超笼统，但未见 ask_user / checkpoint_required",
            {"ask_events": 0, "ask_user_tool": False, "vague": True},
        )
    else:
        ring1 = RingResult(
            1,
            "超笼统→CEO ask_user 澄清",
            "N/A",
            "输入非超笼统（或无原话），本环跳过",
            {"ask_events": 0, "ask_user_tool": False, "vague": False},
        )

    roles = _mlr_roles_completed(events)
    lenses_ok = all(roles[k] for k in _LENS_RUN_PREFIXES) and roles["synthesizer"]
    research_ok = all(f in files for f in EXPECTED_RESEARCH_FILES)
    missing_research = [f for f in EXPECTED_RESEARCH_FILES if f not in files]
    motions = _collect_motions(events)
    fidelity = (
        all(motion_preserves_topic(m, bundle.user_prompt) for m in motions)
        if motions and bundle.user_prompt
        else bool(motions)
    )
    has_motion = bool(motions)
    ring2_pass = lenses_ok and research_ok and has_motion and fidelity
    ring2 = RingResult(
        2,
        "幕1 四透镜+汇总落盘+命题保真",
        "PASS" if ring2_pass else "FAIL",
        (
            f"lenses={lenses_ok} research={research_ok} "
            f"motion={has_motion} fidelity={fidelity} missing={missing_research}"
        ),
        {
            "roles": roles,
            "research_ok": research_ok,
            "missing_research": missing_research,
            "motions": motions[:6],
            "fidelity": fidelity,
        },
    )

    stage_req = types.get("stage_card_required", 0) >= 1
    stage_res_start = False
    for e in events:
        if _event_type(e) != "stage_card_resolved":
            continue
        p = _payload(e)
        if str(p.get("decision") or "") == "start_debate":
            stage_res_start = True
    exec_info = _host_and_debate_execution(events)
    ga = exec_info["graph_append"] or {}
    act = exec_info["debate_act"] or {}
    auth = str(ga.get("authorized_by") or act.get("authorized_by") or "")
    auth_ok = auth == "stage_card"
    team_preview = types.get("team_preview_required", 0)
    # 推进卡一步：有 resolved(start_debate) + authorized_by=stage_card，且无多余开工卡
    ring3_pass = stage_req and stage_res_start and auth_ok and team_preview == 0
    ring3 = RingResult(
        3,
        "推进卡一步授权 start_debate",
        "PASS" if ring3_pass else "FAIL",
        (
            f"required={stage_req} resolved_start_debate={stage_res_start} "
            f"authorized_by={auth!r} team_preview={team_preview}"
        ),
        {
            "stage_card_required": stage_req,
            "stage_card_resolved_start_debate": stage_res_start,
            "authorized_by": auth,
            "team_preview_required": team_preview,
        },
    )

    # 幕 2 链到幕 1：新契约 = prev_execution_id；旧 divert = 同 execution_id。
    linked = bool(
        exec_info["host_execution_id"]
        and exec_info["debate_execution_id"]
        and (
            exec_info["host_execution_id"] == exec_info["debate_execution_id"]
            or exec_info.get("prev_execution_id") == exec_info["host_execution_id"]
        )
    )
    act_id = str(ga.get("act_id") or act.get("act_id") or "")
    act2_ok = act_id.startswith("act-") and act_id != "act-1"
    anchor = str(act.get("anchor_run_id") or "")
    anchor_synth = "synth" in anchor.lower()
    file_reads = int(metrics.get("research_file_read_hits") or 0)
    searches = int(metrics.get("debater_search_total") or 0)
    # 机制对齐硬判据：任一辩手 run「成功完成」的检索数超其机制预算 = 预算强制被
    # 绕过 / 接线断裂（被引擎拒绝的发起不占槽——那是机制在工作）。
    # 场级总量只观测（见 SEARCH_BUDGET_PER_RUN 注释）。
    search_by_run = {
        str(k): int(v)
        for k, v in (metrics.get("debater_search_charged_by_run") or {}).items()
    }
    budget_violations = {
        rid: n for rid, n in search_by_run.items() if n > SEARCH_BUDGET_PER_RUN
    }
    search_ok = not budget_violations
    metrics["debater_search_budget_per_run"] = SEARCH_BUDGET_PER_RUN
    metrics["debater_budget_violations"] = dict(budget_violations)
    ring4_pass = (
        linked
        and act2_ok
        and anchor_synth
        and file_reads >= 1
        and search_ok
        and auth_ok
    )
    ring4 = RingResult(
        4,
        "幕2 链到幕1+约定文档消费+检索预算合规",
        "PASS" if ring4_pass else "FAIL",
        (
            f"linked={linked} prev={exec_info.get('prev_execution_id')!r} "
            f"act_id={act_id!r} anchor_synth={anchor_synth} "
            f"file_read_research={file_reads} "
            f"per_run≤{SEARCH_BUDGET_PER_RUN}={search_ok}"
            f"（超预算 run={len(budget_violations)}）total={searches}(观测)"
        ),
        {
            "linked_to_host": linked,
            "same_execution": exec_info["host_execution_id"]
            == exec_info["debate_execution_id"],
            "prev_execution_id": exec_info.get("prev_execution_id"),
            "host_execution_id": exec_info["host_execution_id"],
            "debate_execution_id": exec_info["debate_execution_id"],
            "act_id": act_id,
            "anchor_run_id": anchor,
            "anchor_is_synthesizer": anchor_synth,
            "research_file_read_hits": file_reads,
            "debater_search_total": searches,
            "search_budget_per_run": SEARCH_BUDGET_PER_RUN,
            "budget_violations": dict(budget_violations),
        },
    )

    debate_files = [p for p in files if p.startswith(DEBATE_PREFIX)]
    has_brief = any("决策简报" in p for p in debate_files)
    has_narrative = any("交锋叙事线" in p for p in debate_files)
    brief_skeleton: dict[str, bool] = {}
    debate_result_n = 0
    for e in events:
        if _event_type(e) != "debate_result":
            continue
        debate_result_n += 1
        p = _payload(e)
        # payload 可能是扁平 brief 或嵌套
        brief = p.get("brief") if isinstance(p.get("brief"), dict) else p
        if not isinstance(brief, dict):
            continue
        for k in _BRIEF_SKELETON_KEYS:
            brief_skeleton[k] = bool(str(brief.get(k) or "").strip()) or (
                brief_skeleton.get(k, False)
            )
    skeleton_ok = (
        all(brief_skeleton.get(k) for k in _BRIEF_SKELETON_KEYS)
        if brief_skeleton
        else False
    )
    if debate_result_n == 0:
        gaps.append("debate_result：journal 未见该事件，无法检四维骨架")
    ring5_pass = has_brief and has_narrative and skeleton_ok
    ring5 = RingResult(
        5,
        "双产物落盘+debate_result 四维骨架",
        "PASS" if ring5_pass else "FAIL",
        (
            f"brief_file={has_brief} narrative_file={has_narrative} "
            f"skeleton={brief_skeleton} debate_result_n={debate_result_n}"
        ),
        {
            "debate_files": debate_files,
            "has_brief_file": has_brief,
            "has_narrative_file": has_narrative,
            "brief_skeleton": brief_skeleton,
            "debate_result_count": debate_result_n,
        },
    )

    # 环6 · 证人点名（批 D1）：幕1 透镜进幕2 被质询 ≥1，答问进场级台账 side_key=witness:*
    wit = collect_witness_signals(events)
    lenses_any = any(roles[k] for k in _LENS_RUN_PREFIXES)
    has_debate_act = bool(
        exec_info["debate_execution_id"]
        or (exec_info["debate_act"] and str(exec_info["debate_act"].get("kind") or "") == "debate")
        or types.get("debate_result", 0)
        or types.get("debate_round", 0)
    )
    roster = wit["witnesses_roster"]
    named_n = int(wit["named_count"])
    ledger_n = int(wit["ledger_count"])
    metrics["witness_named_count"] = named_n
    metrics["witness_ledger_count"] = ledger_n
    metrics["witnesses_roster_size"] = len(roster) if roster is not None else None

    if not has_debate_act or not lenses_any:
        ring6 = RingResult(
            6,
            "证人点名≥1+答问进台账",
            "N/A",
            (
                "无幕2辩论或无幕1透镜完成，证人机制不适用"
                f"（debate={has_debate_act} lenses_any={lenses_any}）"
            ),
            {
                "applicable": False,
                "has_debate_act": has_debate_act,
                "lenses_any": lenses_any,
                "named_count": named_n,
                "ledger_count": ledger_n,
                "witnesses_roster": roster,
            },
        )
    elif roster is not None and len(roster) == 0:
        ring6 = RingResult(
            6,
            "证人点名≥1+答问进台账",
            "N/A",
            "debate_result.witnesses=[]（探测无可用透镜 session，整场零证人）",
            {
                "applicable": False,
                "has_debate_act": True,
                "lenses_any": lenses_any,
                "named_count": named_n,
                "ledger_count": ledger_n,
                "witnesses_roster": [],
            },
        )
    else:
        if not wit["saw_debate_result"] and not wit["saw_debate_round"]:
            gaps.append(
                "证人：未见 debate_round / debate_result，无法核验 witness_exam 与台账"
            )
        elif wit["saw_debate_result"] and roster is None and named_n == 0 and ledger_n == 0:
            gaps.append(
                "证人：debate_result 无 witnesses 字段且无 witness_exam/台账"
                "（旧 journal 或缺证人机制落痕）"
            )
        ring6_pass = named_n >= 1 and ledger_n >= 1
        ring6 = RingResult(
            6,
            "证人点名≥1+答问进台账",
            "PASS" if ring6_pass else "FAIL",
            (
                f"named={named_n} ledger_witness={ledger_n} "
                f"roster_size={len(roster) if roster is not None else 'absent'}"
            ),
            {
                "applicable": True,
                "has_debate_act": True,
                "lenses_any": lenses_any,
                "named_count": named_n,
                "ledger_count": ledger_n,
                "witnesses_roster": roster,
                "named_keys": [
                    str(x.get("witness_key") or x.get("lens_run_id") or "")
                    for x in wit["named_exams"][:8]
                ],
                "ledger_side_keys": [
                    str(x.get("side_key") or "") for x in wit["ledger_entries"][:8]
                ],
            },
        )

    rings = [ring1, ring2, ring3, ring4, ring5, ring6]
    # N/A 不阻断 all_pass；仅 FAIL 阻断
    all_pass = all(r.ok for r in rings)
    return GoldenReport(rings=rings, metrics=metrics, gaps=gaps, all_pass=all_pass)


def format_report(report: GoldenReport, *, conversation_id: str = "") -> str:
    lines = ["======== 批C 黄金场六环验收（离线）========"]
    if conversation_id:
        lines.append(f"conversation_id: {conversation_id}")
    lines.append("")
    lines.append(f"{'环':<4} {'状态':<6} {'名称'}")
    lines.append("-" * 72)
    for r in report.rings:
        lines.append(f"{r.ring:<4} {r.status:<6} {r.name}")
        lines.append(f"       {r.detail}")
    lines.append("")
    lines.append("-------- 量化指标 --------")
    for k, v in report.metrics.items():
        lines.append(f"  {k}: {v}")
    if report.gaps:
        lines.append("")
        lines.append("-------- 数据缺口（未硬造）--------")
        for g in report.gaps:
            lines.append(f"  · {g}")
    lines.append("")
    lines.append(f"ALL_PASS={report.all_pass}")
    return "\n".join(lines)


def sse_events_to_bundle(
    events: list[Any],
    *,
    user_prompt: str = "",
    workspace_files: list[str] | None = None,
    conversation_id: str = "",
    message_costs: dict[str, Any] | None = None,
) -> GoldenBundle:
    """从 conformance 向量 / 合成 SSE 事件构造 bundle。"""
    return GoldenBundle(
        conversation_id=conversation_id,
        user_prompt=user_prompt,
        events=normalize_events(events),
        workspace_files=list(workspace_files or []),
        message_costs=dict(message_costs or {}),
    )
