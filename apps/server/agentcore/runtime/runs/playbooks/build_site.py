"""建站：build_website（style 气质槽 + intensity 编制档）/ verify."""

from __future__ import annotations

from typing import Any

from agentcore.runtime.runs.playbooks._common import (
    CONVERSATION_ID_MECH_KEY,
    clean_str,
    clean_str_list,
)
from agentcore.runtime.runs.web_quality_rules import anti_slop_prompt_block

# 建站 / 落地页产物约定（下游便签与 QA 回读共用；禁 CEO 手糊内容→单前端）。
_BUILD_WEBSITE_DIR = "site"
# 简述同义词 → 规范键 topic；永不映射旧键 site（与目录 site/ 撞名已废）。
TOPIC_BRIEF_ALIASES: tuple[str, ...] = ("purpose", "brief", "description")


def normalize_website_topic_args(args: dict[str, Any]) -> dict[str, Any]:
    """Promote brief-synonym keys to canonical ``topic``; never accept legacy ``site``."""
    out = dict(args)
    if clean_str(out.get("topic")):
        return out
    for key in TOPIC_BRIEF_ALIASES:
        alt = clean_str(out.get(key))
        if alt:
            out["topic"] = alt
            return out
    return out

_BUILD_WEBSITE_COPY = f"{_BUILD_WEBSITE_DIR}/copy.md"
_BUILD_WEBSITE_HTML = f"{_BUILD_WEBSITE_DIR}/index.html"
_BUILD_WEBSITE_CSS = f"{_BUILD_WEBSITE_DIR}/styles.css"
_BUILD_WEBSITE_JS = f"{_BUILD_WEBSITE_DIR}/main.js"
_BUILD_WEBSITE_CONTRACT = f"{_BUILD_WEBSITE_DIR}/CONTRACT.md"
_BUILD_WEBSITE_DESIGN = f"{_BUILD_WEBSITE_DIR}/DESIGN.md"
_BUILD_WEBSITE_QA = f"{_BUILD_WEBSITE_DIR}/QA.md"
_DEFAULT_WEBSITE_SECTIONS = ("首屏英雄区", "卖点能力区", "行动号召区")
_DEFAULT_TOOLSHED_SECTIONS = ("应用外壳", "侧栏导航", "数据表格")

# 气质槽：默认营销/落地页；toolshed = 控制台 dense（旧独立 playbook 行为）。
STYLE_MARKETING = "marketing"
STYLE_TOOLSHED = "toolshed"
_ALLOWED_STYLES = frozenset({STYLE_MARKETING, STYLE_TOOLSHED})

# 编制档：默认 standard 三串；solo = 单节点一人整页。
INTENSITY_SOLO = "solo"
INTENSITY_STANDARD = "standard"
_ALLOWED_INTENSITIES = frozenset({INTENSITY_SOLO, INTENSITY_STANDARD})

# 文案包结构化板块（验收用 required_sections；主题约束进 task / required_sections）
# 首项对齐 task 里 visual thesis / 信息架构，must_contain_soft 仍软拦。
_BUILD_WEBSITE_COPY_SECTIONS = (
    "视觉 thesis",
    "品牌一句话",
    "各分区标题与正文",
    "CTA",
    "SEO",
)

_BUILD_WEBSITE_VISUAL_THESIS = (
    "【首步·visual thesis + 文案先行】先书面钉死视觉 thesis（品牌气质 / 对比度策略 / "
    "字体方向 / 动效克制原则，各 1–2 句），再写分区文案；"
    "禁止未立 thesis 就堆板块或套默认模板皮。"
)

_BUILD_WEBSITE_DOMAIN_HINT = (
    "站点类型默认按营销/落地页审美；若 topic（简述）明显是产品控制台 / 工具页，"
    "按工具页信息架构优先。"
)

_BUILD_TOOLSHED_VISUAL_THESIS = (
    "【首步·信息架构 + 文案先行】先书面钉死信息架构（主导航 / 主工作区 / "
    "筛选与详情层级，各 1–2 句），再写分区文案；"
    "禁止未立架构就堆板块，禁止套营销着陆页 hero / pricing 皮。"
)

_BUILD_TOOLSHED_DOMAIN_HINT = (
    "站点类型按产品控制台 / 工具台 dense UI；清晰信息架构与可读性优先，装饰克制。"
)

