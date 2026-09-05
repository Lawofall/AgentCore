"""场级证据台账（evidence_ledger）——辩论薄封装，核心委托平台共享核。

与 :mod:`match_ledger`（对局状态事件）分工不同：本模块登记的是「本场被真正消费的来源」
（``web_fetch`` 深读页 + 笔记实际引用的 search 命中 + 底料预登记），成稿
``【已核实·#eN】`` 的机械闸基准是**本方笔记引用集**（结辩 = 本方历轮已引用并集）。

规则（提案 O1 / O4 / 咬合点；共享核见 :mod:`agentcore.runtime.evidence_ledger`）：
- append-only，id = ``#e{n}``（登记序）
- 同 URL（归一化）去重 → 返回既有 id
- 空 URL（底料预登记）按归一化 title 去重
- 条目记登记方 ``side_key``（主持人预登记 = ``moderator``）；共享核内别名 ``registrant``
- **wire 形状冻结**（仅 id/url/title/snippet/site/date/tier/side_key）；不向辩论事件
  泄露 query/deep_read/citable/registrant
- **sink 登记语义**：``reject_blocked=False``；检索期可先写入共享核供工具注解 ``#eN``，
  仅 ``commit_research`` 后的消费子集进入 wire（``all_entries`` / ``drain_delta``）
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from typing import Any

from agentcore.runtime.debate.evidence_guard import extract_verified_tags
from agentcore.runtime.evidence_ledger import EvidenceLedgerCore

# 主持人底料预登记的登记方键（非辩手 side_key）。
MODERATOR_SIDE_KEY = "moderator"

_VERIFIED_PREFIX = "【已核实·"
_ID_IN_NOTE_RE = re.compile(r"#e(\d+)\b")
# 底料中 CEO 回合引用：不用 \b（中文邻接时 \b 失效，会漏掉「#r2与」这类）。
_CEO_R_REF_RE = re.compile(r"#r(\d+)(?!\d)")

# 辩论 wire / 对外快照字段（与 EvidenceLedgerEntry 对齐；约定文档锚 additive）。
_DEBATE_WIRE_KEYS = (
    "id",
    "url",
    "title",
    "snippet",
    "site",
    "date",
    "tier",
    "side_key",
    "dossier_path",
    "origin_id",
    "dossier_label",
)


def extract_ledger_ids(text: str) -> frozenset[str]:
    """从笔记 / 发言正文抽取 ``#eN`` id 集（稳定、机械）。"""
    return frozenset(f"#e{m.group(1)}" for m in _ID_IN_NOTE_RE.finditer(text or ""))


def side_cited_ledger_ids(
    rounds: Sequence[Any],
    side_key: str,
    *,
    transcript: Sequence[Any] | None = None,
) -> frozenset[str]:
    """本方历轮已引用的 ``#eN`` 并集（结辩闸基准）。

    来源：各轮 ``SideTurn.content``、质询作答摘要、可选 session transcript 中 assistant 正文。
    """
    ids: set[str] = set()
    for rr in rounds or ():
        for turn in getattr(rr, "turns", ()) or ():
            if getattr(turn, "side_key", None) == side_key:
                ids |= extract_ledger_ids(getattr(turn, "content", "") or "")
        for cx in getattr(rr, "cross_exam", ()) or ():
            if getattr(cx, "target", None) != side_key:
                continue
            for ex in getattr(cx, "exchanges", ()) or ():
                ids |= extract_ledger_ids(getattr(ex, "answer", "") or "")
    if transcript:
        for msg in transcript:
            if getattr(msg, "role", None) == "assistant":
                ids |= extract_ledger_ids(getattr(msg, "content", "") or "")
    return frozenset(ids)


def _to_debate_wire(entry: dict[str, Any]) -> dict[str, Any]:
    """共享核条目 → 辩论 wire（registrant ↔ side_key；剥元数据）。"""
    out: dict[str, Any] = {}
    for key in _DEBATE_WIRE_KEYS:
        if key == "side_key":
            out["side_key"] = (
                entry.get("side_key") or entry.get("registrant") or ""
            )
        elif key == "tier":
            out["tier"] = entry.get("tier") or "unknown"
        else:
            out[key] = entry.get(key, "")
    return out


