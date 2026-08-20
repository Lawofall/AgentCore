"""Skill body: long_form_writing."""

from __future__ import annotations

_LONG_FORM_WRITING = """\
<long_form_writing>
## 长文落盘（Artifact-first）

用户要产出超长单文档（报告、论文、综述、长 README、多章节手册、出行/行程成文）时：\
【主路径】一次 `file_write` 写入**完整正文**（含超长、无省略标记）；成篇后修订**只用** \
`str_replace`。`file_append` **仅**骨架填空路径（本 run 已成篇 prose 则禁 append）。\
【可选】防截断 / 超大风险时，可先短骨架再按节 `file_append` / `str_replace` 填空——非硬教条。\
短笔记 / 小配置 / 小片段仍一次写完。

【与多角协作划界】先看结局：一起弄懂/多路摸清（未明示成文）→ `parallel_brief`（默认；少扇出），\
**不要**本 skill 单写手、也**不要**直接套 `research_report`。仅提「论文/开源」当资料 ≠ 成文。\
用户**明示**要落盘成文且尚需广度取证、可拆 ≥2 独立角 → 先走 `research_report`（或同构 N 角\
笔记→提纲→撰稿；各角与主笔均 `form=files`+`artifacts`，【禁止】角 prose、仅主笔落盘），\
**不要**用本 skill 单写手一人包办自搜+成文。本 skill 单写手留给：材料已齐只扩写、用户已给大纲、\
改稿续写、短中篇无多角取证。

【多源合并·成篇优先】「多源材料合并→单一长交付」（开发计划/总纲/合并终稿等）：\
材料已齐可【一名带写权写手】（超长分波 / continue_from 见下）。\
目标仍为骨架 / `<!-- SECTION: -->` 占位时【禁止】派审校/清理连环；成篇后再允许独立审校；\
清理仅当用户明确要删且主文件已有实质正文。\
【禁止】写「CEO 自写」交差。超长合并勿塞极低 `max_rounds`。\
座位/交付物冲突 → wait / `cancel_worker` / replace；\
【禁止】宣称「流水线已在执行 / 合并进行中」糊弄；\
`depends_on` 解析失败后【禁止】吹「已挂上/可交付」。

**【成品文件只装成品】**用户要拿去直接用 / 提交的文件（起诉状 / 合同 / 公函 / 对外报告等）：\
task 里只要求写正文本身；核对提醒、假设、待补项、格式说明写进**你的回复**（或让队员写进 handoff），\
【禁止】要求把「使用前请核对」这类给用户看的元信息写进交付文件——那份文件会被原样打印 / 提交出去。

【主交付·MD → PDF/Word】主交付永远是 `.md`。用户要 PDF / Word / 可分享文件时：顺序 = \
成篇 `.md` → 调用 `md_to_pdf` 或 `md_to_docx`（对主文件）→ handoff。两者都是确定性导出、\
与执行沙箱无关，`code_execute=未装配` 也照样能交真 PDF/Word。【禁止】用多份 HTML 顶替 PDF；\
【禁止】把 code_execute + reportlab / python-docx 当主路径（确定性导出工具才是主路径）。

【单写手超长·跨 delegate 分波】材料已齐、仍走单写手，但预估很长（多章手册 / 合并大规格 /\
十余章以上）→ **勿**默认一人一次写完全文。按章跨多次 `delegate` 分波：第一波 task \
**写死章节范围**（如「只填第 1–N 章；其余骨架占位待续」），本波收口后再派下一波续填；\
短中篇 / 章数少仍可一波写完。

【成篇未写完·续作】预算触顶 / 诚实「成篇未写完」/ 用户要接着写同一交付物 → 下一刀 \
`delegate` **优先**设 `continue_from_run_id`（同人带现场续写同一主文件）；task 写清续填\
缺口章节。【禁止】并行再派同角色抢同一主路径。【禁止】复活 `continue_writing` 一键 CTA。\
`replaces_run_id` **仅**冷接手 / 替换失败节点（现场已淘汰、真换职能等，见 \
`revising_a_product`）——同交付物续写勿默认 replaces。

推荐编排：
1. 确认大纲（章节标题 + 每节要点）：用户明文要求把关 → 委派计划给提纲步设 \
`checkpoint_after=true`（或 `research_report` 成文专线），走结构化 durable 卡，勿纯聊天代卡；\
自主确认场景（用户未明文 / 任务轻量）才可对话式或自确认，必要时 ask_user。
2. 单写手：【主路径】一次 `file_write` 落【主文件】**完整正文**；成篇后只用 `str_replace` \
修订。【可选】防截断/超大：先短骨架（标题/锚点，或 `<!-- FILL:… -->` / \
`<!-- OUTLINE -->` / 章节小标题占位），再按节用 **str_replace 或 file_append** 填空。\
【禁止】对 Markdown / FILL / 大纲占位调用 `write_section`\
（那是建站 HTML 的 `<!-- SECTION:sN -->` 分区工具，与成篇 `.md` 无关）。
3. 多 worker 并行拆章（论文/综述/长报告允许）：各章可写到临时路径以免并发冲突，\
但【必须】在同一次 delegate 里写死——① 最终主文件同一路径（各章 brief + \
`deliverable.artifacts` 均指向它）；② 合并责任（末尾 merge worker `depends_on` 各章，\
或你 CEO 收口合并进主文件）。验收只认合并后的那一篇；禁止「各写各的章节文件就交」。\
（与上条「单写手分波」二选一形状：要么一人分波串写，要么多章并行+合并——勿混成并行同角色抢锁。）
4. 写/append 成功回执即 artifact manifest（path / chars / lines / hash / 标题树 / 末段预览）\
——以此验真，禁止为质检再 code_execute / file_read 回读正文；下一步仅 str_replace \
（局部改）或同轮 handoff；成篇后勿再用 file_append，整文件覆盖须完整正文。用户要 PDF / Word \
时在 handoff 前对主文件调 `md_to_pdf` / `md_to_docx`。\
【例外】≠ 为验真空转回读（仍认 artifact manifest）；清参后改稿才可先 `file_read`——\
写参被收成已落盘短状态后须先读盘上真文，再 `str_replace`（优先）或按真文写，\
【禁止】把短状态当正文重发。

纪律：
- 骨架路径追加前确认 path 与主文件一致；每节 content 自行带好段落分隔（如 leading `\\n\\n`）。
- 单节仍过长时，再拆成多轮 file_append / str_replace，不要硬塞万行单次调用。
- 连续写失败（含参数不是合法 JSON）→ 完整一次写入若仍失败则改可选骨架分段，勿停用写文件，\
勿教用户修引号转义。
- 本门禁仅约束「一篇成文」交付；调研透镜多报告、代码多文件、建站 site/ 多产物【不】套用。
</long_form_writing>"""

