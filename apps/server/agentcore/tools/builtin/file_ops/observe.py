"""Model-facing observation envelopes for file_read (success, not tool failure).

Recoverable read limits / format gaps are facts the model can act on — not
``contract_failure`` essays that tell it to bother the user.
"""

from __future__ import annotations


def format_observe_envelope(
    *,
    kind: str,
    path: str,
    type_label: str,
    next_actions: str,
    size: int | None = None,
    text: str = "",
) -> str:
    """Stable envelope the model can parse; ``next`` is another tool, never 请用户."""
    size_line = f"size: {size}" if size is not None else "size: unknown"
    body = (text or "").strip() or "（无）"
    return (
        f"[观察信封]\n"
        f"kind: {kind}\n"
        f"path: `{path}`\n"
        f"{size_line}\n"
        f"type: {type_label}\n"
        f"text:\n{body}\n"
        f"next: {next_actions}"
    )


def ole_next() -> str:
    return (
        "按文件名归类或跳过正文。不要用 code_execute 解 OLE，不要原样重试 file_read。"
    )


def scan_next() -> str:
    return (
        "可用 read_image 看首页，或按文件名归类。"
        "offset/limit / start_page 变不出文本层。"
    )


def extract_failed_next() -> str:
    return (
        "可改 read_image 看首页，或按文件名归类继续整理。"
        "不要用 code_execute 硬解 Office/PDF。"
    )


def extract_failed_text(detail: str) -> str:
    """Model-facing fact; never library names or Python exceptions."""
    if "timeout" in (detail or "").lower():
        return "抽取超时，没有得到可用文本层。"
    return "抽文本失败，没有得到可用文本层。"


def source_too_large_next() -> str:
    return (
        "源文件超过抽取摄入顶，本工具不能整份吞入。"
        "可 read_image 看首页，或按文件名归类。"
    )


def binary_next() -> str:
    return (
        "按文件名归类或跳过正文。不要用 code_execute dump 二进制，不要原样重试 file_read。"
    )


def table_next(*, code_execute_assembled: bool) -> str:
    if code_execute_assembled:
        return (
            "file_read 不抽表格全文；用 code_execute（如 openpyxl / pandas）"
            "按工作区相对路径解析。不要手抄单元格。"
        )
    return (
        "file_read 不抽表格全文，本回合也没有按单元格解析的执行工具。"
        "用已给的结构面写原件结构报告并落盘待跑变换脚本，不要手抄数据。"
    )
