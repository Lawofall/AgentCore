"""Layout presets shared by the deterministic document exporters (docx / pdf).

首行缩进两字是中文正式文书的通例，放到技术文档上却很怪；Markdown 本身不带
「这是不是公文」的信号。所以档位由调用方（工具入参）显式给出，转换器**不**看正文
内容猜——没有任何内容启发式。一级标题居中不在档位内：Word 大标题居中是通例，
两个档位都开。

两档共用：A4、正文/列表 1.5 倍行距。official 另开两端对齐、公文页边距、页码——
仍是通例，不是 GB/T 9704 红头。PDF 只对齐行高，不做第二套视觉。
"""

from __future__ import annotations

from typing import Final, Literal

DocLayout = Literal["standard", "official"]

LAYOUT_STANDARD: Final[DocLayout] = "standard"
LAYOUT_OFFICIAL: Final[DocLayout] = "official"

DOC_LAYOUTS: Final[tuple[DocLayout, ...]] = (LAYOUT_STANDARD, LAYOUT_OFFICIAL)

# 中文公文通例：正文首行缩进两个字符（随字号走，不是固定磅值）。
FIRST_LINE_INDENT_CHARS: Final[int] = 2

# 正文/列表行距（倍数）。标题、代码、表格单元格保持紧凑，不吃这一档。
LINE_SPACING_MULTIPLE: Final[float] = 1.5
# 1.5 倍之后段后 6pt 会偏松；3pt 只作段间气口。
BODY_SPACE_AFTER_PT: Final[int] = 3

# A4。python-docx 默认 Letter，中文交付件必须显式钉死。
A4_WIDTH_CM: Final[float] = 21.0
A4_HEIGHT_CM: Final[float] = 29.7

# (top, bottom, left, right) cm。standard ≈ Word 默认；official ≈ 公文版心通例。
STANDARD_MARGINS_CM: Final[tuple[float, float, float, float]] = (2.54, 2.54, 3.17, 3.17)
OFFICIAL_MARGINS_CM: Final[tuple[float, float, float, float]] = (3.7, 3.5, 2.8, 2.6)

# 工具 schema 复用同一段措辞，两个导出器口径一致。
LAYOUT_PARAM_DESCRIPTION: Final[str] = (
    "排版档位（可选，默认 standard）：standard=技术文档/报告；"
    "official=中文正式文书（正文首行缩进两字；Word 另加两端对齐、公文页边距、页码）。"
    "用户要起诉状、公函、通知、声明等可提交的正式文书时传 official，技术文档别传。"
)

LAYOUT_INVALID_MESSAGE: Final[str] = (
    f"layout 须为 {' / '.join(DOC_LAYOUTS)}（留空 = {LAYOUT_STANDARD}）。"
)


def parse_layout(value: object) -> DocLayout | None:
    """Map a caller-supplied layout token to a preset; ``None`` when unrecognized.

    Empty / missing → ``standard``（现状默认）。未知取值返回 ``None``，由调用方明确
    报错——静默降级会让用户以为拿到了公文排版。
    """
    token = str(value or "").strip().lower()
    if not token:
        return LAYOUT_STANDARD
    if token == LAYOUT_OFFICIAL:
        return LAYOUT_OFFICIAL
    if token == LAYOUT_STANDARD:
        return LAYOUT_STANDARD
    return None