class _SuppressDeltaLedger:
    """包装共享核：检索期 register + ``#eN`` 注解照常，``drain_delta`` 恒空以免误发回合 SSE。"""

    def __init__(self, core: EvidenceLedgerCore) -> None:
        self._core = core

    def drain_delta(self) -> list[dict[str, Any]]:
        return []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._core, name)


class EvidenceLedger:
    """场级合并池：单协程编排；登记委托 :class:`EvidenceLedgerCore`。

    检索期命中可先进入共享核（供工具消息注解 id）；只有
    :meth:`commit_research` / 显式 :meth:`register` 提交的子集进 wire。
    """

    def __init__(self) -> None:
        # reject_blocked=False：保持 M1 sink 登记过滤语义（不在此层拒 blocked）。
        self._core = EvidenceLedgerCore(id_prefix="#e", reject_blocked=False)
        self._committed: set[str] = set()
        self._drained: set[str] = set()

    def research_proxy(self) -> _SuppressDeltaLedger:
        """供 ``react_loop(turn_evidence_ledger=…)``：注解 ``#eN`` 且不发射回合台账 SSE。"""
        return _SuppressDeltaLedger(self._core)

    def __len__(self) -> int:
        return len(self._committed)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(self._committed)

    def get(self, entry_id: str) -> dict[str, Any] | None:
        raw = self._core.get(entry_id)
        if raw is None:
            return None
        return _to_debate_wire(raw)

    def all_entries(self) -> list[dict[str, Any]]:
        """全量**已提交**台账（权威快照，供 ``debate_result``；wire 形状）。"""
        return [
            _to_debate_wire(e)
            for e in self._core.all_entries()
            if e["id"] in self._committed
        ]

    def drain_delta(self) -> list[dict[str, Any]]:
        """自上次 drain 以来新**提交**的条目（供 ``debate_round`` 增量；wire 形状）。"""
        out: list[dict[str, Any]] = []
        for e in self._core.all_entries():
            eid = e["id"]
            if eid in self._committed and eid not in self._drained:
                out.append(_to_debate_wire(e))
                self._drained.add(eid)
        return out

    def register(
        self,
        *,
        url: str = "",
        title: str = "",
        snippet: str = "",
        site: str = "",
        date: str = "",
        side_key: str,
        tier: str | None = None,
        dossier_path: str = "",
        origin_id: str = "",
        dossier_label: str = "",
    ) -> str:
        """登记一条来源并立即提交上 wire；同键去重返回既有 id。返回 ``#eN``。

        ``side_key`` → 共享核 ``registrant`` 别名。约定文档锚字段 additive（空串=无）。
        """
        eid = self._core.register_sync(
            url=url,
            title=title,
            snippet=snippet,
            site=site,
            date=date,
            registrant=side_key,
            tier=tier,
            dossier_path=dossier_path,
            origin_id=origin_id,
            dossier_label=dossier_label,
        )
        # reject_blocked=False 下不应为 None；防御性回退避免破坏调用方 str 契约。
        if eid is None:
            raise RuntimeError("evidence ledger rejected registration unexpectedly")
        self._committed.add(eid)
        return eid

    def register_citation(self, citation: dict[str, Any], *, side_key: str) -> str:
        """从工具 citation dict 登记（兼容 url/title/snippet/site；可选 date）。"""
        return self.register(
            url=str(citation.get("url") or ""),
            title=str(citation.get("title") or ""),
            snippet=str(citation.get("snippet") or ""),
            site=str(citation.get("site") or ""),
            date=str(citation.get("date") or ""),
            side_key=side_key,
            tier=citation.get("tier") if isinstance(citation.get("tier"), str) else None,
        )

    def register_citations(
        self, citations: list[dict[str, Any]], *, side_key: str
    ) -> list[str]:
        """批量登记；返回各条目 id（含去重命中）。"""
        return [self.register_citation(c, side_key=side_key) for c in citations]

    def commit_research(self, *, note_cited_ids: Collection[str]) -> frozenset[str]:
        """检索结束后提交消费子集：``web_fetch``（deep_read）+ 笔记引用的 search 命中。

        返回本轮新提交的 id 集。未消费的 search 噪声留在核内供去重，不上 wire。
        """
        cited = set(note_cited_ids)
        newly: set[str] = set()
        for e in self._core.all_entries():
            eid = e["id"]
            if eid in self._committed:
                continue
            if e.get("deep_read") or eid in cited:
                self._committed.add(eid)
                newly.add(eid)
        return frozenset(newly)


