"""Web 来源（引用）的合并、编号与标注。

一个独立的叶子模块（不依赖 engine / runs / tools），因此 CEO 回合（engine）、
worker 执行器（runs.executor）、委派工具（tools.delegate）与回合管线（pipeline）
都能复用同一套去重/编号逻辑，而不会引入循环导入。

编号 = 来源在 sink 中的 1-based 序号 = 客户端「来源」卡渲染的序号；engine 在 CEO
路径把这个号折回工具输出（:func:`annotate_tool_citations`），让模型按号引用，正文
里的 [n] 始终对得上卡片。worker 路径只做合并、不做标注（见 engine 的 annotate
开关），因为 worker 的本地编号会在汇入回合卡时被重排，标注反而会误导。

域名分级单源在 :mod:`agentcore.core.citation_tier`（叶工具可直接引用，不碰 runtime）。
本模块 re-export，供 runtime / 台账既有 import 路径保持稳定。
"""

import re
from typing import Any

from agentcore.core.citation_tier import (
    CitationTier as CitationTier,
)
from agentcore.core.citation_tier import (
    citation_pool_admissible,
    normalize_citation_url,
    stamp_citation_tier,
)
from agentcore.core.citation_tier import (
    citation_tier_for_url as citation_tier_for_url,
)

# P2：来源卡 = 成稿引用集（无硬帽；旧池帽 24 随池语义退役）。
# mid-turn ``merge_citations`` 仍汇入检索命中供 pause 快照 / 遗留 ``[n]`` 双轨，
# 仅拒 ``blocked``（``weak`` 可进 sink，卡片是否挂出由 settle 按 cited_ids 投影）。


def _citation_key(citation: dict[str, Any]) -> str:
    """来源去重用的归一化键（同一页面被 search + read_url、或被多个引擎命中时合并）：
    去掉 ``#fragment`` 与结尾的 ``/``。"""
    return normalize_citation_url(citation.get("url") or "")


def ledger_entry_to_citation(entry: dict[str, Any]) -> dict[str, Any]:
    """台账条目 → ``citations_event`` 来源卡形状（含 tier / id，供弱源徽标）。"""
    return {
        "url": str(entry.get("url") or ""),
        "title": str(entry.get("title") or ""),
        "snippet": str(entry.get("snippet") or ""),
        "site": str(entry.get("site") or ""),
        "id": entry.get("id"),
        "date": str(entry.get("date") or ""),
        "tier": str(entry.get("tier") or "unknown"),
        "query": str(entry.get("query") or ""),
        "deep_read": bool(entry.get("deep_read")),
        "registrant": str(entry.get("registrant") or ""),
        "citable": bool(entry["citable"]) if "citable" in entry else True,
    }


def project_cited_citations(
    ledger_entries: list[dict[str, Any]],
    cited_ids: list[str],
) -> list[dict[str, Any]]:
    """P2：按成稿 ``cited_ids``（首次出现序）从台账投影来源卡——**无硬帽**。

    未出现在 ``cited_ids`` 的台账条目是检索痕迹，不进 ``citations_event``。
    未知 id 与 ``citable=false``（blocked）跳过——未登记号留白字，不剥正文。
    """
    by_id: dict[str, dict[str, Any]] = {}
    for e in ledger_entries:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        if eid:
            by_id[eid] = e
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for eid in cited_ids:
        if not eid or eid in seen:
            continue
        seen.add(eid)
        entry = by_id.get(eid)
        if entry is None or not entry.get("citable", True):
            continue
        out.append(ledger_entry_to_citation(entry))
    return out


