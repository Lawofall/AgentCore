"""落盘产物自报契约（交付物台账的事实口径）。

台账（``files_touched`` / ``file_acceptance``）**不再**按工具名白名单猜谁产了文件、
再从调用入参里抠 path——入参不等于产物（``md_to_docx`` 的入参是源 md、产物是推导出的
docx；批量工具一次产上千个），而且产文件的通道也不止工具调用。改为：**产文件的工具在
自己的执行结果上自报产物**（:class:`FileProduct`），引擎在 tool 消息上盖一条机器尾注，
台账只读这条尾注。新增产文件工具只需自报，无需在任何名单里登记。

形状沿用 ``code_execute`` 写回尾注那套（producer 编码函数 + consumer 解析函数，靠
round-trip 单测钉死格式）：生产方是本模块的 :func:`render_file_products_marker`
（经 :func:`with_file_products_marker` 由引擎单点盖章），消费方是
``runtime/runs/serialize.py`` 的 ``file_products_from_transcript``。

尾注只进 transcript，不进 SSE ``tool_use_end.output`` —— 与 ``tool_failed`` 尾注同处
（``runtime/engine/tool_exec_call.py``），所以工具的对外回执文案不受影响；也因此它加在
``ToolResult`` 的 HEAD+TAIL 截断之后，永远不会被截掉。

本模块只依赖标准库，任何层（tools / runtime / engine）都可直接 import。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "FILE_PRODUCTS_MARKER_PREFIX",
    "FILE_PRODUCTS_MARKER_SUFFIX",
    "LANDING_TOOLS",
    "LANDING_TOOL_NAMES",
    "FileProduct",
    "file_product",
    "file_products_from_text",
    "product_kind_for_path",
    "render_file_products_marker",
    "strip_file_products_markers",
    "with_file_products_marker",
]


@dataclass(frozen=True)
class FileProduct:
    """一件落盘产物：``{path, kind, derived_from?}``。

    ``path`` 是工作区相对路径，须与工具真正落盘的路径一致（已过 sanitize，不是模型
    请求的原始路径）。``kind`` 是归一后的产物类型（见 :func:`product_kind_for_path`）。
    ``derived_from`` 表达「本产物是从某个源文件派生的**导出件**」（``md_to_docx``：
    docx.derived_from = 源 md），供消费方把源文件降级为中间稿；移动 / 复制不是派生
    （源不是中间稿），保持 ``None``。
    """

    path: str
    kind: str
    derived_from: str | None = None


# 产物类型归一口径：文档 / 导出件保留自身扩展名（消费方据此区分导出件与中间稿），
# 其余按大类收敛。未知扩展名与无扩展名（目录）落到 ``file``。
_DOCUMENT_KINDS: dict[str, str] = {
    "md": "md",
    "markdown": "md",
    "doc": "docx",
    "docx": "docx",
    "pdf": "pdf",
    "ppt": "pptx",
    "pptx": "pptx",
    "xls": "xlsx",
    "xlsx": "xlsx",
    "csv": "csv",
    "htm": "html",
    "html": "html",
    "txt": "txt",
    "rtf": "txt",
    "epub": "epub",
}
_DATA_EXTS = frozenset({"json", "jsonl", "yaml", "yml", "toml", "xml", "ini", "parquet"})
_IMAGE_EXTS = frozenset({"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "avif"})
_ARCHIVE_EXTS = frozenset({"zip", "tar", "gz", "tgz", "bz2", "xz", "7z", "rar"})
_CODE_EXTS = frozenset(
    {
        "py", "pyi", "ts", "tsx", "js", "jsx", "mjs", "cjs", "vue", "svelte",
        "css", "scss", "sass", "less", "go", "rs", "java", "kt", "kts", "c", "h",
        "cc", "cpp", "hpp", "cs", "rb", "php", "swift", "m", "mm", "scala", "lua",
        "sh", "bash", "zsh", "ps1", "bat", "sql", "r", "ipynb",
    }
)


def product_kind_for_path(path: str) -> str:
    """归一产物类型：``md`` / ``docx`` / ``pdf`` / … / ``code`` / ``image`` / ``file``。"""
    name = (path or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    if "." not in name:
        return "file"
    ext = name.rsplit(".", 1)[-1].lower()
    if not ext:
        return "file"
    doc = _DOCUMENT_KINDS.get(ext)
    if doc:
        return doc
    if ext in _DATA_EXTS:
        return "data"
    if ext in _IMAGE_EXTS:
        return "image"
    if ext in _ARCHIVE_EXTS:
        return "archive"
    if ext in _CODE_EXTS:
        return "code"
    return "file"


def file_product(path: str, *, derived_from: str | None = None) -> FileProduct:
    """按路径推断 ``kind`` 建一件产物（写盘工具的常用捷径）。"""
    return FileProduct(
        path=path, kind=product_kind_for_path(path), derived_from=derived_from
    )


FILE_PRODUCTS_MARKER_PREFIX = "<!--agentcore:file_products:"
FILE_PRODUCTS_MARKER_SUFFIX = "-->"
_FILE_PRODUCTS_MARKER_RE = re.compile(
    re.escape(FILE_PRODUCTS_MARKER_PREFIX)
    + r"(.*?)"
    + re.escape(FILE_PRODUCTS_MARKER_SUFFIX),
    re.DOTALL,
)


def render_file_products_marker(products: Sequence[FileProduct]) -> str:
    """产物列表 → 一行机器尾注（消费方见 ``runs/serialize.py``）。"""
    payload: list[dict[str, Any]] = []
    for p in products:
        path = (p.path or "").strip()
        if not path:
            continue
        row: dict[str, Any] = {"path": path, "kind": p.kind or "file"}
        src = (p.derived_from or "").strip()
        if src:
            row["derived_from"] = src
        payload.append(row)
    return (
        FILE_PRODUCTS_MARKER_PREFIX
        + json.dumps(payload, ensure_ascii=False)
        + FILE_PRODUCTS_MARKER_SUFFIX
    )


def file_products_from_text(content: str) -> list[FileProduct]:
    """读出 ``content`` 里**每一条**尾注自报的产物（顺序保留）。

    格式损坏 / 截断的尾注整条跳过（best-effort：宁可漏账也不臆造产物）；缺 ``kind``
    时按路径推断，好让手写夹具不必逐条填。
    """
    out: list[FileProduct] = []
    for match in _FILE_PRODUCTS_MARKER_RE.finditer(content or ""):
        try:
            rows = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = str(row.get("path") or "").strip()
            if not path:
                continue
            kind = str(row.get("kind") or "").strip() or product_kind_for_path(path)
            src = str(row.get("derived_from") or "").strip()
            out.append(FileProduct(path=path, kind=kind, derived_from=src or None))
    return out


def strip_file_products_markers(content: str) -> str:
    """删掉 ``content`` 里所有产物尾注。

    工具输出可能**回显**别处的尾注（``file_read`` 读到一份带尾注的文本、写回执预览
    末段正好含尾注），那不是本次调用的产物。引擎盖章前先清一遍，台账便无需再按工具名
    做关联过滤（这正是名单要消失的地方）。
    """
    if FILE_PRODUCTS_MARKER_PREFIX not in (content or ""):
        return content or ""
    return _FILE_PRODUCTS_MARKER_RE.sub("", content).rstrip()


def with_file_products_marker(content: str, products: Iterable[FileProduct]) -> str:
    """引擎单点盖章：先清回显尾注，再按本次执行的自报产物追加一条（无产物则只清）。"""
    body = strip_file_products_markers(content or "")
    rows = [p for p in products if (p.path or "").strip()]
    if not rows:
        return body
    marker = render_file_products_marker(rows)
    return f"{body}\n{marker}" if body else marker


# 直接写盘的「笔」：与自报产物**无关**的治理面用它——落盘空转豁免、熔断不摘笔、
# allowlist 拒绝文案、写盘参数解析失败的分段写引导、失败尝试的 path 归因。这些都发生在
# 「还没有结果 / 结果是失败」的时刻，拿不到自报产物，只能按名字认；台账不再读它。
# ``code_execute`` 的写回是间接落盘，治理面不当它是笔（散文清单另见
# ``serialize.file_landing_tool_names``）。顺序即散文清单顺序。
LANDING_TOOL_NAMES: tuple[str, ...] = (
    "file_write",
    "file_append",
    "str_replace",
    "file_move",
    "file_copy",
)
LANDING_TOOLS: frozenset[str] = frozenset(LANDING_TOOL_NAMES)