# 裁判 / 简报可见的 tier 人话（与 O3 阶梯对齐；unknown ≠ 弱源实锤）。
_TIER_PROMPT_LABEL: dict[str, str] = {
    "official": "官方原文",
    "media": "权威媒体",
    "weak": "弱源/自媒体",
    "unknown": "来源待评（非弱源实锤）",
    "blocked": "硬拦",
}


def tier_prompt_label(tier: str | None) -> str:
    """台账 tier → 裁判/简报 prompt 人话。"""
    key = (tier or "unknown").strip() or "unknown"
    return _TIER_PROMPT_LABEL.get(key, _TIER_PROMPT_LABEL["unknown"])


def _format_ledger_entry_line(entry: dict[str, Any]) -> str:
    eid = entry.get("id") or "?"
    tier = entry.get("tier") or "unknown"
    title = (entry.get("title") or "").strip() or (entry.get("site") or "").strip() or "（无标题）"
    site = (entry.get("site") or "").strip()
    url = (entry.get("url") or "").strip()
    tail = url or site or "底料预登记"
    return f"- {eid} · tier={tier}（{tier_prompt_label(str(tier))}）· {title} · {tail}"


def format_evidence_ledger_for_judge(
    ledger: EvidenceLedger | None,
    turns: Sequence[Any],
    *,
    cross_exam: Sequence[Any] = (),
) -> str:
    """本轮发言 / 质询作答里实际引用的 ``#eN`` → 带 tier 的结构化块（M2 记分锚定）。

    无台账或无引用 → 空串（裁判退化为旧软约束）。unknown 不单独惩罚——教法写进块尾。
    """
    if ledger is None:
        return ""
    ids: set[str] = set()
    for t in turns or ():
        ids |= extract_ledger_ids(getattr(t, "content", "") or "")
    for cx in cross_exam or ():
        for ex in getattr(cx, "exchanges", ()) or ():
            ids |= extract_ledger_ids(getattr(ex, "answer", "") or "")
    if not ids:
        return ""
    entries: list[dict[str, Any]] = []
    for eid in sorted(ids, key=lambda x: int(x[2:]) if x[2:].isdigit() else 0):
        raw = ledger.get(eid)
        if raw is not None:
            entries.append(raw)
    if not entries:
        return ""
    body = "\n".join(_format_ledger_entry_line(e) for e in entries)
    return (
        "【本轮引用证据台账·evidence 记分须按下列 tier 锚定，勿臆造等级】\n"
        f"{body}\n"
        "【教法】official=强可给满；media=决定性事实无交叉印证则中等封顶；"
        "weak=弱、evidence 封顶打低且须在 note/penalties 点名；"
        "unknown【不是】弱源实锤——不单独惩罚，但「决定性事实仅靠单一 unknown/weak」仍封顶打低。\n\n"
    )


