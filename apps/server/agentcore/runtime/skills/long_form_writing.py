"""Skill body: long_form_landing (worker write-file HOW).

CEO 派工 / 分波 / 成品只装成品 → ``team_orchestration_advanced``。
"""

from __future__ import annotations

# Worker-facing landing HOW (no 派工 / continue_from / 多角编排).
_LONG_FORM_LANDING = """\
<超长落盘>
【主路径】一次 `file_write` 写入**完整正文**（含超长）；成篇后修订**只用** \
`str_replace`。

【截断】单次写参被截断时：改写更短但仍完整的 `file_write`；已落盘则用 `str_replace` \
在唯一锚后续写（写回执 `end_preview` 可作锚）。

【主交付】用户要 PDF / Word：handoff 前对主文件调 `md_to_pdf` / `md_to_docx`。

写成功回执即 artifact manifest（path / chars / lines / hash / 标题树 / 末段预览）\
——以此验真，禁止为质检再 run / file_read 回读正文。整文件覆盖须完整正文。\
【例外】清参后改稿才可先 `file_read`——写参被收成已落盘短状态后须先读盘上真文，再 `str_replace`（优先）或按真文写，\
【禁止】把短状态当正文重发。

连续写失败（含参数不是合法 JSON）→ 改更短完整写入或按锚续写，\
勿停用写文件、勿教用户修引号转义。本门禁仅约束一篇成文；调研多报告、代码多文件、建站 site/ 不套用。
</超长落盘>"""