def merge_citations(sink: list[dict[str, Any]], new: list[dict[str, Any]]) -> dict[str, int]:
    """把 ``new`` 合并进 mid-turn ``sink``（按到达顺序、去重），返回编号映射。

    规范编号是来源在 ``sink`` 中的 1-based 序号——遗留 ``[n]`` 双轨 / pause 快照用。
    P2 起用户可见来源卡由 :func:`project_cited_citations` 按 ``cited_ids`` 投影，
    **不再**把本 sink 整批当作 ``citations_event``（亦无 24 帽）。

    入库过滤：仅 ``blocked`` 拒收；其余档（含 ``weak``）写入并戳 ``tier``。
    """
    numbers: dict[str, int] = {}
    seen = {_citation_key(c): i + 1 for i, c in enumerate(sink)}
    for c in new:
        key = _citation_key(c)
        if not key:
            continue
        stamped = stamp_citation_tier(c)
        tier = str(stamped.get("tier") or "unknown")
        if not citation_pool_admissible(tier):
            continue
        existing = seen.get(key)
        if existing is not None:
            numbers[key] = existing
            continue
        sink.append(stamped)
        number = len(sink)
        seen[key] = number
        numbers[key] = number
    return numbers


# A2 引用编号：每条 web 工具结果都被标注上 engine 为其来源分配的规范编号（= 来源卡
# 序号）。模型用这些确切编号引用，于是正文 [n] 总能解析到正确的卡片——而非自己猜一个
# 后端按到达顺序独立分配的序号（那在乱序使用、子集、去重与限量时都会错位）。
_CITATION_NUMBER_HINT = "\n\n[来源编号] 上述来源对应的引用号，正文中用方括号角标引用（如 [1]）："

# 引用即出处 P1：回合共享台账 stable id（``#rN``）注解——汇入后不变，禁止 handoff 重排。
_CITATION_LEDGER_HINT = (
    "\n\n[已登记来源] 台账 id（汇入后不变）；成稿挂 #rN 须已深读或 selected"
    "（仅 search 登记不可）；笔记行尾 / 正文按下列 id 引用："
)


def annotate_tool_citations(
    content: str, citations: list[dict[str, Any]], numbers: dict[str, int]
) -> str:
    """把「来源→编号」映射追加到一条工具消息的模型可见输出末尾。

    对 engine 编了号的每个来源列出 ``[n]=url``（按结果自身顺序），让模型按固定编号引用
    而不自己编。被每回合上限丢弃（无编号）的来源略去；结果内重复出现的来源按编号合并。
    若没有任何来源带编号，原样返回 ``content``。

    新 run 在接通回合台账后改走 :func:`annotate_ledger_ids`（``#rN=url``）；本函数保留给
    无台账的兼容路径与单测。
    """
    seen: set[int] = set()
    entries: list[str] = []
    for citation in citations:
        number = numbers.get(_citation_key(citation))
        if number is None or number in seen:
            continue
        seen.add(number)
        entries.append(f"[{number}]={citation.get('url') or ''}")
    if not entries:
        return content
    return f"{content}{_CITATION_NUMBER_HINT}{' '.join(entries)}"


def annotate_ledger_ids(
    content: str, citations: list[dict[str, Any]], ids: dict[str, str]
) -> str:
    """把「来源→台账 stable id」映射追加到工具消息末尾（``#rN=url``）。

    ``ids`` 键为归一化 URL（:func:`normalize_citation_url`）。未登记（如 blocked）的
    来源略去；同 id 去重。无任何 id 时原样返回 ``content``。
    """
    seen: set[str] = set()
    entries: list[str] = []
    for citation in citations:
        key = _citation_key(citation)
        eid = ids.get(key) if key else None
        if not eid or eid in seen:
            continue
        seen.add(eid)
        entries.append(f"{eid}={citation.get('url') or ''}")
    if not entries:
        return content
    return f"{content}{_CITATION_LEDGER_HINT}{' '.join(entries)}"


