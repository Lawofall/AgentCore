"""Model-facing observation envelopes for read (success, not tool failure).

Recoverable read limits / format gaps are facts the model can act on — not
``contract_failure`` essays that tell it to bother the user. Receipts do not
name a next tool.
"""

from __future__ import annotations


def format_observe_envelope(
    *,
    kind: str,
    path: str,
    type_label: str,
    size: int | None = None,
    text: str = "",
) -> str:
    """Stable envelope the model can parse. Facts only — no next-tool recipe."""
    size_line = f"size: {size}" if size is not None else "size: unknown"
    body = (text or "").strip() or "（无）"
    return (
        f"[观察信封]\n"
        f"kind: {kind}\n"
        f"path: `{path}`\n"
        f"{size_line}\n"
        f"type: {type_label}\n"
        f"text:\n{body}"
    )


def extract_failed_text(detail: str) -> str:
    """Model-facing fact; never library names or Python exceptions."""
    if "timeout" in (detail or "").lower():
        return "抽取超时，没有得到可用文本层。"
    return "抽文本失败，没有得到可用文本层。"


def scan_note(*, accepts_images: bool = False, images_sent: int = 0) -> str:
    """Same-tool page window and whether this model received pixels."""
    if images_sent:
        return (
            f"已把本窗 {images_sent} 页图像发给当前模型。"
            "后面的页再 read 并带 pages。"
        )
    if accepts_images:
        return (
            "当前主模型收图，但本窗页图像未能生成。"
            "后面的页再 read 并带 pages。"
        )
    return (
        "offset/limit 变不出文本层。"
        "当前主模型不收图。后面的页再 read 并带 pages（仍无文本层）。"
    )
