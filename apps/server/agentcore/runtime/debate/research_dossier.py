"""工作区幕 1 调研约定文档（``AgentCore/文档/research/``）——辩论开工探测、台账锚与索引文案。

约定文档由多维调研落盘；辩论侧只读索引（文件列表 + 一行说明），
全文由辩手 ``file_read`` 自取。开赛时把约定文档内 ``#rN`` 锚预登记进场级 ``#eN`` 台账
（对齐底料 ``preregister_background``）。不碰 persist / 轮次原语。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agentcore.runtime.citations import extract_ledger_ref_ids
from agentcore.workspace.protocol import NotADirectory, PathNotFound, WorkspaceError
from agentcore.workspace.stage_dirs import RESEARCH_DIR

if TYPE_CHECKING:
    from agentcore.runtime.debate.evidence_ledger import EvidenceLedger
    from agentcore.workspace.protocol import WorkspaceBackend

# 汇总文件名（议程提示用；不强制文件必须存在）。
SYNTHESIZER_FILE = f"{RESEARCH_DIR}/汇总与命题卡.md"

# 约定文档预登记的登记方键（非辩手 side_key；与 moderator 底料并列）。
DOSSIER_SIDE_KEY = "dossier"

_INDEX_CAP = 40
_ANCHOR_SECTION = "## 来源台账锚"
# 落盘脚注行：`- #r1 · https://… · 标题`（机制兜底写入；亦接受模型自写同形）。
_FOOTER_LINE_RE = re.compile(
    r"^\s*[-*]\s*(#r\d+)\s*(?:·\s*(\S+))?\s*(?:·\s*(.+))?\s*$",
    re.MULTILINE,
)
_URL_LIKE = re.compile(r"^https?://", re.IGNORECASE)


@dataclass(frozen=True)
class ResearchLedgerAnchor:
    """约定文档正文中的一条可解析调研台账锚（幕 1 ``#rN``）。"""

    origin_id: str  # #rN
    url: str = ""
    title: str = ""


def dossier_label_from_path(path: str) -> str:
    """从约定文档路径推导人话透镜/角色标签（徽章溯源用）。"""
    name = (path or "").replace("\\", "/").rsplit("/", 1)[-1]
    stem = name.removesuffix(".md").removesuffix(".MD")
    if stem.endswith("透镜报告"):
        return stem[: -len("透镜报告")] or stem
    if "汇总" in stem:
        return "汇总"
    return stem or path


def extract_research_ledger_anchors(content: str) -> list[ResearchLedgerAnchor]:
    """从约定文档正文抽取 ``#rN`` 锚（正文行尾 + 脚注节）；保首次出现序。"""
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
    避免文末锚制造假安心；交由合同闸 ``citation_quality_reworks`` 返工。
    """
    text = content or ""
    if extract_research_ledger_anchors(text):
        return text
    # 有未核验书目形态时不补脚注（与合同闸对齐，勿用文末 #rN 蒙混段内 [D]）。
    from agentcore.runtime.verify import citation_quality_reworks

    if citation_quality_reworks(text, ledger_entries=list(ledger_entries) or []):
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


async def list_research_artifact_paths(backend: WorkspaceBackend) -> list[str]:
    """列出 ``AgentCore/文档/research/`` 下文件路径；目录不存在或空 → ``[]``。"""
    try:
        entries = await backend.list(RESEARCH_DIR, "*")
    except (PathNotFound, NotADirectory):
        return []
    except WorkspaceError:
        return []
    paths = sorted(e.path.replace("\\", "/") for e in entries if not e.is_dir and e.path)
    return paths[:_INDEX_CAP]


async def workspace_has_research_artifacts(backend: WorkspaceBackend) -> bool:
    """工作区是否已有幕 1 调研产物文件（供调研链证据并集判据）。"""
    return bool(await list_research_artifact_paths(backend))


async def workspace_has_synthesizer(backend: WorkspaceBackend) -> bool:
    """幕 1 汇总文件是否存在（辩论双产物互链头用；无则零行为）。"""
    paths = await list_research_artifact_paths(backend)
    return SYNTHESIZER_FILE in paths


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
    """约定文档文件索引块（非全文）。空路径 → 空串（调用方跳过注入）。

    ``ledger_lines`` 可选：预登记后的 ``#eN`` 映射行（每行已格式化）。
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
        f"【工作区约定文档索引·{RESEARCH_DIR}/】\n"
        "幕1 多视角调研产物已落盘（下列为文件列表+字数/摘要，非全文；"
        "按本轮议题选读相关文件，用 file_read 按路径自取——勿无差别全量通读）。\n"
        f"{lines}"
    )
    if ledger_lines:
        mapped = "\n".join(ledger_lines)
        block += (
            "\n\n【约定文档预登记台账·引用须用下列 #eN】\n"
            "引用约定文档事实写成【已核实·#eN】（id 见下；徽章可溯源到约定文档文件与幕1 #rN）。\n"
            f"{mapped}"
        )
    return block


async def preregister_research_dossier(
    ledger: EvidenceLedger,
    backend: WorkspaceBackend,
) -> str:
    """开赛约定文档预登记：读约定文档文件 → 抽 ``#rN`` 锚 → 登记进场级台账 → 返回索引。

    无约定文档 → 空串（零行为）。文件无锚时仍登记「整文件」一条（一层兜底，可溯源到路径）。
    """
    paths = await list_research_artifact_paths(backend)
    if not paths:
        return ""

    # path → 本文件登记出的 #eN 列表（供索引映射）
    path_eids: dict[str, list[str]] = {p: [] for p in paths}
    file_hints: dict[str, str] = {}

    for path in paths:
        try:
            content = await backend.read(path)
        except WorkspaceError:
            content = ""
        file_hints[path] = dossier_file_hint(path, content)
        anchors = extract_research_ledger_anchors(content)
        label = dossier_label_from_path(path)
        if anchors:
            for a in anchors:
                title = (a.title or "").strip() or f"{label} · {a.origin_id}"
                eid = ledger.register(
                    url=a.url,
                    title=title,
                    snippet=f"约定文档 {path}"
                    + (f" · 幕1 {a.origin_id}" if a.origin_id else ""),
                    site=label,
                    side_key=DOSSIER_SIDE_KEY,
                    tier="unknown",
                    dossier_path=path,
                    origin_id=a.origin_id,
                    dossier_label=label,
                )
                path_eids[path].append(eid)
        else:
            eid = ledger.register(
                url="",
                title=f"约定文档 · {label}",
                snippet=f"约定文档文件 {path}（正文无 #rN 锚）",
                site=label,
                side_key=DOSSIER_SIDE_KEY,
                tier="unknown",
                dossier_path=path,
                origin_id="",
                dossier_label=label,
            )
            path_eids[path].append(eid)

    ledger_lines: list[str] = []
    for path in paths:
        eids = path_eids.get(path) or []
        if not eids:
            continue
        parts: list[str] = []
        for eid in eids:
            entry = ledger.get(eid) or {}
            origin = str(entry.get("origin_id") or "").strip()
            if origin:
                parts.append(f"{eid}（幕1 {origin}）")
            else:
                parts.append(eid)
        ledger_lines.append(f"- {path} → {', '.join(parts)}")

    return format_research_dossier_index(
        paths, ledger_lines=ledger_lines, file_hints=file_hints
    )