# 客户端把正文里的 [n] 渲染成可点的来源角标，但只解析 1..来源数；越界的 [n]（模型
# 引用了一个没有对应卡片的编号——多半是数错或想指上一轮的号）会被原样留成纯文本。
# 服务端在 message_end 前用下面这支度量这种「引用了不存在来源」的发生率；对话出口
# :func:`reconcile_citations` 只观测、不剥正文。落盘成文剥号走文件合同。
#
# P1 双轨：``[n]``（池序）与 ``#rN``（回合台账 id）并存——历史消息按 Q10 双轨解析；
# 新 run 真理层为 ``#rN``。
_MARKER_RE = re.compile(r"\[(\d+)\]")
_LEDGER_REF_RE = re.compile(r"#r(\d+)\b")
# 扫描前先抠掉代码块 / 行内代码 / Markdown 链接：里头的 [5]、[label](url) 是数组
# 下标、代码样例或链接锚，不是引用角标（客户端渲染也跳过它们），抠掉以免误报越界。
_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")
# 剥离悬空角标后收尾空白/标点空隙，避免「声称 [9]。」变成「声称 。」
_STRIP_SPACE_PUNCT_RE = re.compile(r" +([.,;:!?。，；：！？、])")
_STRIP_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")
_PROTECT_SENTINEL = "\x00P{0}\x00"
_PROTECT_RESTORE_RE = re.compile(r"\x00P(\d+)\x00")


def _scannable_citation_text(content: str) -> str:
    """抠掉代码块 / 行内代码 / Markdown 链接后的可扫正文（``[n]`` / ``#rN`` 共用）。"""
    if not content:
        return ""
    scannable = _FENCED_CODE_RE.sub(" ", content)
    scannable = _INLINE_CODE_RE.sub(" ", scannable)
    return _MD_LINK_RE.sub(" ", scannable)


def out_of_range_markers(content: str, citation_count: int) -> list[int]:
    """返回 ``content`` 正文里指向「不存在来源卡」的引用角标编号（升序去重）。

    合法编号是 ``1..citation_count``（= 来源卡数）。返回 ``n < 1`` 或 ``n > 上限``
    的那些——客户端只把 ``1..上限`` 渲染成可点角标、越界的留成纯文本，即模型引用了
    一个没有卡片的编号。仅用于可观测度量；对话出口不剥正文。

    扫描前抠掉代码块 / 行内代码 / Markdown 链接，镜像客户端 remark 插件的跳过规则，
    避免把 ``arr[5]`` 这类下标误判成越界引用。裸正文里的 ``[5]`` 与客户端同样无法
    区分是否引用，按客户端语义一并计入。
    """
    scannable = _scannable_citation_text(content)
    if not scannable:
        return []
    bad = {
        n for n in (int(x) for x in _MARKER_RE.findall(scannable)) if n < 1 or n > citation_count
    }
    return sorted(bad)


def extract_ledger_ref_ids(content: str) -> list[str]:
    """正文中的约定台账引用标记 ``#rN``（首次出现序、去重；跳过代码/链接）。"""
    scannable = _scannable_citation_text(content)
    if not scannable:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for n in _LEDGER_REF_RE.findall(scannable):
        eid = f"#r{n}"
        if eid in seen:
            continue
        seen.add(eid)
        out.append(eid)
    return out


def invalid_ledger_ref_ids(
    content: str, citable_ids: frozenset[str] | set[str] | None
) -> list[str]:
    """成稿中 ∉ 可引用台账 id 的 ``#rN``（升序）。

    Q5：正文无任何 ``#rN`` 时返回空——闸不启用、不回炉。
    ``citable_ids`` 为 ``None`` 时不做校验（调用方未接通台账）。
    """
    refs = extract_ledger_ref_ids(content)
    if not refs or citable_ids is None:
        return []
    known = set(citable_ids)
    return [r for r in refs if r not in known]


def _protect_non_citation_spans(content: str) -> tuple[str, list[str]]:
    """Replace fenced/inline code and markdown links with sentinels (restore later)."""
    placeholders: list[str] = []

    def _protect(pattern: re.Pattern[str], text: str) -> str:
        def repl(match: re.Match[str]) -> str:
            placeholders.append(match.group(0))
            return _PROTECT_SENTINEL.format(len(placeholders) - 1)

        return pattern.sub(repl, text)

    text = _protect(_FENCED_CODE_RE, content)
    text = _protect(_INLINE_CODE_RE, text)
    text = _protect(_MD_LINK_RE, text)
    return text, placeholders


