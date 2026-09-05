"""平台层证据台账共享核——登记 / 去重 / tier / 元数据 / 原子 id。

与辩论场级封装（:mod:`agentcore.runtime.debate.evidence_ledger`）及后续回合级
调研台账共用本核。条目模型见提案《引用即出处》§二。

规则：
- append-only；id = ``{prefix}{n}``（登记序，默认 ``#e``）
- 同 URL（:func:`normalize_citation_url`）去重 → 返回既有 id
- 空 URL（底料等）按归一化 title 去重
- ``tier`` 单源 :func:`citation_tier_for_url`；``blocked`` 默拒登记
- ``citable``：登记仍宽——已登记档（含 ``weak``）均为 ``True``；``blocked`` 不进台账
- 成稿闸（``#r``）：``draft_citable_ids`` = ``deep_read ∪ selected``（对齐辩论
  ``commit_research`` 精神；search-only 不得进成稿闸）
- asyncio 单进程内对分配路径加锁，支撑并行登记不撞号
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from agentcore.runtime.citations import citation_tier_for_url, normalize_citation_url

# 书目形态 / 公告类启发式（禁域名黑名单；仅 title/snippet/显式 doc_kind）。
_ANNOUNCEMENT_KIND_RE = re.compile(
    r"开题|答辩|公告|公示|征稿|通知|招标|中标|听证会"
)
_DOC_KIND_ANNOUNCEMENT = "announcement"


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", "", (title or "").strip().casefold())


def citable_for_tier(tier: str) -> bool:
    """登记宽：已登记档均可挂台账 id（含 ``weak``）；``blocked`` 不进台账。

    成稿 ``#rN`` 闸见 :meth:`EvidenceLedgerCore.draft_citable_ids`（更窄）。
    """
    return tier != "blocked"


def infer_doc_kind(
    *,
    title: str = "",
    snippet: str = "",
    doc_kind: str = "",
) -> str:
    """可选 ``doc_kind``；未给时用 title/snippet 启发式（开题/答辩/公告…）。"""
    explicit = (doc_kind or "").strip()
    if explicit:
        return explicit
    blob = f"{title or ''} {snippet or ''}"
    if _ANNOUNCEMENT_KIND_RE.search(blob):
        return _DOC_KIND_ANNOUNCEMENT
    return ""


def is_announcement_doc_kind(doc_kind: str, *, title: str = "", snippet: str = "") -> bool:
    """书目形态闸：元数据呈开题/答辩/公告类 → True。"""
    kind = (doc_kind or "").strip().casefold()
    if kind in {_DOC_KIND_ANNOUNCEMENT, "notice", "defense", "proposal"}:
        return True
    if kind:
        return False
    return bool(_ANNOUNCEMENT_KIND_RE.search(f"{title or ''} {snippet or ''}"))


@dataclass
class EvidenceLedgerCore:
    """回合 / 场级共享台账核：线程外 asyncio 单进程加锁分配 id。"""

    id_prefix: str = "#e"
    reject_blocked: bool = True
    _entries: list[dict[str, Any]] = field(default_factory=list)
    _by_url: dict[str, str] = field(default_factory=dict)
    _by_title: dict[str, str] = field(default_factory=dict)
    _cursor: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(e["id"] for e in self._entries)

    def get(self, entry_id: str) -> dict[str, Any] | None:
        for e in self._entries:
            if e["id"] == entry_id:
                return dict(e)
        return None

    def all_entries(self) -> list[dict[str, Any]]:
        """全量台账快照（含元数据字段）。"""
        return [dict(e) for e in self._entries]

    def citable_ids(self) -> frozenset[str]:
        """登记宽：``citable=true`` 的全量 id（含 search-only）。

        对话成稿 / 来源卡用本集。落盘成文闸用 :meth:`draft_citable_ids`。
        """
        return frozenset(e["id"] for e in self._entries if e.get("citable"))

    def draft_citable_ids(self) -> frozenset[str]:
        """落盘成文 ``#rN`` 闸：``deep_read ∪ selected``（search-only 不得进）。"""
        return frozenset(
            e["id"]
            for e in self._entries
            if e.get("citable") and (e.get("deep_read") or e.get("selected"))
        )

    def mark_selected_from_content(self, content: str) -> frozenset[str]:
        """settle：正文实际引用且已 ``deep_read`` 的 id 持久标 ``selected``。"""
        from agentcore.runtime.citations import extract_ledger_ref_ids

        cited = set(extract_ledger_ref_ids(content or ""))
        newly: set[str] = set()
        for e in self._entries:
            eid = e["id"]
            if eid not in cited or not e.get("deep_read"):
                continue
            if not e.get("selected"):
                newly.add(eid)
            e["selected"] = True
        return frozenset(newly)

    def promote_refs_cited_in_landed_note(self, content: str) -> frozenset[str]:
        """调研方向笔记落盘：正文已引用且可登记的 ``#rN`` 升为 ``selected``。

        供 CEO 汇总成稿闸继承队员笔记中的引用（search-only 亦可，因队员已写入交付物）。
        伪造 / 越界 id 仍不进台账，本方法只提升已登记条目。
        """
        from agentcore.runtime.citations import extract_ledger_ref_ids

        cited = set(extract_ledger_ref_ids(content or ""))
        newly: set[str] = set()
        for e in self._entries:
            eid = e["id"]
            if eid not in cited or not e.get("citable"):
                continue
            if not e.get("selected"):
                newly.add(eid)
            e["selected"] = True
        return frozenset(newly)

    def load_entries(self, entries: list[dict[str, Any]]) -> None:
        """从 pause / 历史快照再水化台账（保留既有 id，后续登记续号）。

        ``_cursor`` 置到末尾，避免把快照条目当成新 delta 重放。
        """
        self._entries = []
        self._by_url = {}
        self._by_title = {}
        self._cursor = 0
        for raw in entries or []:
            if not isinstance(raw, dict):
                continue
            entry_id = str(raw.get("id") or "").strip()
            if not entry_id:
                continue
            norm_url = normalize_citation_url(str(raw.get("url") or ""))
            title = str(raw.get("title") or "")
            snippet = str(raw.get("snippet") or "")
            tier = str(raw.get("tier") or "unknown")
            doc_kind = infer_doc_kind(
                title=title,
                snippet=snippet,
                doc_kind=str(raw.get("doc_kind") or ""),
            )
            entry = {
                "id": entry_id,
                "url": norm_url or str(raw.get("url") or ""),
                "title": title,
                "snippet": snippet,
                "site": str(raw.get("site") or ""),
                "date": str(raw.get("date") or ""),
                "tier": tier,
                "query": str(raw.get("query") or ""),
                "deep_read": bool(raw.get("deep_read")),
                "selected": bool(raw.get("selected")),
                "doc_kind": doc_kind,
                "registrant": str(raw.get("registrant") or ""),
                "citable": bool(raw["citable"])
                if "citable" in raw
                else citable_for_tier(tier),
                "dossier_path": str(raw.get("dossier_path") or ""),
                "origin_id": str(raw.get("origin_id") or ""),
                "dossier_label": str(raw.get("dossier_label") or ""),
            }
            self._entries.append(entry)
            if norm_url:
                self._by_url[norm_url] = entry_id
            else:
                title_key = _norm_title(title)
                if title_key:
                    self._by_title[title_key] = entry_id
        self._cursor = len(self._entries)

    def merge_history_ledgers(self, history: list[dict[str, Any]] | None) -> int:
        """跨回合 hydrate：合并历史 assistant ``evidence_ledger``（同 id 后写覆盖）。

        返回合并条数。LLM history 仍可只带 role/content；引擎核经此补齐。
        """
        by_id: dict[str, dict[str, Any]] = {}
        for msg in history or ():
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            raw_list = msg.get("evidence_ledger")
            if not isinstance(raw_list, list):
                continue
            for raw in raw_list:
                if not isinstance(raw, dict):
                    continue
                eid = str(raw.get("id") or "").strip()
                if not eid:
                    continue
                prev = by_id.get(eid)
                if prev is None:
                    by_id[eid] = dict(raw)
                    continue
                # 后写覆盖字段，但对 deep_read / selected 取并集，避免丢成稿资格。
                merged = dict(prev)
                merged.update(raw)
                merged["deep_read"] = bool(prev.get("deep_read")) or bool(
                    raw.get("deep_read")
                )
                merged["selected"] = bool(prev.get("selected")) or bool(
                    raw.get("selected")
                )
                by_id[eid] = merged
        if not by_id:
            return 0

        def _id_sort_key(entry: dict[str, Any]) -> tuple[int, str]:
            eid = str(entry.get("id") or "")
            num = "".join(ch for ch in eid if ch.isdigit())
            return (int(num) if num else 10**9, eid)

        ordered = sorted(by_id.values(), key=_id_sort_key)
        self.load_entries(ordered)
        return len(ordered)

    def drain_delta(self) -> list[dict[str, Any]]:
        """自上次 drain 以来的新登记条目。"""
        delta = [dict(e) for e in self._entries[self._cursor :]]
        self._cursor = len(self._entries)
        return delta

    async def register(
        self,
        *,
        url: str = "",
        title: str = "",
        snippet: str = "",
        site: str = "",
        date: str = "",
        registrant: str,
        tier: str | None = None,
        query: str = "",
        deep_read: bool = False,
        selected: bool = False,
        doc_kind: str = "",
        dossier_path: str = "",
        origin_id: str = "",
        dossier_label: str = "",
    ) -> str | None:
        """异步登记（持锁）；``blocked`` 且 ``reject_blocked`` 时返回 ``None``。"""
        async with self._lock:
            return self._register_unlocked(
                url=url,
                title=title,
                snippet=snippet,
                site=site,
                date=date,
                registrant=registrant,
                tier=tier,
                query=query,
                deep_read=deep_read,
                selected=selected,
                doc_kind=doc_kind,
                dossier_path=dossier_path,
                origin_id=origin_id,
                dossier_label=dossier_label,
            )

    def register_sync(
        self,
        *,
        url: str = "",
        title: str = "",
        snippet: str = "",
        site: str = "",
        date: str = "",
        registrant: str,
        tier: str | None = None,
        query: str = "",
        deep_read: bool = False,
        selected: bool = False,
        doc_kind: str = "",
        dossier_path: str = "",
        origin_id: str = "",
        dossier_label: str = "",
    ) -> str | None:
        """同步登记。

        供单协程调用方（辩论编排）。并行 worker 须用 :meth:`register`，否则
        跨 await 交错时可能撞号。
        """
        return self._register_unlocked(
            url=url,
            title=title,
            snippet=snippet,
            site=site,
            date=date,
            registrant=registrant,
            tier=tier,
            query=query,
            deep_read=deep_read,
            selected=selected,
            doc_kind=doc_kind,
            dossier_path=dossier_path,
            origin_id=origin_id,
            dossier_label=dossier_label,
        )

    async def register_citation(
        self, citation: dict[str, Any], *, registrant: str
    ) -> str | None:
        """从工具 citation dict 异步登记。"""
        return await self.register(
            url=str(citation.get("url") or ""),
            title=str(citation.get("title") or ""),
            snippet=str(citation.get("snippet") or ""),
            site=str(citation.get("site") or ""),
            date=str(citation.get("date") or ""),
            registrant=registrant,
            tier=citation.get("tier") if isinstance(citation.get("tier"), str) else None,
            query=str(citation.get("query") or ""),
            deep_read=bool(citation.get("deep_read")),
            selected=bool(citation.get("selected")),
            doc_kind=str(citation.get("doc_kind") or ""),
        )

    def register_citation_sync(
        self, citation: dict[str, Any], *, registrant: str
    ) -> str | None:
        """从工具 citation dict 同步登记。"""
        return self.register_sync(
            url=str(citation.get("url") or ""),
            title=str(citation.get("title") or ""),
            snippet=str(citation.get("snippet") or ""),
            site=str(citation.get("site") or ""),
            date=str(citation.get("date") or ""),
            registrant=registrant,
            tier=citation.get("tier") if isinstance(citation.get("tier"), str) else None,
            query=str(citation.get("query") or ""),
            deep_read=bool(citation.get("deep_read")),
            selected=bool(citation.get("selected")),
            doc_kind=str(citation.get("doc_kind") or ""),
        )

    async def register_citations(
        self, citations: list[dict[str, Any]], *, registrant: str
    ) -> list[str]:
        """批量异步登记；跳过拒登记项；返回成功 id（含去重命中）。"""
        out: list[str] = []
        async with self._lock:
            for c in citations:
                eid = self._register_unlocked(
                    url=str(c.get("url") or ""),
                    title=str(c.get("title") or ""),
                    snippet=str(c.get("snippet") or ""),
                    site=str(c.get("site") or ""),
                    date=str(c.get("date") or ""),
                    registrant=registrant,
                    tier=c.get("tier") if isinstance(c.get("tier"), str) else None,
                    query=str(c.get("query") or ""),
                    deep_read=bool(c.get("deep_read")),
                    selected=bool(c.get("selected")),
                    doc_kind=str(c.get("doc_kind") or ""),
                )
                if eid is not None:
                    out.append(eid)
        return out

    def register_citations_sync(
        self, citations: list[dict[str, Any]], *, registrant: str
    ) -> list[str]:
        """批量同步登记；跳过拒登记项。"""
        out: list[str] = []
        for c in citations:
            eid = self.register_citation_sync(c, registrant=registrant)
            if eid is not None:
                out.append(eid)
        return out

    def _register_unlocked(
        self,
        *,
        url: str = "",
        title: str = "",
        snippet: str = "",
        site: str = "",
        date: str = "",
        registrant: str,
        tier: str | None = None,
        query: str = "",
        deep_read: bool = False,
        selected: bool = False,
        doc_kind: str = "",
        dossier_path: str = "",
        origin_id: str = "",
        dossier_label: str = "",
    ) -> str | None:
        norm_url = normalize_citation_url(url)
        url_tier = citation_tier_for_url(norm_url)
        # 空串 / None 均回退 URL 分级（对齐原辩论 register 的 ``tier or …``）。
        resolved_tier = tier or url_tier
        if self.reject_blocked and (
            url_tier == "blocked" or resolved_tier == "blocked"
        ):
            return None

        resolved_kind = infer_doc_kind(
            title=title, snippet=snippet, doc_kind=doc_kind
        )

        if norm_url:
            existing = self._by_url.get(norm_url)
            if existing is not None:
                self._upgrade_existing(
                    existing,
                    query=query,
                    deep_read=deep_read,
                    selected=selected,
                    doc_kind=resolved_kind,
                    dossier_path=dossier_path,
                    origin_id=origin_id,
                    dossier_label=dossier_label,
                )
                return existing
        else:
            title_key = _norm_title(title)
            if title_key:
                existing = self._by_title.get(title_key)
                if existing is not None:
                    self._upgrade_existing(
                        existing,
                        query=query,
                        deep_read=deep_read,
                        selected=selected,
                        doc_kind=resolved_kind,
                        dossier_path=dossier_path,
                        origin_id=origin_id,
                        dossier_label=dossier_label,
                    )
                    return existing

        entry_id = f"{self.id_prefix}{len(self._entries) + 1}"
        if not site and norm_url:
            site = urlparse(norm_url).netloc.removeprefix("www.")
        entry = {
            "id": entry_id,
            "url": norm_url or (url or ""),
            "title": title or "",
            "snippet": snippet or "",
            "site": site or "",
            "date": date or "",
            "tier": resolved_tier,
            "query": query or "",
            "deep_read": bool(deep_read),
            "selected": bool(selected),
            "doc_kind": resolved_kind,
            "registrant": registrant,
            "citable": citable_for_tier(resolved_tier),
            "dossier_path": dossier_path or "",
            "origin_id": origin_id or "",
            "dossier_label": dossier_label or "",
        }
        self._entries.append(entry)
        if norm_url:
            self._by_url[norm_url] = entry_id
        else:
            title_key = _norm_title(title)
            if title_key:
                self._by_title[title_key] = entry_id
        return entry_id

    def _upgrade_existing(
        self,
        entry_id: str,
        *,
        query: str = "",
        deep_read: bool = False,
        selected: bool = False,
        doc_kind: str = "",
        dossier_path: str = "",
        origin_id: str = "",
        dossier_label: str = "",
    ) -> None:
        """同 URL / 底料去重命中时：``web_fetch`` 可升级 ``deep_read``；空字段可补填。"""
        if not (
            deep_read
            or selected
            or query
            or doc_kind
            or dossier_path
            or origin_id
            or dossier_label
        ):
            return
        for e in self._entries:
            if e["id"] != entry_id:
                continue
            if deep_read and not e.get("deep_read"):
                e["deep_read"] = True
            if selected and not e.get("selected"):
                e["selected"] = True
            if query and not (e.get("query") or "").strip():
                e["query"] = query
            if doc_kind and not (e.get("doc_kind") or "").strip():
                e["doc_kind"] = doc_kind
            if dossier_path and not (e.get("dossier_path") or "").strip():
                e["dossier_path"] = dossier_path
            if origin_id and not (e.get("origin_id") or "").strip():
                e["origin_id"] = origin_id
            if dossier_label and not (e.get("dossier_label") or "").strip():
                e["dossier_label"] = dossier_label
            return


def format_registered_sources_prompt(ledger: EvidenceLedgerCore | None) -> str:
    """hydrate 后注入「已登记来源」结构化摘要（id/url/query/registrant/deep_read）。"""
    if ledger is None or len(ledger) == 0:
        return ""
    lines: list[str] = []
    for e in ledger.all_entries():
        eid = e.get("id") or "?"
        url = (e.get("url") or "").strip() or "（无 URL）"
        query = (e.get("query") or "").strip() or "—"
        registrant = (e.get("registrant") or "").strip() or "—"
        deep = "是" if e.get("deep_read") else "否"
        selected = "是" if e.get("selected") else "否"
        lines.append(
            f"- {eid} · url={url} · query={query} · registrant={registrant} · "
            f"deep_read={deep} · selected={selected}"
        )
    body = "\n".join(lines)
    return (
        "<已登记来源>\n"
        "【已登记来源】本会话台账（跨回合 hydrate 后可见）。"
        "回答某 #rN 出处必须对照下列字段，禁止占位/巧合叙事；"
        "对话成稿可挂下列已登记可引用 id（含仅检索未深读）。\n"
        f"{body}\n"
        "</已登记来源>"
    )
