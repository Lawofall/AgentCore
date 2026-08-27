"""Skill body: revising_a_product."""

from __future__ import annotations

_REVISING_A_PRODUCT = """\
<revising_a_product>
当用户看到某个 worker 的产物后，要求对【它】做小改 / 增补 / 调整，或让同一人接着干强相关
的新任务（例如「把风险那节展开」「换个更正式的语气」「接着实现方案 B」），且仍由原角色
带着现场来干最合适时，用 `delegate` 并在该 task 上设 `continue_from_run_id`（取自团队执行
结果里标注的 run_id）——原作者带着 ReAct 轨迹接着干，而不是从零另派看不到旧稿的新人。
task 正文写清续干指令（改哪里 / 新任务是什么）；可与 depends_on / deliverable 同用。\
【成篇未写完】预算触顶 / 诚实「成篇未写完」→ 同优先 `continue_from_run_id` 续同一主文件（细则见 \
`long_form_writing`）；勿默认 `replaces_run_id` 换人。

【调查/审查批 → 用户确认按结论修·默认乙】多角调查或审查已收口、用户确认「按结论修」时：\
【默认】对手头各调查/审查 run 手写 tasks，并设 `continue_from_run_id`（可并行多角；task \
正文改成改码/落实指令即可）。换 title / 马甲文案（如「审查员」→「修复员」）【不算】换职能，\
【禁止】因此冷开新人。【禁止】此时再套 `playbook=diagnose_fix_verify` 冷开诊断→修补→验证新三角色——\
`diagnose_fix_verify` 仅覆盖【无先验调查批】的单症状修码。\
队员默认坐本任务桌相关工具面（含写盘 / 执行类；跨桌 list_folder_dir / read_folder_file 仅 CEO）。\
续派同人带现场即可；验码靠 task 正文点名 `test_run` 等，或甲冷开验证员。环境未装配\
执行面时走能力闸 / `ask_user`。

【修订落盘纪律·写进续派 task】已有成品按审校意见【逐条】用 `str_replace` 局部改（优先）；扩写章节用 \
`file_append`；整文件 `file_write` 覆盖允许，但须写出完整正文——勿惰性省略中段（正文自带\
「……（中间省略，已保留首尾）……」会残缺交付）。非空代码文件亦优先 `str_replace`；确需整盖时\
写出完整实现，勿用残缺骨架交差——补丁失败时对照失败回执盘片段再改或 escalate。\
写参被收成已落盘短状态后：先 `file_read` 取盘上真文，再 `str_replace`（优先）或按真文写，\
【禁止】把短状态当正文重发。

什么时候【不要】带现场续派，而改用冷委派（不设 continue_from_run_id）——仅甲：真换职能\
（需另一专长从头干，非仅改 title）、找不到可续现场、要把多份产物合并了再改、调查失败且无\
可用现场、或独立新任务（防上下文污染）。原稿 FAILED 但 transcript 仍在 → 仍可乙续派改写。\
若续派提示「现场已被内存 roster 淘汰」或找不到该 run、已达唤回上限、或目标仍在进行中，\
也按同样方式改冷委派，并设 replaces_run_id 标接手（值 = 被替换的原 run_id）——这不是 id \
抄错，而是现场已淘汰→冷委派。协调态里对失败 worker 的补派同理：必填 \
replaces_run_id，否则下游 depends_on 不会接到补跑。
</revising_a_product>"""