_BUILD_TOOLSHED_COPY_SECTIONS = (
    "信息架构",
    "产品一句话",
    "各分区标题与正文",
    "主操作 CTA",
    "空态说明",
)


def _website_qa_task(
    *,
    topic: str,
    copy_files_qa: str,
    tone_qa: str,
    deferred_ok: bool,
) -> str:
    """Shared whole-page QA task book (build_website tail + build_website_verify)."""
    defer_line = (
        "（可与建站同波；若本回合预算不足可跳过，由下一回合续派本验收——"
        "区块自动检查仍在各分区落盘时执行）"
        if deferred_ok
        else "（本 playbook 专跑整页验收；工作区已有 site/ 产物，勿重做文案/骨架/整站）"
    )
    return (
        f"独立【整页验收】站点【{topic}】{defer_line}："
        f"file_read 全部产物（"
        f"{copy_files_qa} / `{_BUILD_WEBSITE_DESIGN}` / `{_BUILD_WEBSITE_HTML}` / "
        f"`{_BUILD_WEBSITE_CSS}` / `{_BUILD_WEBSITE_JS}` / "
        f"`{_BUILD_WEBSITE_CONTRACT}`），核对 HTML↔CSS↔JS 接缝与交互方案一致性——"
        "契约列出的 class/id 均有实现、无挂空选择器；文案键已落地；"
        "实现色值 ⊆ DESIGN tokens；交互入口与契约一致。"
        f"{tone_qa}"
        "【接缝门禁】web_seam 静态门禁会拦挂空 class/id，验收时主动对照，"
        "勿留下死钩子。"
        "【视觉 QA·P1c】运行时在 web_quality hard 通过后自动多视口截图 → "
        "独立 VisionReader critic（对照 DESIGN.md + anti-slop）；"
        "有 critical findings 时至多 2 轮定向修补（str_replace/file_append）；"
        "无 browser(action=screenshot) 或无 VisionReader 时产物明示『未目验』，"
        "【禁止】谎称视觉 QA 通过。"
        f"用 file_write 落盘 `{_BUILD_WEBSITE_QA}`："
        "通过项 / 缺陷 / 局限声明（含视觉是否目验）；只报告不重写整站"
        "（视觉 critic 回炉时除外：可按 findings 定向补丁）。"
    )


