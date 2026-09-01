"""handoff ``motion_card`` — worker 建议开辩的结构化命题卡（契约地基）。

可选字段：无卡 = 交接行为零变化。薄立场 ``stance`` 硬校验对齐
:mod:`agentcore.tools.builtin.debate.schema`（复用 ``STANCE_MAX_CHARS`` /
``validate_stance``；debate 侧若调整阈值，本卡自动跟随）。
"""

from __future__ import annotations

from typing import Any

# 对齐 debate 工具薄立场硬闸（见 tools/builtin/debate/schema.py）——只读复用，不复制阈值。
from agentcore.runtime.debate.constants import DEBATE_FORM_VALUES
from agentcore.tools.builtin.debate.schema import STANCE_MAX_CHARS, validate_stance

__all__ = [
    "MOTION_CARD_FORMS",
    "STANCE_MAX_CHARS",
    "parse_motion_card",
]

# parse 接受 DebateForm 全员（历史卡不硬拒）；广告 enum 只留 debate，见 handoff schema。
MOTION_CARD_FORMS = frozenset(DEBATE_FORM_VALUES)

_RETRY_TIP = (
    "请改写 `motion_card` 后重试 handoff："
    "motion=争议命题；sides≥2 且各方 stance 为一句话立场倾向（单句判断句，禁论证展开）；"
    "fact_pointers=事实指针（#rN / 路径 / URL）；rationale=为何必须对抗交锋而非继续调研；"
    "form 可选（正反，默认 debate）。"
)


def parse_motion_card(raw: Any) -> tuple[dict[str, Any] | None, str]:
    """解析可选命题卡。

    返回 ``(card, error)``：
    - 缺省 / null → ``(None, "")``（合法省略）
    - 合规 → ``(normalized, "")``
    - 超限或不完整 → ``(None, 引导语)``
    """
    if raw is None:
        return None, ""
    if raw == "":
        return None, ""
    if not isinstance(raw, dict):
        return None, f"`motion_card` 须为对象。{_RETRY_TIP}"

    motion = str(raw.get("motion") or "").strip()
    if not motion:
        return None, (
            f"`motion_card.motion` 不能为空（争议命题，可直接作 debate 的 motion）。"
            f"{_RETRY_TIP}"
        )

    rationale = str(raw.get("rationale") or "").strip()
    if not rationale:
        return None, (
            f"`motion_card.rationale` 不能为空"
            f"（说明为何必须对抗交锋、而非继续调研）。{_RETRY_TIP}"
        )

    form_raw = raw.get("form", None)
    if form_raw is None or (isinstance(form_raw, str) and not form_raw.strip()):
        form = "debate"
    elif isinstance(form_raw, str) and form_raw.strip() in MOTION_CARD_FORMS:
        form = form_raw.strip()
    else:
        return None, (
            f"`motion_card.form` 须为 debate（收到 {form_raw!r}）。{_RETRY_TIP}"
        )

    sides_raw = raw.get("sides")
    if not isinstance(sides_raw, list) or len(sides_raw) < 2:
        return None, (
            f"`motion_card.sides` 须为数组且至少 2 方"
            f"（每方含 key / name / stance）。{_RETRY_TIP}"
        )

    sides: list[dict[str, str]] = []
    seen: set[str] = set()
    for i, item in enumerate(sides_raw):
        if not isinstance(item, dict):
            return None, f"`motion_card.sides[{i}]` 须为对象。{_RETRY_TIP}"
        key = str(item.get("key") or "").strip()
        name = str(item.get("name") or "").strip()
        stance = str(item.get("stance") or "").strip()
        if not key or not name or not stance:
            return None, (
                f"`motion_card.sides[{i}]` 须含非空 key / name / stance。{_RETRY_TIP}"
            )
        if key in seen:
            return None, f"`motion_card.sides` 的 key 重复：`{key}`。{_RETRY_TIP}"
        stance_err = validate_stance(stance, side_key=key)
        if stance_err:
            # validate_stance 已含薄立场重试引导；前缀定位到 motion_card。
            return None, f"`motion_card`：{stance_err}"
        seen.add(key)
        sides.append({"key": key, "name": name, "stance": stance})

    if len(sides) < 2:
        return None, (
            f"`motion_card.sides` 至少需要 2 个有效参与方"
            f"（每个含非空 key / name / stance）。{_RETRY_TIP}"
        )

    pointers_raw = raw.get("fact_pointers")
    if pointers_raw is None:
        return None, (
            f"`motion_card.fact_pointers` 必填（list[str]；可为空列表，"
            f"但须显式给出）。{_RETRY_TIP}"
        )
    if not isinstance(pointers_raw, list):
        return None, f"`motion_card.fact_pointers` 须为字符串数组。{_RETRY_TIP}"
    fact_pointers = [str(p).strip() for p in pointers_raw if str(p).strip()]

    return {
        "motion": motion,
        "sides": sides,
        "fact_pointers": fact_pointers,
        "rationale": rationale,
        "form": form,
    }, ""