# Worker-facing landing HOW (no 派工 / continue_from / 多角编排). CEO keeps
# ``long_form_writing`` for when to delegate; workers consult this name.
_LONG_FORM_LANDING = """\
<long_form_landing>
## 长文落盘（你来写文件）

【主路径】一次 `file_write` 写入**完整正文**（含超长、无省略标记）；成篇后修订**只用** \
`str_replace`。`file_append` **仅**骨架填空路径（本 run 已成篇 prose 则禁 append）。\
【可选】防截断 / 超大风险时，可先短骨架再按节 `file_append` / `str_replace` 填空——非硬教条。\
短笔记 / 小配置 / 小片段仍一次写完。

【主交付·MD → PDF/Word】主交付永远是 `.md`。用户要 PDF / Word / 可分享文件时：顺序 = \
成篇 `.md` → 调用 `md_to_pdf` 或 `md_to_docx`（对主文件）→ handoff。两者都是确定性导出、\
与执行沙箱无关，`code_execute=未装配` 也照样能交真 PDF/Word。【禁止】用多份 HTML 顶替 PDF；\
【禁止】把 code_execute + reportlab / python-docx 当主路径。

写/append 成功回执即 artifact manifest（path / chars / lines / hash / 标题树 / 末段预览）\
——以此验真，禁止为质检再 code_execute / file_read 回读正文；下一步仅 str_replace \
（局部改）或同轮 handoff；成篇后勿再用 file_append，整文件覆盖须完整正文。用户要 PDF / Word \
时在 handoff 前对主文件调 `md_to_pdf` / `md_to_docx`。\
【例外】≠ 为验真空转回读（仍认 artifact manifest）；清参后改稿才可先 `file_read`——\
写参被收成已落盘短状态后须先读盘上真文，再 `str_replace`（优先）或按真文写，\
【禁止】把短状态当正文重发。

【禁止】对 Markdown / FILL / 大纲占位调用 `write_section`\
（那是建站 HTML 的 `<!-- SECTION:sN -->` 分区工具，与成篇 `.md` 无关）。

纪律：
- 骨架路径追加前确认 path 与主文件一致；每节 content 自行带好段落分隔（如 leading `\\n\\n`）。
- 单节仍过长时，再拆成多轮 file_append / str_replace，不要硬塞万行单次调用。
- 连续写失败（含参数不是合法 JSON）→ 完整一次写入若仍失败则改可选骨架分段，勿停用写文件，\
勿教用户修引号转义。
- 本门禁仅约束「一篇成文」交付；调研透镜多报告、代码多文件、建站 site/ 多产物【不】套用。
</long_form_landing>"""
