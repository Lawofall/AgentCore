"""调研类 playbook：map_fanout / cite_write_review."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.playbooks._common import (
    RESEARCHER_ACADEMIC_SEARCH_DISCIPLINE,
    clean_str,
    clean_str_list,
    fold_fanout_slots,
)
from agentcore.workspace.stage_dirs import RESEARCH_DIR, REVIEWS_DIR

_RESEARCH_REPORT_OUTLINE_ARTIFACT = f"{RESEARCH_DIR}/提纲.md"
# 单角调研中间产物（≠ 成篇主文件 ``报告.md``；见 research_quality.DEFAULT_*）。
_RESEARCH_REPORT_DEFAULT_ANGLE_ARTIFACT = f"{RESEARCH_DIR}/调研要点.md"
# 审校落盘契约写死在 playbook（不靠运行时扫角色名抬 files）；本轮保持中文名。
_RESEARCH_REPORT_REVIEW_ARTIFACT = f"{REVIEWS_DIR}/审校报告.md"


def _research_angle_artifact(label: str) -> str:
    """Workspace-relative path for one cite_write_review angle dossier (angle in filename)."""
    from agentcore.workspace._paths import truncate_filename_utf8

    return f"{RESEARCH_DIR}/{truncate_filename_utf8(f'{label}调研报告.md')}"


def _brief_angle_artifact(label: str) -> str:
    """Workspace-relative path for one map_fanout direction note (angle in filename)."""
    from agentcore.workspace._paths import truncate_filename_utf8

    return f"{RESEARCH_DIR}/{truncate_filename_utf8(f'{label}方向笔记.md')}"


# A 档摸底验收：写进 map_fanout task（提示词纪律，非完成硬闸）。
_BRIEF_ACCEPTANCE = (
    "【摸底验收·够用即停】"
    "本任务是方向摸底、不是整座成果：默认自己交一页地图。"
    "目标：本方向能讲清「定位 / 技术栈或手段 / 进度或开放问题」即算够"
    "（非工程主题则对应本方向的是什么 / 怎么做 / 到哪了）。"
    "笔记只交一页地图：入口路径（文档 + 代码目录）、模块边界、三到五个开放问题。"
    "【禁止】写成完整要点 / 白皮书 / 终稿章节。"
    "【禁止】本轮替用户定优化方案或开做。"
    "手段：自己定位真实入口再读（找路径见身份说明），够用即停；"
    "【禁止】整仓通读、【禁止】把方向文案当成章节大纲或必读书单逐项打卡。"
    "收工：handoff 短摘要【必须】交（精炼结论 + 关键证据指引）；"
    "方向笔记用 file_write 落盘，过长则分段追加；"
    "【禁止】把笔记正文写进给用户看的回复；"
    "落盘是叠加、不得替代 handoff；"
    "只读/零写入摸底时【禁止】落盘改业务代码（本约定方向笔记除外）。"
)


def map_fanout(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """A 档·对齐推进：N 路并行摸底 → 方向笔记落盘；无提纲/撰稿/审校（交回 CEO 对话综述）.

    与 ``cite_write_review``（B/重成文专线）划界：本形状是默认——一起弄懂 / 多路摸清 /
    讨论对齐；未明示成文勿升 ``cite_write_review``。仅确有 ≥2 独立缝才用；人数跟缝走。
    验收口径见 ``_BRIEF_ACCEPTANCE``（一页地图 + 够用即停 + handoff 必交；非完成硬闸）。
    """
    topic = clean_str(args.get("topic"))
    if not topic:
        return [], ["map_fanout 需要 slot『topic』（要摸底对齐的主题）"]
    angles_raw = clean_str_list(args.get("angles"), cap=None)
    if len(angles_raw) < 2:
        return [], [
            "map_fanout 需要 slot『angles』且 ≥2 个可并行方向"
            "（单方向请手写 1 人 task，或明示成文走 cite_write_review）"
        ]
    angle_slots, angle_fold_note = fold_fanout_slots(angles_raw, label="摸底方向")
    fold_hint = f" {angle_fold_note}" if angle_fold_note else ""

    tasks: list[dict[str, Any]] = []
    for i, parts in enumerate(angle_slots):
        merged = len(parts) > 1
        label = " + ".join(parts)
        artifact = _brief_angle_artifact(label)
        scope = (
            f"专门摸底以下合并方向：{'、'.join(f'【{p}】' for p in parts)}。"
            f"本节点职责涵盖上述全部 {len(parts)} 个方向；须全部覆盖，勿只做第一项。"
            if merged
            else f"专门摸底这一个方向：{parts[0]}。"
        )
        task_body: dict[str, Any] = {
            "id": f"brief_{i}",
            "role": "方向专员",
            "task": (
                f"围绕主题【{topic}】，{scope}"
                "把方向句当目标，【禁止】当成章节大纲或必读书单逐项打卡。"
                "给出该方向一页地图（入口、边界、三到五个开放问题）。"
                "有出处写文件名或路径即可；【禁止】为凑台账编号继续挖。"
                "聚焦本方向、回报精炼结论供 CEO 与用户对齐。"
                f"{_BRIEF_ACCEPTANCE}"
                f"一页地图须用 file_write 落盘到 `{artifact}`"
                "（内容=入口 / 边界 / 开放问题 + 来源路径，不是 handoff 摘要的复制）；"
                "handoff 结构化简报照旧。"
                f"{fold_hint}"
            ),
            "deliverable": {
                "form": "files",
                "artifacts": [artifact],
            },
        }
        if angle_fold_note and merged:
            task_body["playbook_note"] = angle_fold_note
        tasks.append(task_body)
    return tasks, []


def cite_write_review(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """B/重成文专线：N×并行调研 → 提纲（默认 checkpoint）→ 写作 → 学术审校.

    仅用户明示成文且需正式长文/可提交（或已确认要审校满编）时用；讨论/形态未定勿首派；
    普通构想勿默认学术审校。一起弄懂/多路摸清/仅提论文开源当资料默认 ``map_fanout``。

    中间环（各路调研 + 提纲）与终稿同走约定文档契约：``form=files`` + 钉死
    ``AgentCore/文档/research/`` 下路径（角度名入文件名；提纲钉 ``提纲.md``）。
    成篇验收钉死单一主文件（``output_path`` / 默认 ``报告.md``）；
    主交付 `.md`；用户要 PDF/Word/可分享时 brief 钉 ``md → md_to_pdf | md_to_docx → handoff``
    （禁 HTML 顶替、禁 reportlab / python-docx 主路径）。若 CEO 手写并行拆章，须另加 merge 步
    并把各章 brief 写死同一路径——见 ``PAPER_PARALLEL_MERGE_DISCIPLINE``。
    """
    from agentcore.runtime.runs.research_quality import (
        INDEPENDENT_REVIEW_REPORT_DISCIPLINE,
        MD_EXPORT_DISCIPLINE,
        PAPER_PARALLEL_MERGE_DISCIPLINE,
        research_report_main_artifact,
    )

    topic = clean_str(args.get("topic"))
    if not topic:
        return [], ["cite_write_review 需要 slot『topic』（要调研并成文的主题）"]
    angles_raw = clean_str_list(args.get("angles"), cap=None)
    angle_slots, angle_fold_note = fold_fanout_slots(angles_raw, label="调研子方向")
    checkpoint = bool(args.get("checkpoint", True))
    audience = clean_str(args.get("audience"))
    deliverable = clean_str(args.get("deliverable")) or f"一篇关于【{topic}】的完整报告"
    main_path = research_report_main_artifact(clean_str(args.get("output_path")) or None)
    fold_hint = f" {angle_fold_note}" if angle_fold_note else ""
    outline_path = _RESEARCH_REPORT_OUTLINE_ARTIFACT

    tasks: list[dict[str, Any]] = []
    if angle_slots:
        research_ids = [f"research_{i}" for i in range(len(angle_slots))]
        for rid, parts in zip(research_ids, angle_slots, strict=True):
            merged = len(parts) > 1
            label = " + ".join(parts)
            artifact = _research_angle_artifact(label)
            scope = (
                f"专门调研以下合并子方向：{'、'.join(f'【{p}】' for p in parts)}。"
                f"本节点职责涵盖上述全部 {len(parts)} 个方向；须全部覆盖，勿只做第一项。"
                if merged
                else f"专门调研这一个子方向：{parts[0]}。"
            )
            task_body: dict[str, Any] = {
                "id": rid,
                "role": "调研员",
                # 成文综述：结构化学术检索挡位（偏论文站；junk → evidence_gap）。
                "search_policy": "academic_literature",
                "task": (
                    f"围绕主题【{topic}】，{scope}"
                    "给出该子方向的关键事实 / 现状 / 证据；"
                    "附来源（文件:行 或 链接）。"
                    "聚焦本子方向、回报精炼结论而非整段原文，别铺开到其它角度。"
                    f"完整调研要点须用 file_write 落盘到 `{artifact}`"
                    "（内容=本子方向完整要点 + 来源，不是 handoff 摘要的复制）；"
                    "handoff 结构化简报照旧，落盘是叠加、不得替代 handoff。"
                    f"{RESEARCHER_ACADEMIC_SEARCH_DISCIPLINE}"
                    f"{fold_hint}"
                ),
                "deliverable": {
                    "form": "files",
                    "artifacts": [artifact],
                    "citation_mode": "two_phase",
                },
            }
            if angle_fold_note and merged:
                task_body["playbook_note"] = angle_fold_note
            tasks.append(task_body)
    else:
        research_ids = ["research_0"]
        artifact = _RESEARCH_REPORT_DEFAULT_ANGLE_ARTIFACT
        tasks.append(
            {
                "id": "research_0",
                "role": "调研员",
                "search_policy": "academic_literature",
                "task": (
                    f"调研主题【{topic}】：覆盖关键事实 / 现状 / 主要观点与证据；"
                    "附来源。"
                    "回报精炼结论 + 关键证据指引，别回贴整段原文。"
                    f"完整调研要点须用 file_write 落盘到 `{artifact}`"
                    "（内容=本主题完整要点 + 来源，不是 handoff 摘要的复制）；"
                    "handoff 结构化简报照旧，落盘是叠加、不得替代 handoff。"
                    f"{RESEARCHER_ACADEMIC_SEARCH_DISCIPLINE}"
                ),
                "deliverable": {
                    "form": "files",
                    "artifacts": [artifact],
                    "citation_mode": "two_phase",
                },
            }
        )

    aud = f"，面向读者：{audience}" if audience else ""
    tasks.append(
        {
            "id": "outline",
            "role": "提纲编辑",
            "task": (
                f"综合上游各路调研，为主题【{topic}】拟一份报告提纲{aud}：列出章节结构与每节要点。"
                "据证据定结构（别凭空先写死），确保覆盖各调研方向、无重复无缺口。"
                f"可先 file_read `{RESEARCH_DIR}/` 下各路调研报告取完整要点；"
                f"结构化提纲须用 file_write 落盘到 `{outline_path}`"
                "（章节 + 每节要点全文，不是 handoff 摘要复制）；"
                "handoff 结构化简报照旧，落盘是叠加、不得替代 handoff。"
            ),
            "depends_on": research_ids,
            "deliverable": {
                "form": "files",
                "artifacts": [outline_path],
                "citation_mode": "two_phase",
            },
            "checkpoint_after": checkpoint,
        }
    )
    tasks.append(
        {
            "id": "write",
            "role": "撰稿人",
            "task": (
                f"严格按上游定稿的提纲、结合各路调研，写成{deliverable}。"
                "忠于调研事实与来源、不杜撰。"
                f"【主文件】整篇落盘到 `{main_path}`（验收只认这一路径）；"
                f"{PAPER_PARALLEL_MERGE_DISCIPLINE}"
                f"{MD_EXPORT_DISCIPLINE}"
                "【成篇落盘纪律·Artifact-first】① 【主路径】一次 file_write 完整正文；"
                "或先短骨架（标题+各章小标题/FILL 占位）再按节填空——"
                "禁止首写半章散文再 append；"
                "Markdown 填空只用 file_append / str_replace；"
                "② 骨架路径按章填空，一章写完再下一章；多章超长时本波只填写死的章节范围"
                "（或前几章），勿默认一人一次写完全文；③ 中等篇幅一次 "
                "file_write 写完全文（与①主路径一致）；④ 预算/token 不够写完下一章时，"
                "停在完整章边界，"
                "handoff 标明已完成与【待续】章节——由主管 `continue_from_run_id` "
                "同人续写同一主文件，禁止并行同角色抢同一路径；⑤ 禁止整篇 "
                "file_delete 后重写长文；成篇后修订优先 str_replace，整文件 file_write "
                "覆盖允许但须完整正文（勿惰性省略）；⑥ 写回执即 artifact manifest，禁止再对本文件 "
                "file_read 回读正文验真。"
            ),
            "depends_on": ["outline"],
            "deliverable": {
                "form": "files",
                "artifacts": [main_path],
                "citation_mode": "two_phase",
            },
        }
    )
    tasks.append(
        {
            "id": "review",
            "role": "学术审校员",
            "task": (
                f"对上游成稿（主文件 `{main_path}`）做学术审校（{deliverable}）："
                "核查学术准确性、逻辑完整性与引用规范；"
                "指出具体问题并给出可操作的修改建议，不重写全文。"
                f"【主文件】审校短报告落盘到 `{_RESEARCH_REPORT_REVIEW_ARTIFACT}`；"
                f"{INDEPENDENT_REVIEW_REPORT_DISCIPLINE}"
            ),
            "depends_on": ["write"],
            "deliverable": {
                "form": "files",
                "artifacts": [_RESEARCH_REPORT_REVIEW_ARTIFACT],
            },
            # 审校为依赖写作的收尾节点：通读长稿 + 核对出处。墙钟显式 300s（优先于统一
            # backstop）；token 顶走 worker_budget 统一回填。
            "timeout_ms": 300_000,
        }
    )
    return tasks, []
