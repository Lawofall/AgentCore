"""Model-facing observation envelopes for read (success, not tool failure).

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
        "按文件名归类或跳过正文。不要原样重试 read。"
    )


def scan_next(*, accepts_images: bool = False, images_sent: int = 0) -> str:
    if images_sent:
        return (
            f"文本层几乎没有。已把本窗 {images_sent} 页图像发给当前模型。"
            "后面的页再 read 并带 pages。"
        )
    if accepts_images:
        return (
            "文本层几乎没有。当前主模型收图，但本窗页图像未能生成。"
            "后面的页再 read 并带 pages。"
        )
    return (
        "按文件名归类。offset/limit 变不出文本层。"
        "当前主模型不收图。后面的页再 read 并带 pages（仍无文本层）。"
    )


def extract_failed_next() -> str:
    return (
        "按文件名归类继续整理。"
    )


def extract_failed_text(detail: str) -> str:
    """Model-facing fact; never library names or Python exceptions."""
    if "timeout" in (detail or "").lower():
        return "抽取超时，没有得到可用文本层。"
    return "抽文本失败，没有得到可用文本层。"


def source_too_large_next() -> str:
    return (
        "源文件超过抽取摄入顶，本工具不能整份吞入。"
        "按文件名归类。"
    )


def binary_next() -> str:
    return (
        "按文件名归类或跳过正文。不要原样重试 read。"
    )


def archive_next(*, location: str | None = None) -> str:
    unpacker = (
        "云桌菜单里的 unzip / 7z"
        if location == "server"
        else "本机菜单里的 unzip / 7z"
    )
    return (
        "先 file_list 这个路径（压缩包当目录）或同名已展开目录。"
        f"仍要落盘则用{unpacker}解到工作区相对路径。"
    )


def table_next(*, code_execute_assembled: bool) -> str:
    if code_execute_assembled:
        return (
            "read 不抽表格全文；用 run（如 openpyxl / pandas）"
            "按干活根相对路径解析。不要手抄单元格。"
        )
    return (
        "read 不抽表格全文，本回合也没有按单元格解析的执行工具。"
        "用已给的列名、类型和样例写原件结构报告并落盘待跑变换脚本，不要手抄数据。"
    )


def pdf_next(*, code_execute_assembled: bool) -> str:
    if code_execute_assembled:
        return (
            "read 只看这一窗文本层。要对账、抽表、落盘整理用 run"
            "（PyMuPDF / pandas）按干活根相对路径解析。不要手抄单元格。"
        )
    return (
        "read 只看这一窗文本层。本回合没有按单元格解析的执行工具。"
        "用已给文本写结构报告并落盘待跑变换脚本，不要手抄数据。"
    )