def _prepare_site_inputs(
    args: dict[str, Any],
    *,
    playbook_name: str,
    pack: str,
    anti_slop_domain: str,
    default_sections: tuple[str, ...],
    topic_slot_hint: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Shared slot resolve + prompt fragments for standard / solo website builds."""
    from agentcore.runtime.runs.website_catalog import (
        catalog_contract_stub,
        catalog_prompt_block_skeleton,
        catalog_shared_css_for_skeleton,
        catalog_shell_bodies_for_sections,
    )
    from agentcore.runtime.runs.website_style import (
        DESIGN_MD_PATH,
        design_prompt_block,
        get_style_confirmation,
    )

    topic = clean_str(args.get("topic"))
    if not topic:
        return None, [
            f"{playbook_name} 需要 slot『topic』（{topic_slot_hint}；"
            f"产物目录固定 {_BUILD_WEBSITE_DIR}/，不是文件夹槽）"
        ]
    sections = clean_str_list(args.get("sections"), cap=None)
    if not sections:
        sections = list(default_sections)
    stack = clean_str(args.get("stack"))
    stack_hint = (
        f"（技术栈：{stack}）" if stack else "（默认静态 HTML/CSS/JS，可按 stack 调整）"
    )
    audience = clean_str(args.get("audience"))
    aud = f"，面向读者 / 访客：{audience}" if audience else ""
    all_sections_label = "、".join(sections)
    anti_slop = anti_slop_prompt_block(domain=anti_slop_domain)
    style_conf = get_style_confirmation(
        clean_str(args.get(CONVERSATION_ID_MECH_KEY)) or None
    )
    design_block = design_prompt_block(style=style_conf, domain=anti_slop_domain)
    catalog_block = catalog_prompt_block_skeleton(sections, pack=pack)
    catalog_shells = catalog_shell_bodies_for_sections(sections, pack=pack)
    catalog_css = catalog_shared_css_for_skeleton(pack=pack)
    catalog_contract = catalog_contract_stub(sections, pack=pack)
    return {
        "topic": topic,
        "sections": sections,
        "stack_hint": stack_hint,
        "aud": aud,
        "all_sections_label": all_sections_label,
        "anti_slop": anti_slop,
        "design_block": design_block,
        "design_md_path": DESIGN_MD_PATH,
        "catalog_block": catalog_block,
        "catalog_shells": catalog_shells,
        "catalog_css": catalog_css,
        "catalog_contract": catalog_contract,
    }, []


def _build_three_chain_site(
    args: dict[str, Any],
    *,
    playbook_name: str,
    pack: str,
    anti_slop_domain: str,
    default_sections: tuple[str, ...],
    visual_thesis: str,
    domain_hint: str,
    copy_sections: tuple[str, ...],
    topic_slot_hint: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Shared three-chain site pipeline: copy → frontend (DESIGN+page+CONTRACT) → QA.

    ``sections`` is a coverage checklist only — no partition fan-out / assemble.
    Slot ``topic`` = one-line brief; artifacts always under fixed ``site/``.
    """
    prep, errors = _prepare_site_inputs(
        args,
        playbook_name=playbook_name,
        pack=pack,
        anti_slop_domain=anti_slop_domain,
        default_sections=default_sections,
        topic_slot_hint=topic_slot_hint,
    )
    if prep is None:
        return [], errors

    topic = prep["topic"]
    all_sections_label = prep["all_sections_label"]
    stack_hint = prep["stack_hint"]
    aud = prep["aud"]
    anti_slop = prep["anti_slop"]
    design_block = prep["design_block"]
    design_md_path = prep["design_md_path"]

    tasks: list[dict[str, Any]] = [
        {
            "id": "copy",
            "role": "内容文案",
            "task": (
                f"{visual_thesis}{domain_hint}"
                f"{anti_slop}"
                "任务书只消费事实输入（品牌 / 受众 / 素材 / 用户明示偏好）；"
                "禁止在文案包里自拟配色色板 / 动效清单当施工图（色板归前端 DESIGN）。"
                f"为站点【{topic}】撰写完整文案包{aud}：品牌一句话、各区块标题 / 正文 / CTA、"
                "SEO 标题与 meta description、可选的微文案（按钮 / 脚注）。"
                f"须覆盖这些分区：{all_sections_label}。"
                f"用 file_write 落盘 `{_BUILD_WEBSITE_COPY}`；"
                "关键主张须可核对（有出处或标待核实），勿堆空话。"
                "收尾用 post_note(kind=decision) 广播文案分区清单，供前端对齐。"
            ),
            "deliverable": {
                "form": "files",
                "artifacts": [_BUILD_WEBSITE_COPY],
                "required_sections": list(copy_sections),
                "must_contain_soft": True,
                "web_quality_scan": False,
                "strict": True,
            },
        },
        {
            "id": "frontend",
            "role": "前端开发者",
            "task": (
                f"先 file_read 上游文案（`{_BUILD_WEBSITE_COPY}`），"
                f"为站点【{topic}】一人包整页实现{stack_hint}{aud}。"
                f"{design_block}"
                "先落 DESIGN（含 style 账 / tokens），再实现整页；"
                "颜色 / 字体只引用 DESIGN tokens，【禁止】散写未声明 hex。"
                f"须覆盖分区：{all_sections_label}。"
                f"用 file_write 写 `{_BUILD_WEBSITE_HTML}`（语义化分区容器 + 终态内容）、"
                f"`{_BUILD_WEBSITE_CSS}`（排版 / CSS 变量对齐 `{design_md_path}` tokens）、"
                f"`{_BUILD_WEBSITE_JS}`（交互 wiring）。"
                f"{prep['catalog_block']}"
                f"{prep['catalog_shells']}"
                f"{prep['catalog_css']}"
                f"{prep['catalog_contract']}"
                f"另用 file_write 写轻量 `{_BUILD_WEBSITE_CONTRACT}`："
                "列出各分区 catalog id/指针、id/class、文案键、交互约定——"
                "可基于上方 CONTRACT 起步表扩写；禁止含糊。"
                f"{anti_slop}"
                "挂空 class/id 会被 web_seam 拦下；"
                "坏 CSS / 编造联系方式 / 散色 / anti-slop 会被 web_quality 拦下。"
                "用 post_note(kind=decision) 广播 DESIGN / 页面 / 契约路径。"
            ),
            "depends_on": ["copy"],
            "deliverable": {
                "form": "files",
                "artifacts": [
                    _BUILD_WEBSITE_DESIGN,
                    _BUILD_WEBSITE_HTML,
                    _BUILD_WEBSITE_CSS,
                    _BUILD_WEBSITE_JS,
                    _BUILD_WEBSITE_CONTRACT,
                ],
                "placeholder_hard_exempt_artifacts": [
                    _BUILD_WEBSITE_CONTRACT,
                    _BUILD_WEBSITE_DESIGN,
                ],
                "web_quality_scan": True,
                "strict": True,
            },
        },
        {
            "id": "qa",
            "role": "页面 QA",
            "task": _website_qa_task(
                topic=topic,
                copy_files_qa=f"`{_BUILD_WEBSITE_COPY}`",
                tone_qa="",
                deferred_ok=True,
            ),
            "depends_on": ["frontend"],
            "ceiling_priority": True,
            "deliverable": {
                "form": "files",
                "artifacts": [_BUILD_WEBSITE_QA],
                "web_seam_scope": f"{_BUILD_WEBSITE_DIR}/",
                "placeholder_hard_exempt": True,
                "web_quality_scan": True,
                "visual_critic": True,
                "strict": True,
            },
            "timeout_ms": 300_000,
        },
    ]
    return tasks, []


def _build_solo_site(
    args: dict[str, Any],
    *,
    playbook_name: str,
    pack: str,
    anti_slop_domain: str,
    default_sections: tuple[str, ...],
    visual_thesis: str,
    domain_hint: str,
    copy_sections: tuple[str, ...],
    topic_slot_hint: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Single-node whole-page: copy + DESIGN + page (+ light self-check) in one frontend task."""
    prep, errors = _prepare_site_inputs(
        args,
        playbook_name=playbook_name,
        pack=pack,
        anti_slop_domain=anti_slop_domain,
        default_sections=default_sections,
        topic_slot_hint=topic_slot_hint,
    )
    if prep is None:
        return [], errors

    topic = prep["topic"]
    all_sections_label = prep["all_sections_label"]
    stack_hint = prep["stack_hint"]
    aud = prep["aud"]
    anti_slop = prep["anti_slop"]
    design_block = prep["design_block"]
    design_md_path = prep["design_md_path"]

    tasks: list[dict[str, Any]] = [
        {
            "id": "frontend",
            "role": "前端开发者",
            "task": (
                f"【intensity=solo·单人整页】一人完成文案 + DESIGN + 整页；"
                "无独立文案 / QA 波，收尾做轻验收自检。"
                f"{visual_thesis}{domain_hint}"
                f"{anti_slop}"
                "任务书只消费事实输入（品牌 / 受众 / 素材 / 用户明示偏好）；"
                "禁止在文案包里自拟配色色板 / 动效清单当施工图（色板归 DESIGN）。"
                f"为站点【{topic}】撰写完整文案包{aud}并落盘 `{_BUILD_WEBSITE_COPY}`："
                "品牌一句话、各区块标题 / 正文 / CTA、SEO 标题与 meta；"
                f"须覆盖分区：{all_sections_label}。"
                f"{design_block}"
                f"再一人包整页实现{stack_hint}：先落 DESIGN（含 style 账 / tokens），"
                "颜色 / 字体只引用 DESIGN tokens，【禁止】散写未声明 hex。"
                f"用 file_write 写 `{_BUILD_WEBSITE_HTML}` / `{_BUILD_WEBSITE_CSS}`"
                f"（对齐 `{design_md_path}` tokens）/ `{_BUILD_WEBSITE_JS}`。"
                f"{prep['catalog_block']}"
                f"{prep['catalog_shells']}"
                f"{prep['catalog_css']}"
                f"{prep['catalog_contract']}"
                f"另写轻量 `{_BUILD_WEBSITE_CONTRACT}`（catalog id、id/class、文案键、交互）。"
                "【轻验收】自检 HTML↔CSS↔JS 接缝、契约 class/id 均有实现、"
                "文案键落地、色值 ⊆ DESIGN tokens；挂空选择器会被 web_seam 拦下。"
                "只报告缺口并最小修补，勿另起独立 QA 岗。"
            ),
            "deliverable": {
                "form": "files",
                "artifacts": [
                    _BUILD_WEBSITE_COPY,
                    _BUILD_WEBSITE_DESIGN,
                    _BUILD_WEBSITE_HTML,
                    _BUILD_WEBSITE_CSS,
                    _BUILD_WEBSITE_JS,
                    _BUILD_WEBSITE_CONTRACT,
                ],
                "required_sections": list(copy_sections),
                "must_contain_soft": True,
                "placeholder_hard_exempt_artifacts": [
                    _BUILD_WEBSITE_CONTRACT,
                    _BUILD_WEBSITE_DESIGN,
                ],
                "web_quality_scan": True,
                "strict": True,
            },
        },
    ]
    return tasks, []


def build_website(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """建站 DAG：``intensity`` 编制档 + ``style`` 气质槽。

    ``intensity``：默认 ``standard``（文案→前端→独立 QA）；``solo`` = 单节点一人整页。
    ``style``：默认 marketing（落地页）；``toolshed`` = 控制台 dense / tool pack。
    """
    from agentcore.runtime.runs.website_catalog import PACK_MARKETING, PACK_TOOL_DENSE

    raw_style = clean_str(args.get("style"))
    style = raw_style or STYLE_MARKETING
    if style not in _ALLOWED_STYLES:
        return [], [
            f"build_website 未知 style『{style}』；"
            f"可选：{STYLE_MARKETING}（默认）/ {STYLE_TOOLSHED}"
        ]

    raw_intensity = clean_str(args.get("intensity"))
    intensity = raw_intensity or INTENSITY_STANDARD
    if intensity not in _ALLOWED_INTENSITIES:
        return [], [
            f"build_website 未知 intensity『{intensity}』；"
            f"可选：{INTENSITY_SOLO} / {INTENSITY_STANDARD}（默认）"
        ]

    builder = (
        _build_solo_site if intensity == INTENSITY_SOLO else _build_three_chain_site
    )
    if style == STYLE_TOOLSHED:
        return builder(
            args,
            playbook_name="build_website",
            pack=PACK_TOOL_DENSE,
            anti_slop_domain="tool",
            default_sections=_DEFAULT_TOOLSHED_SECTIONS,
            visual_thesis=_BUILD_TOOLSHED_VISUAL_THESIS,
            domain_hint=_BUILD_TOOLSHED_DOMAIN_HINT,
            copy_sections=_BUILD_TOOLSHED_COPY_SECTIONS,
            topic_slot_hint="要建的控制台 / 工具台一句话简述",
        )

    return builder(
        args,
        playbook_name="build_website",
        pack=PACK_MARKETING,
        anti_slop_domain="marketing",
        default_sections=_DEFAULT_WEBSITE_SECTIONS,
        visual_thesis=_BUILD_WEBSITE_VISUAL_THESIS,
        domain_hint=_BUILD_WEBSITE_DOMAIN_HINT,
        copy_sections=_BUILD_WEBSITE_COPY_SECTIONS,
        topic_slot_hint="要建的站点 / 落地页一句话简述",
    )


def build_website_verify(args: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Second-act whole-page QA only — for qa_deferred_budget follow-up turns."""
    topic = clean_str(args.get("topic"))
    if not topic:
        return [], [
            "build_website_verify 需要 slot『topic』（与建站时 topic 简述一致，"
            f"或写工作区站点名；产物目录固定 {_BUILD_WEBSITE_DIR}/，不是文件夹槽）"
        ]
    return [
        {
            "id": "qa",
            "role": "页面 QA",
            "task": _website_qa_task(
                topic=topic,
                copy_files_qa=f"`{_BUILD_WEBSITE_COPY}`（若存在）",
                tone_qa="",
                deferred_ok=False,
            ),
            "ceiling_priority": True,
            "deliverable": {
                "form": "files",
                "artifacts": [_BUILD_WEBSITE_QA],
                "web_seam_scope": f"{_BUILD_WEBSITE_DIR}/",
                "placeholder_hard_exempt": True,
                "web_quality_scan": True,
                "visual_critic": True,
            },
            "timeout_ms": 300_000,
        }
    ], []