def _cleanup_after_strip(text: str) -> str:
    text = _STRIP_MULTI_SPACE_RE.sub(" ", text)
    return _STRIP_SPACE_PUNCT_RE.sub(r"\1", text)


def strip_out_of_range_markers(content: str, citation_count: int) -> str:
    """从正文剥离悬空 ``[n]`` 角标（越界或来源卡为空），保持代码/链接不动、文句通顺。

    合法 ``1..citation_count`` 角标保留；剥离后折叠多余空白与「词 + 标点」空隙。
    """
    if not content:
        return content
    text, placeholders = _protect_non_citation_spans(content)

    def _strip_or_keep(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if n < 1 or n > citation_count:
            return ""
        return match.group(0)

    text = _MARKER_RE.sub(_strip_or_keep, text)
    text = _cleanup_after_strip(text)
    if placeholders:
        text = _PROTECT_RESTORE_RE.sub(lambda m: placeholders[int(m.group(1))], text)
    return text


def strip_invalid_ledger_refs(content: str, invalid_ids: set[str] | frozenset[str]) -> str:
    """从正文剥离非法 ``#rN``（∉ 可引用台账），保持代码/链接不动。"""
    if not content or not invalid_ids:
        return content
    text, placeholders = _protect_non_citation_spans(content)

    def _strip_or_keep(match: re.Match[str]) -> str:
        eid = f"#r{match.group(1)}"
        if eid in invalid_ids:
            return ""
        return match.group(0)

    text = _LEDGER_REF_RE.sub(_strip_or_keep, text)
    text = _cleanup_after_strip(text)
    if placeholders:
        text = _PROTECT_RESTORE_RE.sub(lambda m: placeholders[int(m.group(1))], text)
    return text


def reconcile_citations(
    content: str,
    citations: list[dict[str, Any]],
    *,
    citable_ids: frozenset[str] | set[str] | None = None,
) -> tuple[str, list[dict[str, Any]], list[int], list[str]]:
    """对话出口观测：报告悬空 ``[n]`` / 非登记 ``#rN``，**不剥正文**。

    来源卡由 :func:`project_cited_citations` 按已登记 ``citable`` id 投影；
    未登记号留白字。落盘成文剥号走合同闸，不走本函数。
    """
    stray_n = out_of_range_markers(content, len(citations))
    stray_r = invalid_ledger_ref_ids(content, citable_ids)
    return content, citations, stray_n, stray_r


def stamp_citations_from_ledger(
    citations: list[dict[str, Any]],
    ledger_entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """按归一化 URL 把台账 id / 元数据盖到 mid-turn sink 条目上（兼容 / 单测）。

    P2 收口卡片投影走 :func:`project_cited_citations`；本函数保留给无 cited 投影
    的遗留路径。缺台账命中时保留原条；legacy 缺字段不炸。返回新列表（不原地改）。
    """
    by_url: dict[str, dict[str, Any]] = {}
    for e in ledger_entries:
        if not isinstance(e, dict):
            continue
        key = normalize_citation_url(str(e.get("url") or ""))
        if key:
            by_url[key] = e
    out: list[dict[str, Any]] = []
    for c in citations:
        stamped = dict(c)
        key = _citation_key(stamped)
        entry = by_url.get(key) if key else None
        if entry is not None:
            if entry.get("id") and not stamped.get("id"):
                stamped["id"] = entry["id"]
            for field in ("query", "deep_read", "registrant", "citable", "date", "tier"):
                if (field not in stamped or stamped.get(field) in (None, "")) and field in entry:
                    stamped[field] = entry[field]
        out.append(stamped)
    return out
