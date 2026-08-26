"""debate tool schema + argument parsing（薄适配层；域常量见 runtime.debate.constants）。"""

from __future__ import annotations

import re
from typing import Any

from agentcore.llm.model_ref import parse_model_input
from agentcore.runtime.debate import DebateForm, DebateSide
from agentcore.runtime.debate.constants import (
    CLOSING_LENGTH_HINT,
    CX_LENGTH_HINT,
    DEBATE_FORM_VALUES,
    DEBATE_OUTPUT_LIMIT,
    FORM_LABELS,
    LENGTH_HINT,
    QUICK_DEBATER_HINT,
)
from agentcore.tools.protocol import ToolResult

__all__ = [
    "DEBATE_OUTPUT_LIMIT",
    "LENGTH_HINT",
    "CLOSING_LENGTH_HINT",
    "CX_LENGTH_HINT",
    "QUICK_DEBATER_HINT",
    "FORM_LABELS",
    "DEBATE_DESCRIPTION",
    "DEBATE_PARAMETERS",
    "STANCE_MAX_CHARS",
    "err",
    "parse_form",
    "parse_background",
    "parse_sides",
    "parse_moderator_fields",
    "validate_stance",
]

# 薄立场兜底硬上限：真意图是「一句立场倾向、不带论证」（语义形状），不是字符数。
# 48 字闸三次被 LLM 突破致 debate 首调失败——放宽到 80 作兜底，主拦靠形状校验。
STANCE_MAX_CHARS = 80

# 语义形状违规（机械可查，任一命中即拒）：换行、分号、低歧义枚举标记、论证展开标记、
# 以及把辩手当执行器的论点清单 / 剧本指令措辞。
# 枚举标记不含 ASCII「N.」——与版本号 / 模型名（GLM 5.2、Claude 3.5）歧义，硬拒资格不足；
# 真论点清单靠顿号/括号号/圆圈号 + 分号/论证词/剧本词/字数兜底。
_STANCE_LIST_MARKERS = re.compile(
    r"(?:\([1-9]\)|（[1-9]）|[①②③④⑤⑥⑦⑧⑨]|"
    r"(?:^|[\s；;。，,])[1-9]、|"
    r"(?:^|[\s；;。，,])[一二三四五六七八九十][、.])"
)
_STANCE_ARGUMENT_MARKERS = re.compile(
    r"首先|其次|再次|最后|其一|其二|其三|一是|二是|三是|"
    r"一方面|另一方面|综上|总而言之"
)
_STANCE_SCRIPT_CUES = re.compile(
    r"核心论点|论点包括|请从.{0,16}角度|系统论证|论证角度|"
    r"重点论证|分点论证|依次论证|论证路径|请论证|务必论证"
)

_STANCE_RETRY_TIP = (
    "薄立场硬校验未通过：`stance` 只写【一句】该方主张什么结论的立场倾向。"
    "正例：「支持一审判决正确」/「认为判赔过重」。"
    "须为单句判断句；禁换行、分号、顿号/括号号等枚举展开、"
    "「首先/其次/一、二、」类论证展开，亦禁论点清单与论证角度指令——"
    "客观事实归 `background`，论点与论证路径由辩手自己检索构建。"
    "请改写成一句立场倾向后重试本工具。"
)

# Schema layer (工具面瘦身): short trigger + key param cues. HOW → debate_and_review skill.
DEBATE_DESCRIPTION = (
    "对抗性多视角思考：主持人驱动结构化辩论，交回【决策简报+交锋叙事线】（非终结）。"
    "form：debate=正反；red_team=红队压测（被审方 is_subject）；roundtable=圆桌。"
    "必填 motion+form+sides（≥2）；轮数/收敛主持人自调。"
    "独立并行调研用 delegate；无对立面/单点事实勿用。"
    "HOW→consult(debate_and_review)。"
)

DEBATE_PARAMETERS = {
    "type": "object",
    "properties": {
        "motion": {
            "type": "string",
            "description": "辩论命题（用户原话或提炼的争议命题）。",
        },
        "form": {
            "type": "string",
            "enum": list(DEBATE_FORM_VALUES),
            "description": (
                "debate=正反攻防；red_team=红队挑刺（被审方 is_subject）；"
                "roundtable=多方圆桌。流程细节→debate_and_review。"
            ),
        },
        "sides": {
            "type": "array",
            "description": "参与方（≥2）：正反=2，圆桌≥3，红队=被审方+≥1 红队。",
            "items": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "唯一英文短词（如 pro/con/red1）。",
                    },
                    "name": {
                        "type": "string",
                        "description": "展示立场/视角名；勿塞模型名（走 model）。",
                    },
                    "stance": {
                        "type": "string",
                        "maxLength": STANCE_MAX_CHARS,
                        "description": (
                            f"一句话立场（≤{STANCE_MAX_CHARS} 字）；只写主张结论，事实归 background。"
                        ),
                    },
                    "is_subject": {
                        "type": "boolean",
                        "description": "仅红队：标记被审方案方。",
                    },
                    "model": {
                        "type": "string",
                        "description": (
                            "辩手模型：目录身份 @platform/{id} 或 @byok/{provider_id}/{id}，"
                            "或可读提及（「glm-5.2」/「平台 Flash」/「DeepSeek」）。"
                            "空=跟主模型。勿写未加 @ 的 platform/{id} 路由键。"
                        ),
                    },
                },
                "required": ["key", "name", "stance"],
            },
        },
        "cross_model": {
            "type": "boolean",
            "description": (
                "仅说「跨模型」未点名时置 true 且各方 model 留空→默认对阵；"
                "无本旗标=同模型场。"
            ),
        },
        "thorough": {
            "type": "boolean",
            "description": "默认 true=辩透；false=快速单轮对碰。",
        },
        "background": {
            "type": "string",
            "description": (
                "（可选）赛前客观事实清单，每条附【来源】与【日期】；"
                "未决/推断勿当既定事实。细则→debate_and_review。"
            ),
        },
        "moderator_model": {
            "type": "string",
            "description": (
                "（可选）裁判模型：同 sides[].model 填法（目录身份或提及）。"
                "空=系统默认（可与辩手同模）。"
            ),
        },
    },
    "required": ["motion", "form", "sides"],
}