def format_evidence_ledger_for_brief(
    ledger: EvidenceLedger | None,
    rounds: Sequence[Any] = (),
) -> str:
    """收场简报：全场已引用 ``#eN`` 的 tier 标注块（M2 抽查；无引用则空）。"""
    if ledger is None:
        return ""
    ids: set[str] = set()
    for rr in rounds or ():
        for turn in getattr(rr, "turns", ()) or ():
            ids |= extract_ledger_ids(getattr(turn, "content", "") or "")
        for cx in getattr(rr, "cross_exam", ()) or ():
            for ex in getattr(cx, "exchanges", ()) or ():
                ids |= extract_ledger_ids(getattr(ex, "answer", "") or "")
    if not ids:
        # 无正文引用时仍给已提交全量（底料预登记等），便于抽查弱源。
        entries = list(ledger.all_entries())
    else:
        entries = []
        for eid in sorted(ids, key=lambda x: int(x[2:]) if x[2:].isdigit() else 0):
            raw = ledger.get(eid)
            if raw is not None:
                entries.append(raw)
    if not entries:
        return ""
    body = "\n".join(_format_ledger_entry_line(e) for e in entries)
    return (
        "【本场证据台账·结论引用须保留 tier / 待核实状态，不得抹平】\n"
        f"{body}\n"
        "decisive / leaning 若依赖 tier=weak 或【待核实】事实：须在 confidence 降级并标"
        "【需一手核实】，或移进交接清单；unknown 不单独当弱源实锤，但单一 unknown 撑决定性"
        "事实同样不得写成既定。\n\n"
    )


def format_evidence_ledger_hint(
    ledger: EvidenceLedger,
    *,
    ids: Collection[str] | None = None,
) -> str:
    """成稿可见的台账摘要：默认只列 ``ids`` 子集（本方笔记引用），避免全场大清单盲配。"""
    allow = None if ids is None else set(ids)
    entries = [
        e
        for e in ledger.all_entries()
        if allow is None or e["id"] in allow
    ]
    if not entries and allow:
        # 笔记引用了尚未 commit 的 id（或仅历轮并集）：从核补全摘要行。
        for eid in sorted(allow, key=lambda x: int(x[2:]) if x[2:].isdigit() else 0):
            raw = ledger.get(eid)
            if raw is not None:
                entries.append(raw)
    if not entries:
        return ""
    lines: list[str] = []
    for e in entries:
        title = (e.get("title") or "").strip() or (e.get("site") or "").strip() or "（无标题）"
        url = (e.get("url") or "").strip()
        site = (e.get("site") or "").strip()
        tail = url or site or "底料预登记"
        lines.append(f"- {e['id']} · {title} · {tail}")
    return (
        "【本方已绑定来源·成稿只许引用下列 id（须已出现在本方证据笔记）】\n"
        + "\n".join(lines)
        + "\n已核实主张写成【已核实·#eN】；不在上表 / 本方笔记的来源不得标已核实，改【待核实·推断】。"
    )


def _eid_for_origin(ledger: EvidenceLedger, origin_id: str) -> str | None:
    """若台账已有 ``origin_id`` 匹配的条目，返回其 ``#eN``。"""
    oid = (origin_id or "").strip()
    if not oid:
        return None
    for entry in ledger.all_entries():
        if str(entry.get("origin_id") or "").strip() == oid:
            eid = str(entry.get("id") or "").strip()
            if eid:
                return eid
    return None


def _extract_ceo_r_ref_ids(text: str) -> list[str]:
    """底料正文中的 ``#rN``（首次出现序、去重；``#r10`` 不被 ``#r1`` 前缀吞）。"""
    out: list[str] = []
    seen: set[str] = set()
    for m in _CEO_R_REF_RE.finditer(text or ""):
        rid = f"#r{m.group(1)}"
        if rid in seen:
            continue
        seen.add(rid)
        out.append(rid)
    return out


