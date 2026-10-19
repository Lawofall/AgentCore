"""Names the model may still call after they left the tool table.

Did-you-mean for unknown *calls* still lives in ``registry._KNOWN_TOOL_ALIASES``
(never auto-execute). This module is for names that must **not** suggest a
replacement tool — Word/PDF export is a user-side file action.
"""

from __future__ import annotations

USER_SIDE_EXPORT_NAMES = frozenset({"md_export", "md_to_docx", "md_to_pdf"})

USER_SIDE_EXPORT_MESSAGE = (
    "成篇 `.md` 即收口。勿用 run / reportlab / python-docx 做 Word / PDF。"
)