def err(msg: str) -> ToolResult:
    return ToolResult(tool_call_id="", success=False, output=msg, error=msg)


def parse_form(raw: Any) -> DebateForm:
    if isinstance(raw, str):
        try:
            return DebateForm(raw.strip())
        except ValueError:
            pass
    return DebateForm.DEBATE


def parse_background(raw: Any) -> str:
    """解析可选案件底料；仅收非空字符串，其它类型 / 缺省 → 空串（零行为变化路径）。"""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def validate_stance(stance: str, *, side_key: str = "") -> str | None:
    """薄立场硬校验。返回错误信息（含重试引导），或 None 表示通过。

    对齐 ``validate_search_query``：只拒不改写。真意图是单句立场倾向（不带论证）；
    机械拦换行 / 分号 / 低歧义枚举（顿号、括号号、圆圈号；不含 ASCII「N.」）/
    「首先其次」类展开与论点清单特征；:data:`STANCE_MAX_CHARS` 仅作兜底字数闸。
    """
    text = (stance or "").strip()
    if not text:
        return None
    where = f"sides[`{side_key}`].stance" if side_key else "stance"
    n = len(text)
    if n > STANCE_MAX_CHARS:
        return (
            f"{where} 过长（{n} 字，硬上限 {STANCE_MAX_CHARS}）。{_STANCE_RETRY_TIP}"
        )
    if "\n" in text or "\r" in text:
        return f"{where} 含换行，非单句立场形状。{_STANCE_RETRY_TIP}"
    if ";" in text or "；" in text:
        return f"{where} 含分号，非单句立场形状。{_STANCE_RETRY_TIP}"
    if (
        _STANCE_LIST_MARKERS.search(text)
        or _STANCE_ARGUMENT_MARKERS.search(text)
        or _STANCE_SCRIPT_CUES.search(text)
    ):
        return f"{where} 含论点清单/论证展开特征。{_STANCE_RETRY_TIP}"
    return None


def parse_sides(raw: Any) -> tuple[list[DebateSide], str]:
    """把 sides 原始数组解析为 :class:`DebateSide` 列表；返回 (sides, 错误信息)。"""
    if not isinstance(raw, list) or len(raw) < 2:
        return [], "debate 需要 sides（参与方数组，至少 2 个，每个含 key/name/stance）。"
    sides: list[DebateSide] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        stance = str(item.get("stance") or "").strip()
        if not key or not name or not stance:
            continue
        if key in seen:
            return [], f"sides 的 key 重复：`{key}`（每个参与方需唯一 key）。"
        stance_err = validate_stance(stance, side_key=key)
        if stance_err:
            return [], stance_err
        seen.add(key)
        model = str(item.get("model") or "").strip()
        parsed = parse_model_input(model)
        if parsed.kind == "bad_ref":
            return [], f"sides[`{key}`].model {parsed.error}"
        if parsed.kind == "ref":
            model, origin, provider_id = parsed.model, parsed.origin, parsed.provider_id
        elif parsed.kind == "empty":
            origin, provider_id = "", ""
        else:
            # 提及；库存/旧调用若仍带 origin/provider_id 则作消歧偏好（不在 schema 里）。
            origin = str(item.get("origin") or "").strip().lower()
            provider_id = str(item.get("provider_id") or "").strip()
            if origin and origin not in ("platform", "byok"):
                return (
                    [],
                    f"sides[`{key}`].origin 须为 platform|byok（收到 `{origin}`）。",
                )
            if origin == "platform":
                provider_id = ""
        sides.append(
            DebateSide(
                key=key,
                name=name,
                stance=stance,
                is_subject=bool(item.get("is_subject")),
                model=model,
                origin=origin if origin in ("platform", "byok") else "",
                provider_id=provider_id if origin == "byok" else "",
                run_id=str(item.get("run_id") or "").strip(),
            )
        )
    if len(sides) < 2:
        return [], "debate 至少需要 2 个有效参与方（每个含非空 key/name/stance）。"
    return sides, ""


def parse_moderator_fields(
    raw_model: Any,
    raw_origin: Any = None,
    raw_provider_id: Any = None,
) -> tuple[str, str, str, str]:
    """解析顶层裁判模型字段；返回 (model, origin, provider_id, 错误)。

    与 sides[].model 同规：``@`` 句柄展开；提及可缺 origin；库存 origin 仅作消歧偏好。
    """
    model = str(raw_model or "").strip()
    parsed = parse_model_input(model)
    if parsed.kind == "bad_ref":
        return "", "", "", f"moderator_model {parsed.error}"
    if parsed.kind == "empty":
        return "", "", "", ""
    if parsed.kind == "ref":
        return parsed.model, parsed.origin, parsed.provider_id, ""
    origin = str(raw_origin or "").strip().lower()
    provider_id = str(raw_provider_id or "").strip()
    if origin and origin not in ("platform", "byok"):
        return (
            "",
            "",
            "",
            f"moderator_origin 须为 platform|byok（收到 `{origin}`）。",
        )
    if origin == "platform":
        provider_id = ""
    return (
        model,
        origin if origin in ("platform", "byok") else "",
        provider_id if origin == "byok" else "",
        "",
    )
