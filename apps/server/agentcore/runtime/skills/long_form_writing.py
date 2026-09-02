"""Skill body: long_form_landing (worker write-file HOW).

CEO 派工 / 分波 / 成品只装成品 → ``team_orchestration_advanced``。
"""

from __future__ import annotations

# Worker-facing landing HOW (no 派工 / continue_from / 多角编排).
_LONG_FORM_LANDING = """\
<超长落盘>
【主路径】一次 `file_write` 写入**完整正文**（含超长）；成篇后修订**只用** \
`str_replace`。`file_append` **仅**骨架填空路径（本 run 已成篇 prose 则禁 append）。\
超大风险可先短骨架再按节填空。

【主交付】用户要 PDF / Word：handoff 前对主文件调 `md_to_pdf` / `md_to_docx`。

写/append 成功回执即 artifact manifest（path / chars / lines / hash / 标题树 / 末段预览）\
——以此验真，禁止为质检再 run / file_read 回读正文。成篇后勿再用 file_append；整文件覆盖须完整正文。\
【例外】清参后改稿才可先 `file_read`——写参被收成已落盘短状态后须先读盘上真文，再 `str_replace`（优先）或按真文写，\
【禁止】把短状态当正文重发。

骨架追加前确认 path 与主文件一致；节间自带分隔。连续写失败（含参数不是合法 JSON）→ 改骨架分段，\
勿停用写文件、勿教用户修引号转义。本门禁仅约束一篇成文；调研多报告、代码多文件、建站 site/ 不套用。
</超长落盘>"""
