"""材料索引与调研正文里的 ``#rN`` 锚——辩论侧只读文案，不扫约定柜。

约定文档抽屉已卸。开赛材料走 CEO ``background`` / 附件 / 已声明 ``#rN``；
本模块只格式化调用方传入的路径列表，并从正文抽锚。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agentcore.runtime.citations import extract_ledger_ref_ids

# 预登记的登记方键（非辩手 side_key；与 moderator 底料并列）。
DOSSIER_SIDE_KEY = "dossier"

_ANCHOR_SECTION = "## 来源台账锚"
# 落盘脚注行：`- #r1 · https://… · 标题`（机制兜底写入；亦接受模型自写同形）。
_FOOTER_LINE_RE = re.compile(
    r"^\s*[-*]\s*(#r\d+)\s*(?:·\s*(\S+))?\s*(?:·\s*(.+))?\s*$",
    re.MULTILINE,
)
_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)
_BIBLIO_TYPE_MARKER_RE = re.compile(r"\[(?:D|J|M|C|N)\]")


@dataclass(frozen=True)
class ResearchLedgerAnchor:
    """正文中的一条可解析调研台账锚（``#rN``）。"""

    origin_id: str  # #rN
    url: str = ""
    title: str = ""


def dossier_label_from_path(path: str) -> str:
    """从路径推导人话透镜/角色标签（徽章溯源用）。"""
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.removesuffix(".md").removesuffix(".MD")
    if stem.endswith("透镜报告"):
        return stem[: -len("透镜报告")] or stem
    if "汇总" in stem:
        return "汇总"
    return stem or path


def extract_research_ledger_anchors(content: str) -> list[ResearchLedgerAnchor]:
    """从正文抽取 ``#rN`` 锚（正文行尾 + 脚注节）；保首次出现序。"""
    text = content or ""
    by_id: dict[str, ResearchLedgerAnchor] = {}
    order: list[str] = []

    def _put(origin_id: str, *, url: str = "", title: str = "") -> None:
        if origin_id not in by_id:
            order.append(origin_id)
            by_id[origin_id] = ResearchLedgerAnchor(
                origin_id=origin_id, url=url, title=title
            )
            return
        cur = by_id[origin_id]
        if (not cur.url and url) or (not cur.title and title):
            by_id[origin_id] = ResearchLedgerAnchor(
                origin_id=origin_id,
                url=cur.url or url,
                title=cur.title or title,
            )

    for eid in extract_ledger_ref_ids(text):
        _put(eid)

    for m in _FOOTER_LINE_RE.finditer(text):
        eid = m.group(1)
        mid = (m.group(2) or "").strip()
        tail = (m.group(3) or "").strip()
        url = mid if _URL_LIKE.match(mid) else ""
        title = tail or (mid if mid and not url else "")
        _put(eid, url=url, title=title)

    return [by_id[i] for i in order]


def ensure_research_file_anchors(
    content: str,
    ledger_entries: Sequence[dict[str, Any]],
) -> str:
    """落盘锚写入：正文已有 ``#rN`` 则原样返回；否则追加脚注节（一层兜底）。

    ``ledger_entries`` 为本 worker 已登记的调研台账条目（含 url/title）。
    无条目 / 空正文 → 原样返回。

    若正文已有未绑定 ``#rN`` 的 GB/T 书目形态（``[D]/[J]``…），**不**补脚注——
    避免文末锚制造假安心。
    """
    text = content or ""
    if extract_research_ledger_anchors(text):
        return text
    if _BIBLIO_TYPE_MARKER_RE.search(text) and not extract_ledger_ref_ids(text):
        return text
    usable = [
        e
        for e in ledger_entries
        if isinstance(e, dict) and str(e.get("id") or "").startswith("#r")
    ]
    if not usable:
        return text
    lines = [_ANCHOR_SECTION, ""]
    for e in usable:
        eid = str(e["id"])
        url = str(e.get("url") or "").strip()
        title = str(e.get("title") or "").strip() or str(e.get("site") or "").strip()
        parts = [eid]
        if url:
            parts.append(url)
        if title:
            parts.append(title)
        lines.append("- " + " · ".join(parts))
    footer = "\n".join(lines) + "\n"
    body = text.rstrip()
    if not body:
        return footer
    return body + "\n\n" + footer


def _format_char_size(n: int) -> str:
    if n >= 1000:
        return f"约{max(1, n // 1000)}k字"
    return f"{n}字"


def dossier_file_hint(path: str, content: str) -> str:
    """索引行附注：字数 + 首行摘要，帮辩手按议题选读（非全文）。"""
    text = content or ""
    size = _format_char_size(len(text))
    label = dossier_label_from_path(path)
    blurb = ""
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if not s or s.startswith("---"):
            continue
        blurb = s[:64]
        break
    if blurb:
        return f"{size} · {label}：{blurb}"
    return f"{size} · {label}"


def format_research_dossier_index(
    paths: Sequence[str],
    *,
    ledger_lines: Sequence[str] | None = None,
    file_hints: dict[str, str] | None = None,
) -> str:
    """材料文件索引块（非全文）。空路径 → 空串（调用方跳过注入）。

    ``ledger_lines`` 可选：预登记后的 ``#rN`` 行（每行已格式化）。
    ``file_hints`` 可选：path → 「约Nk字 · 标签：摘要」附注，助选读。
    """
    clean = [p.strip().replace("\\", "/") for p in paths if (p or "").strip()]
    if not clean:
        return ""
    hints = file_hints or {}
    bullet_lines: list[str] = []
    for p in clean:
        hint = (hints.get(p) or "").strip()
        bullet_lines.append(f"- {p}" + (f"（{hint}）" if hint else ""))
    lines = "\n".join(bullet_lines)
    block = (
        "【工作区材料索引】\n"
        "下列为文件列表+字数/摘要，非全文；"
        "按本轮议题选读相关文件，用 read 按路径自取——勿无差别全量通读。\n"
        f"{lines}"
    )
    if ledger_lines:
        mapped = "\n".join(ledger_lines)
        block += (
            "\n\n【材料预登记台账·引用须用下列 #rN】\n"
            "id 见下；徽章可溯源到文件。\n"
            f"{mapped}"
        )
    return block
