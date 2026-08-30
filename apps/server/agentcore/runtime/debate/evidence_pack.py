"""共享证据包（Evidence Pack）——庭前「共享事实库 → 对抗论证」的数据契约。

行业实践：附件/底料先组装为双方共享的证据包，再开辩；禁止对同一附件各自深挖 ReAct。
本模块只负责契约 + 从主持人上下文机械组装 + 完整度驱动的外证跳过计划（庭前舰队已删；
发言期有界预算见 ``debater_budgets_from_completeness``）；LLM 精炼条款锚/争议点留给后续步。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from agentcore.runtime.debate.types import DebateSide

EvidenceSourceKind = Literal[
    "attachment",
    "conversation",
    "background",
    "dossier",
    "workspace",
]
PackCompleteness = Literal["full", "partial", "empty"]
# 庭前舰队已删：外证计划恒为 skip（观测字段仍保留 mode/reason）。
ExternalEvidenceMode = Literal["skip"]
ExternalEvidencePath = Literal[
    "evidence_pack",
    "no_pack",
    "fast",
]

# 与 ``_build_attachment_context`` 产出的块头对齐。
_ATTACHED_RE = re.compile(
    r"<附件>(.*?)</附件>",
    re.DOTALL | re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"---\s*(File|Conversation|Directory):\s*(.+?)\s*\(([^)]+)\)([^\n]*)---\n"
    r"(.*?)(?=\n---\s*(?:File|Conversation|Directory):|\Z)",
    re.DOTALL,
)

# 内联摘录上限（控 token；全文由辩手按 path 自取）。
_EXCERPT_CAP = 1200
_PACK_SIDE_KEY = "evidence_pack"


@dataclass(frozen=True)
class EvidenceSource:
    """证据包中的一条来源（附件 / 对话日志 / 底料等）。"""

    source_id: str
    kind: EvidenceSourceKind
    label: str
    path: str = ""
    excerpt: str = ""
    complete: bool = True
    failure: str = ""

    def to_wire(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "kind": self.kind,
            "label": self.label,
            "path": self.path or None,
            "excerpt": self.excerpt,
            "complete": self.complete,
            "failure": self.failure or None,
        }


@dataclass(frozen=True)
class DisputeCandidate:
    """争议候选（薄结构；精炼留给后续步）。"""

    claim: str
    why_contested: str = ""
    related_source_ids: tuple[str, ...] = ()

    def to_wire(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "why_contested": self.why_contested or None,
            "related_source_ids": list(self.related_source_ids),
        }


@dataclass
class EvidencePack:
    """双方共享的庭前证据包。"""

    sources: list[EvidenceSource] = field(default_factory=list)
    dispute_candidates: list[DisputeCandidate] = field(default_factory=list)
    motion: str = ""
    completeness: PackCompleteness = "empty"
    notes: str = ""
    # 台账登记后回填的 #eN（source_id → eid）；非 wire 必填。
    ledger_ids: dict[str, str] = field(default_factory=dict)

    def has_usable_body(self) -> bool:
        """至少一条带来源正文摘录的附件/对话来源（truncated 仍算可用；binary/空正文不算）。"""
        return any(
            s.kind in ("attachment", "conversation")
            and bool((s.excerpt or "").strip())
            and s.failure not in ("binary_no_text", "empty_body")
            for s in self.sources
        )

    def to_wire(self) -> dict[str, Any]:
        return {
            "motion": self.motion or None,
            "completeness": self.completeness,
            "notes": self.notes or None,
            "sources": [s.to_wire() for s in self.sources],
            "dispute_candidates": [d.to_wire() for d in self.dispute_candidates],
            "ledger_ids": dict(self.ledger_ids) or None,
        }


def _clip_excerpt(text: str, *, cap: int = _EXCERPT_CAP) -> tuple[str, bool]:
    body = (text or "").strip()
    if len(body) <= cap:
        return body, False
    return body[:cap].rstrip() + "…", True


def _kind_from_header(header: str) -> EvidenceSourceKind | None:
    h = (header or "").strip().lower()
    if h == "file":
        return "attachment"
    if h == "conversation":
        return "conversation"
    return None  # Directory：无正文，不算可用正文附件


def _is_binary_stub(flags: str, body: str) -> bool:
    """``[binary]`` 且正文只是「用 code_execute」说明 → 无可用正文。"""
    if "[binary]" not in (flags or "").lower():
        return False
    b = (body or "").strip().lower()
    if not b:
        return True
    return "binary file" in b or "code_execute" in b


def parse_attached_file_sources(system_prompt: str) -> list[EvidenceSource]:
    """从主持人/worker 系统提示中的 ``<附件>`` 抽出可用正文来源。"""
    if not isinstance(system_prompt, str):
        return []
    raw = system_prompt
    m = _ATTACHED_RE.search(raw)
    if not m:
        return []
    block = m.group(1)
    out: list[EvidenceSource] = []
    for i, bm in enumerate(_BLOCK_RE.finditer(block), start=1):
        header, name, path, flags, body = bm.group(1), bm.group(2), bm.group(3), bm.group(4), bm.group(5)
        kind = _kind_from_header(header)
        if kind is None:
            continue
        label = (name or "").strip() or f"attachment_{i}"
        path_s = (path or "").strip()
        flags_s = (flags or "").strip()
        body_s = (body or "").strip()
        sid = f"att:{path_s or label}"
        if _is_binary_stub(flags_s, body_s):
            out.append(
                EvidenceSource(
                    source_id=sid,
                    kind=kind,
                    label=label,
                    path=path_s,
                    excerpt="",
                    complete=False,
                    failure="binary_no_text",
                )
            )
            continue
        if not body_s:
            out.append(
                EvidenceSource(
                    source_id=sid,
                    kind=kind,
                    label=label,
                    path=path_s,
                    excerpt="",
                    complete=False,
                    failure="empty_body",
                )
            )
            continue
        excerpt, clipped = _clip_excerpt(body_s)
        truncated_flag = "truncated" in flags_s.lower() or clipped
        out.append(
            EvidenceSource(
                source_id=sid,
                kind=kind,
                label=label,
                path=path_s,
                excerpt=excerpt,
                complete=not truncated_flag,
                failure="truncated" if truncated_flag else "",
            )
        )
    return out


def thin_dispute_candidates(
    sides: Sequence[DebateSide],
    *,
    source_ids: Sequence[str],
) -> list[DisputeCandidate]:
    """按各方立场生成薄争议候选（非 LLM；后续步可精炼）。"""
    ids = tuple(s for s in source_ids if s)
    out: list[DisputeCandidate] = []
    for side in sides:
        stance = (side.stance or side.name or side.key).strip()
        out.append(
            DisputeCandidate(
                claim=f"「{side.name}」主张：{stance}",
                why_contested="共享证据上的对抗论证点（庭前候选，开辩后由双方展开）",
                related_source_ids=ids,
            )
        )
    return out


def assemble_evidence_pack_from_host(
    *,
    system_prompt: str,
    motion: str = "",
    sides: Sequence[DebateSide] = (),
    background: str = "",
) -> EvidencePack | None:
    """若主持人上下文已有可用正文附件 → 组装 Evidence Pack；否则 ``None``。

    判定：``<附件>`` 内至少一条 File/Conversation 带来源正文。
    纯 binary / 空正文 / 仅 Directory → 不走本路径（回落 no_pack）。
    """
    sources = parse_attached_file_sources(system_prompt)
    usable = [
        s
        for s in sources
        if s.kind in ("attachment", "conversation")
        and (s.excerpt or "").strip()
        and s.failure not in ("binary_no_text", "empty_body")
    ]
    if not usable:
        return None

    # 失败/binary 条目仍保留在包内作完整度标记，但不计入「可用」。
    pack_sources = list(sources)
    bg = (background or "").strip()
    if bg:
        excerpt, clipped = _clip_excerpt(bg)
        pack_sources.append(
            EvidenceSource(
                source_id="bg:host",
                kind="background",
                label="主持人底料",
                excerpt=excerpt,
                complete=not clipped,
                failure="truncated" if clipped else "",
            )
        )

    n_fail = sum(1 for s in pack_sources if s.failure)
    if n_fail == 0 and all(s.complete for s in usable):
        completeness: PackCompleteness = "full"
    elif usable:
        completeness = "partial"
    else:
        completeness = "empty"

    source_ids = [s.source_id for s in usable]
    disputes = thin_dispute_candidates(sides, source_ids=source_ids) if sides else []
    notes = (
        f"从主持人上下文组装共享证据包（{len(usable)} 份可用正文附件）；"
        "庭前不派员、不对同一附件深度 file_read/grep。"
    )
    return EvidencePack(
        sources=pack_sources,
        dispute_candidates=disputes,
        motion=(motion or "").strip(),
        completeness=completeness,
        notes=notes,
    )


@dataclass(frozen=True)
class ExternalEvidencePlan:
    """完整度驱动的外证跳过计划（庭前舰队已删；发言期预算另见 debater_budgets）。"""

    mode: ExternalEvidenceMode
    retrieval_budget: int
    sides: tuple[str, ...]
    allow_read_url: bool
    max_tasks_per_side: int
    reason: str

    @property
    def allow_external(self) -> bool:
        return False

    def to_wire(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "retrieval_budget": self.retrieval_budget,
            "sides": list(self.sides),
            "allow_read_url": self.allow_read_url,
            "max_tasks_per_side": self.max_tasks_per_side,
            "reason": self.reason,
            "allow_external": self.allow_external,
        }


def _skip_plan(*, reason: str) -> ExternalEvidencePlan:
    return ExternalEvidencePlan(
        mode="skip",
        retrieval_budget=0,
        sides=(),
        allow_read_url=False,
        max_tasks_per_side=0,
        reason=reason,
    )


def resolve_external_evidence_plan(
    *,
    completeness: PackCompleteness,
    path: ExternalEvidencePath,
) -> ExternalEvidencePlan:
    """由完整度 / 路径解析外证计划：庭前永不派员（恒 skip）。

    - ``fast`` → skip
    - ``evidence_pack`` + ``full`` → skip（``evidence_pack_full``）
    - ``evidence_pack`` + ``partial``/``empty`` → skip（发言期对称有界预算）
    - ``no_pack`` → skip（发言期对称有界预算）
    """
    if path == "fast":
        return _skip_plan(reason="fast")
    if path == "evidence_pack":
        if completeness == "full":
            return _skip_plan(reason="evidence_pack_full")
        if completeness == "partial":
            return _skip_plan(reason="evidence_pack_partial")
        return _skip_plan(reason="evidence_pack_empty")
    return _skip_plan(reason="no_pack")


def debater_budgets_from_completeness(
    *,
    side_keys: Sequence[str],
    completeness: PackCompleteness,
) -> dict[str, int]:
    """庭前后辩手 per-side ``retrieval_budget``。

    - ``full`` → 0（共享包已充分，禁外证扫网）
    - ``partial``/``empty`` → 各方对称有界残搜（``BOUNDED_GAP_FILL_RETRIEVAL_BUDGET``）
    """
    from agentcore.runtime.debate.constants import BOUNDED_GAP_FILL_RETRIEVAL_BUDGET

    out: dict[str, int] = {}
    for key in side_keys:
        if not key:
            continue
        if completeness == "full":
            out[key] = 0
        elif completeness in ("partial", "empty"):
            out[key] = BOUNDED_GAP_FILL_RETRIEVAL_BUDGET
        else:
            out[key] = 0
    return out


def format_evidence_completeness_notice(
    *,
    completeness: PackCompleteness,
    path: str = "",
) -> str:
    """写入约定文档索引 / 主持人 frame 可见的「证据不完整」显式标注。"""
    if completeness == "full":
        return ""
    path_bit = f"路径={path}；" if path else ""
    return (
        "【庭前取证·证据不完整】"
        f"{path_bit}完整度={completeness}。"
        "开辩与审议时【禁止】假定已充分取证；缺证侧主张须标【待核实】，"
        "主持人开场与双方发言须显式承认本侧或共享包证据缺口。\n"
    )


def format_evidence_pack_index(pack: EvidencePack) -> str:
    """写入 ``research_dossier_index`` 的共享证据包索引块（非全文灌入）。"""
    if not pack.sources:
        return ""
    lines: list[str] = []
    for s in pack.sources:
        path_bit = f" · {s.path}" if s.path else ""
        status = "完整" if s.complete and not s.failure else f"不完整/{s.failure or 'partial'}"
        eid = pack.ledger_ids.get(s.source_id, "")
        eid_bit = f" · {eid}" if eid else ""
        blurb = (s.excerpt or "").replace("\n", " ").strip()
        if len(blurb) > 80:
            blurb = blurb[:80] + "…"
        blurb_bit = f"：{blurb}" if blurb else ""
        lines.append(
            f"- [{s.kind}] {s.label}{path_bit}{eid_bit}（{status}）{blurb_bit}"
        )
    body = "\n".join(lines)
    dispute_lines = ""
    if pack.dispute_candidates:
        dls = "\n".join(
            f"- {d.claim}" + (f"（{d.why_contested}）" if d.why_contested else "")
            for d in pack.dispute_candidates
        )
        dispute_lines = f"\n\n【争议候选】\n{dls}"
    ledger_lines = ""
    if pack.ledger_ids:
        mapped = "\n".join(
            f"- {eid} · {sid}" for sid, eid in pack.ledger_ids.items()
        )
        ledger_lines = (
            "\n\n【证据包预登记台账·引用须用下列 #eN】\n"
            "引用附件事实写成【已核实·#eN】（id 见下；双方共享，禁各造私证）。\n"
            f"{mapped}"
        )
    incomplete_banner = format_evidence_completeness_notice(
        completeness=pack.completeness,
        path="evidence_pack",
    )
    return (
        "【共享证据包·Evidence Pack】\n"
        "本场附件/底料已组装为双方共享事实库（下列为来源索引+摘录，非对抗论证）；"
        "开辩后各方在共享包上对抗，【禁止】再对同一附件各自深挖重复取证。"
        f" 完整度={pack.completeness}。\n"
        f"{incomplete_banner}"
        f"{body}"
        f"{dispute_lines}"
        f"{ledger_lines}"
    )


def register_evidence_pack_on_ledger(ledger: Any, pack: EvidencePack) -> EvidencePack:
    """把可用来源登记进场级台账；回填 ``ledger_ids``。"""
    ids: dict[str, str] = dict(pack.ledger_ids)
    for s in pack.sources:
        if s.source_id in ids:
            continue
        if not (s.excerpt or "").strip() and s.failure in ("binary_no_text", "empty_body"):
            continue
        eid = ledger.register(
            url="",
            title=s.label,
            snippet=(s.excerpt or "")[:240],
            site=s.label,
            side_key=_PACK_SIDE_KEY,
            tier="unknown",
            dossier_path=s.path,
            dossier_label=s.label,
        )
        ids[s.source_id] = eid
    pack.ledger_ids = ids
    return pack


def merge_pack_into_dossier_index(existing: str, pack: EvidencePack) -> str:
    """把证据包索引并入约定文档索引通道（复用既有辩手注入点）。"""
    pack_block = format_evidence_pack_index(pack)
    if not pack_block:
        return existing or ""
    prev = (existing or "").strip()
    if not prev:
        return pack_block
    return f"{prev}\n\n{pack_block}"