def preregister_turn_research_entries(
    ledger: EvidenceLedger,
    turn_entries: Sequence[Mapping[str, Any]] | None,
) -> list[str]:
    """CEO 回合调研台账 ``#rN`` → 场级 ``#eN``（约定文档桥无条件化 · §二之二）。

    无条目 → ``[]``。已映射的 ``origin_id`` 去重。不改写任何正文（正文改写仍走
    :func:`preregister_background`）。
    """
    if not turn_entries:
        return []
    out: list[str] = []
    for raw in turn_entries:
        if not isinstance(raw, Mapping):
            continue
        origin = str(raw.get("id") or "").strip()
        if not origin.startswith("#r"):
            continue
        existing = _eid_for_origin(ledger, origin)
        if existing is not None:
            out.append(existing)
            continue
        eid = ledger.register(
            url=str(raw.get("url") or ""),
            title=str(raw.get("title") or "") or f"回合来源 {origin}",
            snippet=str(raw.get("snippet") or ""),
            site=str(raw.get("site") or ""),
            date=str(raw.get("date") or ""),
            side_key=MODERATOR_SIDE_KEY,
            tier=str(raw.get("tier") or "unknown") or "unknown",
            origin_id=origin,
        )
        out.append(eid)
    return out


def preregister_background(ledger: EvidenceLedger, background: str) -> str:
    """底料预登记：抽取【已核实·出处】与 CEO 回合 ``#rN`` → 台账条目，
    返回改写后的底料正文（标签 → ``【已核实·#eN】``；裸 ``#rN`` → ``#eN``）。

    无标签 / 无 ``#rN`` / 空底料 → 原样返回。已含 ``#eN`` 的标签不重复登记（沿用既有 id）。
    注入即注册：辩手 prompt 里出现的引用 id 必须已在场级台账，否则会触发
    ``citations.invalid_ledger_ref``（平台闸只认可引用集）。
    """
    text = background or ""
    if not text.strip():
        return text
    tags = extract_verified_tags(text)
    # 按正文首次出现顺序登记（稳定 #eN）；替换时长标签优先防前缀互吞
    ordered: list[str] = []
    for m in re.finditer(re.escape(_VERIFIED_PREFIX), text):
        start = m.start()
        # 在 extract 结果里找以该起点开头的完整标签
        for tag in tags:
            if text.startswith(tag, start) and tag not in ordered:
                ordered.append(tag)
                break
    replacements: list[tuple[str, str]] = []
    for tag in ordered:
        if not tag.endswith("】"):
            continue
        note = tag[len(_VERIFIED_PREFIX) : -1].strip()
        id_match = _ID_IN_NOTE_RE.search(note)
        if id_match:
            # 已是 id 形态（重入 / 测试夹具）——确保台账有对应条目
            eid = f"#e{id_match.group(1)}"
            if ledger.get(eid) is None:
                ledger.register(
                    url="",
                    title=note,
                    side_key=MODERATOR_SIDE_KEY,
                    tier="unknown",
                )
            continue
        if not note:
            continue
        eid = ledger.register(
            url="",
            title=note,
            side_key=MODERATOR_SIDE_KEY,
            tier="unknown",
        )
        replacements.append((tag, f"{_VERIFIED_PREFIX}{eid}】"))
    result = text
    for old, new in sorted(replacements, key=lambda p: len(p[0]), reverse=True):
        result = result.replace(old, new)

    # CEO 回合台账引用（#rN）随底料注入时，映射登记为场级 #eN 并改写正文。
    # 与约定文档 preregister_research_dossier 同构：注入面不得留下未登记的 #rN。
    # 抽取不用 citations.extract_ledger_ref_ids（其 \b 在中文邻接会漏号）。
    r_to_e: dict[str, str] = {}
    for rid in _extract_ceo_r_ref_ids(result):
        existing = _eid_for_origin(ledger, rid)
        if existing is not None:
            r_to_e[rid] = existing
        else:
            r_to_e[rid] = ledger.register(
                url="",
                title=f"底料来源 {rid}",
                side_key=MODERATOR_SIDE_KEY,
                tier="unknown",
                origin_id=rid,
            )

    def _rewrite_r(match: re.Match[str]) -> str:
        rid = f"#r{match.group(1)}"
        return r_to_e.get(rid, match.group(0))

    if r_to_e:
        result = _CEO_R_REF_RE.sub(_rewrite_r, result)
    return result
