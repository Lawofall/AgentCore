"""Skill body: team_delivery_env (点名后缀 / 真 Office / 产物路径)."""

from __future__ import annotations

_TEAM_DELIVERY_ENV = """\
<交付环境>
点名后缀、真 Office、产物路径——本条；组队形状仍走 \
`team_orchestration_advanced`。本机进桌 / 通道 → `consult(team_local_desk)`。\
对照 `<工作区>` 与开场表缺口。

【点名后缀】只认开场表 ≠ 凭印象猜导出器。\
表上有 `md_to_docx` / `md_to_pdf` → 交真 `.docx` / `.pdf`（与执行正交；落盘 `.md` 后导出）\
≠ 静默降成 `.md`/脚本、≠ 因缺口有 `run` 就说 Word/PDF 做不到、≠ `run` 顶替这两把确定性导出器。\
开场表无对应导出器、且缺口含 `run`（如 `.pptx`/`.xlsx`）→ 目标格式不可产：≠ 再派「跑脚本」空转。\
有等效替代（文档 → `.md` / HTML；真图形对象 → 可交互 HTML 或文字·表格版）→ \
先干完不依赖该选择的活，写清缺口与升级路径，升级走非阻塞追问；本条先于「点名载体/手段」短问。\
无等效替代 → `ask_user` 只覆盖这类目标，勿把开场表已有的导出器捎进缺口。\
用户要 Word 里真图形组织图 → 直接拒 + 给替代；仅文本/表格版 Word 才称能做并交真 `.docx`。\
用户明示当模板 → 先 `file_copy` 再改 ≠ 空白新建。\
缺口含 `package_install` 时结构自检 ≠ 外环已跑通；对照缺口写明未装包，或 `export_to_local`。\
数据表 → `consult(data_file_landing)`；交可打开的表时把该 skill 表质量基线写入 task，勿用章节清单冒充表结构。\
其它无执行交付 → `form=files` 落盘并标交付缺口，或 `form=prose`。

【路径】讨论/调研/审查类交付写 `AgentCore/文档/`：有专属出口才进 `research/`、`debate/`、`reviews/`；\
其余自定位；不知放哪再进 `工作稿/`。用户工程源码仍写业务路径。\
【产物路径】向用户列落盘须工作区相对**完整**路径（与约定文档出口同前缀）；\
以本回合写盘回执 / `deliverable.artifacts` / 交付对账为准 ≠ \
缩短成裸 `reviews/…`、同一清单混用两套前缀、或报未写入的路径。\
收口按工作区相对完整路径说 ≠ 讲「归位 / 移到工作区」或写完再搬。
</交付环境>"""
