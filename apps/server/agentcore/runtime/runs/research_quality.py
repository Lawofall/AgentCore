"""调研/实务成篇质量策略（通用，非法务专用通道）。

定案：大纲按章落盘 / 空检索换策略 / 空 handoff 挡写作 /
成篇审计硬门 / 论文并行拆章须单主文件合并门禁。
本模块只放纯谓词与文案常量，供 skill、检索预算、audit gate、
delivery_status 复用——不新建子系统。

成篇硬审计不扫 task/角色自由文猜意图；不认已删字数字段腿。
``reviews/`` 路径前缀闸已卸：不再按约定柜生成 thin_review / literature
证据降档；声明未落盘仍走通用 ``files_not_landed``。
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

# 论文/综述/长文成篇：允许并行拆章起草，但最终验收必须单一主文件（定案：
# 禁的是「并行拆章无合并门禁」，不是双文件本身；调研/代码/建站多产物不受本条约束）。
PAPER_PARALLEL_MERGE_DISCIPLINE = (
    "【论文/综述·单主文件门禁】允许并行拆章起草到临时路径，但最终交付验收必须是"
    "【同一主文件】；并行各章 brief 须写死同一目标路径 + 合并责任"
    "（末尾 merge worker，或 CEO 收口合并）。禁止各章各交各的当终稿。"
    "调研透镜 / 代码 / 建站等多产物场景不受本条约束。"
)

# 出行/报告成文：主交付永远是 .md；用户要 PDF/Word/可分享时才 md→md_export→handoff。
# 导出器是确定性 FILESYSTEM 工具、与执行沙箱正交，无 code_execute 也能交。
MD_EXPORT_DISCIPLINE = (
    "【成文交付·MD 为主】主交付永远是 `.md`。"
    "用户要 PDF / Word / 可分享文件时：顺序 = 成篇 `.md` → 调用 `md_export`"
    "（`format=pdf` 或 `format=docx`，对主文件）→ handoff；确定性导出、不依赖执行沙箱。"
    "【禁止】用多份 HTML 顶替 PDF；【禁止】把 code_execute + reportlab / python-docx 当主路径"
    "（确定性 `md_export` 才是主路径）。"
)

# 成篇主文件：手写 ``output_path`` / ``artifacts`` 钉路径；引擎不发明默认柜。

# 本地改文件 / 广度摸底 / 成篇意图 / 字数承诺：用户·task 文 RE 猜意图腿已撤；
# 成篇硬门不扫自由文分叉；选型靠提示词，硬门只认结构字段。


def has_landed_prose_artifact(kinds: object) -> bool:
    """True when this run already landed at least one ``prose`` artifact.

    Reads ``ToolContext.landed_artifact_kinds`` (shared mutable dict that survives
    ``dataclasses.replace``). Skeleton / empty landings do **not** count — only
    prose exempts the upstream body floor at handoff / executor completion.
    """
    if not isinstance(kinds, dict) or not kinds:
        return False
    return any(v == "prose" for v in kinds.values())


def upstream_body_floor_satisfied(
    *,
    body_chars: int,
    landed_artifact_kinds: object,
    min_body_chars: int = 0,
) -> bool:
    """Upstream floor for handoff / executor completion.

    ``min_body_chars`` is an optional internal floor (0 = 非空即可；不暴露 CEO 合同字段）。
    已落盘 prose 一律满足。无地板时：非空正文即视为可消费产出（空交不再硬拒）；
    有地板时须 ``body ≥ min``。
    """
    if has_landed_prose_artifact(landed_artifact_kinds):
        return True
    floor = max(0, int(min_body_chars or 0))
    n = int(body_chars or 0)
    if floor <= 0:
        return n > 0
    return n >= floor


def brief_may_satisfy_body_floor(*, expects_landing: bool) -> bool:
    """Whether ``promote_brief_to_deliverable`` may count toward the upstream floor.

    Not-landing + 有下游交接地板：只认 ``round_content_chars`` / 已落盘 prose，
    便条不算交付正文。Pinned landing still allows the brief to stand in.
    """
    return expects_landing


def promote_brief_to_deliverable(
    summary: str,
    key_points: object = None,
) -> str:
    """升格交接简报为下游可读候选正文（同轮正文 0 字时的交付替身）。

    ``summary`` 为首段；非空 ``key_points`` 附作 ``- …`` 列表。空 summary → ``""``
    （不豁免空交）。handoff / 收工闸 / 依赖注入共用。
    有下游的 prose 交接地板不得用本函数冒充正文——见 ``brief_may_satisfy_body_floor``。
    """
    head = (summary or "").strip()
    if not head:
        return ""
    points: list[str] = []
    if isinstance(key_points, list):
        for raw in key_points:
            item = str(raw).strip()
            if item:
                points.append(item)
    elif key_points is not None:
        item = str(key_points).strip()
        if item:
            points.append(item)
    if not points:
        return head
    bullets = "\n".join(f"- {p}" for p in points)
    return f"{head}\n\n{bullets}"


def deliverable_signals_long_form(deliverable: Any) -> bool:
    """Retired: long-form audit no longer keys off deleted length fields.

    Always False. Kept so call sites / tests can assert the leg is gone.
    """
    _ = deliverable
    return False


def plan_signals_long_form_audit(plan_nodes: object) -> bool:
    """Retired: hard audit no longer keys off a named pipeline.

    Does **not** scan free-text ``task`` / ``role`` or deleted length fields.
    """
    _ = plan_nodes
    return False


def research_report_main_artifact(output_path: str | None = None) -> str:
    """Single main-file path for formal long-form acceptance (merge gate)."""
    cleaned = (output_path or "").strip().replace("\\", "/")
    if cleaned:
        return cleaned.lstrip("/")
    return cleaned.lstrip("/") if cleaned else ""


# ── 文献成文证据降档（delivery_status 消费）────────────────────────────────

# Machine gap reason — mirrored as REASON_EVIDENCE_DEFICIT in delivery_status.
REASON_EVIDENCE_DEFICIT = "evidence_deficit"

# ── 与「学术搜索块」的稳定接缝（字段名约定；本块只读、不写搜索实现）────────
# 学术搜索块在 junk / uniformly_weak / 空注入时，应至少留下下列之一，供本模块消费：
#
# 1) RunState.delivery_gaps 行：``{"reason": "evidence_deficit", "description": "..."}``
#    （经 ``collect_worker_gaps`` 进卡；本谓词不再重复生成）
# 2) RunState 可选属性（getattr，不强制改 types 契约）：
#    - ``evidence_gap: bool``（真源；executor 可从 RetrievalBudgetState sticky 落盘）
#    - ``evidence_deficit: bool``（兼容旧戳）
#    - ``evidence_meta`` / ``search_evidence`` dict，键见下
# 3) web_search ToolResult.output JSON（进 transcript 的 tool content）或 metadata：
#    - ``evidence_gap``: bool（搜索真源）
#    - ``evidence_deficit``: bool（兼容）
#    - ``evidence_quality``: ``"poor"``（或其它非 ok）
#    - ``academic_usable_count``: int（0 = 本轮无学术可用命中）
#    - ``search_policy``: ``"academic_literature"``（或含 ``academic`` 的 policy 名；
#      旧名 ``academic_evidence`` 作别名）
#    - 既有：``low_relevance`` / ``empty`` / ``empty_streak``（有 academic policy 时计）
#
# 降档仍可由「几乎无学术可用源」与「无参考文献·靠先验」可观测缺口触发。
# 非文献形态（无 reviews/ 审校座）会在 delivery_status 丢弃误入的 evidence_deficit。

EVIDENCE_GAP_KEY = "evidence_gap"
EVIDENCE_DEFICIT_KEY = "evidence_deficit"
EVIDENCE_QUALITY_KEY = "evidence_quality"
ACADEMIC_USABLE_COUNT_KEY = "academic_usable_count"
# Align with tools.builtin.web.relevance.SEARCH_POLICY_ACADEMIC_LITERATURE.
SEARCH_POLICY_ACADEMIC_LITERATURE = "academic_literature"
EVIDENCE_QUALITY_POOR = "poor"

# 有检索痕迹但学术可用源极少 → 近零误报：至少若干非学术 citation 才认「搜了但水」。
_MIN_CITATIONS_FOR_ACADEMIC_GAP = 2

# 论文库 / DOI / 预印本等（假设名单；与学术搜索偏置对齐，非允许域硬闸）。
_ACADEMIC_HOST_SUFFIXES: frozenset[str] = frozenset(
    {
        "arxiv.org",
        "pubmed.ncbi.nlm.nih.gov",
        "ncbi.nlm.nih.gov",
        "doi.org",
        "dx.doi.org",
        "semanticscholar.org",
        "acm.org",
        "dl.acm.org",
        "ieee.org",
        "ieeexplore.ieee.org",
        "springer.com",
        "link.springer.com",
        "nature.com",
        "science.org",
        "sciencedirect.com",
        "wiley.com",
        "onlinelibrary.wiley.com",
        "nih.gov",
        "biorxiv.org",
        "medrxiv.org",
        "ssrn.com",
        "openalex.org",
        "crossref.org",
        "jstor.org",
        "plos.org",
        "frontiersin.org",
        "mdpi.com",
        "tandfonline.com",
        "sagepub.com",
        "oup.com",
        "academic.oup.com",
        "cell.com",
        "thelancet.com",
        "nejm.org",
        "bmj.com",
        "cochranelibrary.com",
        "who.int",
    }
)

# 成稿/审校可观测「无参考文献 · 靠先验」缺口（窄匹配；禁完成话术词）。
_NO_REFS_OR_PRIOR_MARKERS: tuple[str, ...] = (
    "无参考文献",
    "没有参考文献",
    "缺少参考文献",
    "未附参考文献",
    "无引用文献",
    "靠先验",
    "基于先验",
    "基于对该领域的了解",
    "基于已有知识",
    "基于模型知识",
    "搜不到文献",
    "未能检索到相关文献",
    "常规搜索拿不到",
)

_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def plan_is_literature_report_delivery(plan_nodes: object) -> bool:
    """Retired: literature shape no longer keys off a ``reviews/`` cabinet.

    Always False. Kept so delivery_status / tests can assert the leg is gone.
    """
    _ = plan_nodes
    return False


def is_academic_usable_url(url: str) -> bool:
    """True when URL host looks like a paper / DOI / preprint venue."""
    host = urlparse(url if "://" in (url or "") else f"https://{url or ''}").netloc.casefold()
    host = host.removeprefix("www.")
    if not host:
        return False
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _ACADEMIC_HOST_SUFFIXES
    )


def academic_usable_citation_count(citations: object) -> int:
    """Count citations whose URL is an academic-usable host (dedupe by URL)."""
    if not isinstance(citations, list):
        return 0
    seen: set[str] = set()
    n = 0
    for row in citations:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        if is_academic_usable_url(url):
            n += 1
    return n


def _citation_total(citations: object) -> int:
    if not isinstance(citations, list):
        return 0
    seen: set[str] = set()
    for row in citations:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url and url not in seen:
            seen.add(url)
    return len(seen)


def _meta_signals_evidence_deficit(meta: object) -> bool:
    """True when a search/tool metadata dict carries a structured evidence-gap stamp."""
    if not isinstance(meta, dict) or not meta:
        return False
    # Search-side true source (academic_literature junk / empty inject).
    if meta.get(EVIDENCE_GAP_KEY) is True:
        return True
    # Legacy stamp — keep reading so older workers / fixtures still trip.
    if meta.get(EVIDENCE_DEFICIT_KEY) is True:
        return True
    quality = str(meta.get(EVIDENCE_QUALITY_KEY) or "").strip().casefold()
    if quality and quality == EVIDENCE_QUALITY_POOR:
        return True
    policy = str(meta.get("search_policy") or "").strip().casefold()
    academic_policy = (
        policy == SEARCH_POLICY_ACADEMIC_LITERATURE.casefold()
        or policy == "academic_evidence"
        or "academic" in policy
    )
    if academic_policy:
        if meta.get("low_relevance") is True or meta.get("empty") is True:
            return True
        try:
            streak = int(meta.get("empty_streak") or 0)
        except (TypeError, ValueError):
            streak = 0
        if streak >= 2:
            return True
        if ACADEMIC_USABLE_COUNT_KEY in meta:
            try:
                if int(meta.get(ACADEMIC_USABLE_COUNT_KEY) or 0) <= 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def _parse_json_object(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        match = _JSON_OBJECT_RE.search(raw)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            return None
    return data if isinstance(data, dict) else None


def _transcript_search_evidence_deficit(transcript: object) -> bool:
    """Scan web_search tool messages for structured evidence-gap fields in output JSON."""
    if not isinstance(transcript, (list, tuple)) or not transcript:
        return False
    call_names: dict[str, str] = {}
    for msg in transcript:
        tool_calls = getattr(msg, "tool_calls", None)
        if getattr(msg, "role", None) == "assistant" and tool_calls:
            for tc in tool_calls:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") if fn is not None else ""
                tc_id = getattr(tc, "id", "") or ""
                if tc_id and name:
                    call_names[tc_id] = str(name)
        if getattr(msg, "role", None) != "tool":
            continue
        tc_id = getattr(msg, "tool_call_id", None) or ""
        if call_names.get(str(tc_id)) != "web_search":
            continue
        payload = _parse_json_object(str(getattr(msg, "content", "") or ""))
        if payload is not None and _meta_signals_evidence_deficit(payload):
            return True
    return False


def _state_structured_evidence_deficit(state: Any) -> bool:
    """True when this RunState already carries a structured evidence-gap stamp.

    Prefers search-side stamps (``evidence_gap`` attr / sticky via
    ``evidence_meta`` / ``search_evidence`` / web_search transcript JSON).
    ``evidence_deficit`` attr remains a compatibility read. Does **not** re-read
    ``delivery_gaps`` rows with ``reason=evidence_deficit`` — those already flow
    through ``collect_worker_gaps`` into the delivery card.
    """
    if state is None:
        return False
    if getattr(state, EVIDENCE_GAP_KEY, False) is True:
        return True
    if getattr(state, EVIDENCE_DEFICIT_KEY, False) is True:
        return True
    for key in ("evidence_meta", "search_evidence"):
        if _meta_signals_evidence_deficit(getattr(state, key, None)):
            return True
    return _transcript_search_evidence_deficit(getattr(state, "transcript", None))


def _batch_has_structured_evidence_deficit(results: dict[str, Any]) -> bool:
    return any(_state_structured_evidence_deficit(st) for st in (results or {}).values())


def _batch_almost_no_academic_sources(results: dict[str, Any]) -> bool:
    """True when the batch consulted sources but almost none are academic-usable."""
    all_cites: list[dict[str, Any]] = []
    for state in (results or {}).values():
        if state is None:
            continue
        for row in getattr(state, "citations", None) or []:
            if isinstance(row, dict):
                all_cites.append(row)
    total = _citation_total(all_cites)
    if total < _MIN_CITATIONS_FOR_ACADEMIC_GAP:
        return False
    return academic_usable_citation_count(all_cites) == 0


def _text_has_no_refs_or_prior_gap(text: str) -> bool:
    return any(marker in (text or "") for marker in _NO_REFS_OR_PRIOR_MARKERS)


def _batch_has_no_refs_or_prior_gap(
    plan_nodes: object,
    results: dict[str, Any],
) -> bool:
    """True when any worker surface admits no-refs/prior."""
    for _rid, state in (results or {}).items():
        if state is None:
            continue
        surfaces: list[str] = [
            str(getattr(state, "content", "") or ""),
            *[str(w) for w in (getattr(state, "warnings", None) or [])],
        ]
        debrief = getattr(state, "debrief", None)
        if isinstance(debrief, dict):
            surfaces.append(str(debrief.get("summary") or ""))
            for kp in debrief.get("key_points") or []:
                surfaces.append(str(kp))
            for a in debrief.get("assumptions") or []:
                surfaces.append(str(a))
        for row in getattr(state, "delivery_gaps", None) or []:
            if isinstance(row, dict):
                surfaces.append(str(row.get("description") or ""))
        if any(_text_has_no_refs_or_prior_gap(s) for s in surfaces):
            return True
    return False


def literature_evidence_deficit_hit(
    plan_nodes: object,
    results: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Return ``(hit, reason_bits)`` for literature-report evidence deficit (combinable)."""
    if not plan_is_literature_report_delivery(plan_nodes):
        return False, []
    bits: list[str] = []
    if _batch_has_structured_evidence_deficit(results):
        bits.append("学术检索侧结构化证据差信号")
    if _batch_almost_no_academic_sources(results):
        bits.append("几乎无学术可用源")
    if _batch_has_no_refs_or_prior_gap(plan_nodes, results):
        bits.append("成稿或审校标明无参考文献或靠先验")
    return bool(bits), bits


def collect_evidence_deficit_gaps(
    plan_nodes: object,
    results: dict[str, Any],
) -> list[dict[str, str]]:
    """Blocking gap rows for ``delivery_status`` when literature evidence is insufficient.

    Empty when not a literature-report shape, or evidence looks adequate.
    Does **not** scan completion-claim phrases（如「综述已完成」）.
    """
    hit, bits = literature_evidence_deficit_hit(plan_nodes, results)
    if not hit:
        return []
    detail = "；".join(bits) if bits else "证据不足"
    return [
        {
            "description": (
                f"文献成文证据不足，本批仅能按草稿/部分交付（{detail}）"
            ),
            "reason": REASON_EVIDENCE_DEFICIT,
        }
    ]


# ── 复核落盘对账 ────────────────────────────────────────────────────────
# ``reviews/`` 路径前缀闸已卸。历史 ``reason=thin_review`` 仍可出现在工人
# ``delivery_gaps``（经 collect_worker_gaps 进卡）；本模块不再按路径生成。

REASON_THIN_REVIEW = "thin_review"


def collect_thin_review_gaps(
    plan_nodes: object,
    results: dict[str, Any],
) -> list[dict[str, str]]:
    """Retired: no ``reviews/`` cabinet gate. Always empty."""
    _ = plan_nodes, results
    return []
