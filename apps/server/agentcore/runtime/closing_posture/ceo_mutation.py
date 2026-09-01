"""CEO mutation / disk-landing claim detectors + withdrawn soft banner.

案 20260803-ceo-claim-edit-without-write · 软Ⅱ′
2026-08-04：【落盘说明】横幅已撤（与完成态叠放净负）；检测器保留；不做完成态降档。
2026-08-09 定案 B：零写落盘声称扫词硬回炉亦撤（``_zero_write_landing_rework`` 恒 None）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .core import _positive_hits

if TYPE_CHECKING:
    from agentcore.runtime.delegate.delivery_status import DeliveryVerdict

_CEO_MUTATION_DONE_CLAIMS = re.compile(
    r"(?:"
    r"已(?:成功)?(?:修改|修正|改好|改完|改妥)|"
    r"(?:代码|文件|源码)已(?:修改|修正|改好|更新|落盘)|"
    r"已将.{0,24}(?:修改|写入|落盘|更新)到|"
    r"✅\s*已(?:改|修改|修正)|"
    r"修改已完成|修正已完成|改动已落地"
    r")"
)

_CEO_WHOLE_FILE_PASTE = re.compile(
    r"(?:"
    r"请(?:你)?(?:自己|自行).{0,12}(?:替换|粘贴|覆盖).{0,24}整(?:个|份)?文件|"
    r"请(?:把|将).{0,20}整(?:个|份)?文件.{0,16}(?:粘贴|替换|覆盖)|"
    r"自己替换整文件|整文件自行(?:替换|粘贴)|"
    r"请(?:把|将)?下面.{0,20}完整.{0,12}(?:粘贴|替换).{0,16}(?:覆盖|文件)|"
    r"手动(?:把|将)?.{0,12}整(?:份|个)?(?:文件|代码).{0,12}粘贴"
    r")"
)

_CEO_DISK_LANDING_CLAIMS = re.compile(
    r"(?:"
    r"已落盘|文件已落盘|已写入工作区|已写入磁盘|"
    r"(?:报告|文档|评审|设计)已(?:写入|落盘|生成并落盘)|"
    r"已成功写入"
    r")"
)


def claims_ceo_mutation_done(content: str) -> bool:
    """True when CEO prose claims this-turn file mutation completed.

    Detector kept for tests / future gates; soft banner path withdrawn 2026-08-04.
    """
    return _positive_hits(_CEO_MUTATION_DONE_CLAIMS, content or "")


def asks_whole_file_user_paste(content: str) -> bool:
    """True when CEO asks the user to paste/replace a whole file themselves."""
    return bool(_CEO_WHOLE_FILE_PASTE.search(content or ""))


def claims_disk_landing(content: str) -> bool:
    """True when prose claims files landed on disk this turn (narrow closed set)."""
    return _positive_hits(_CEO_DISK_LANDING_CLAIMS, content or "")


def turn_has_product_write_evidence(
    *,
    landing_succeeded: bool = False,
    delivery_verdict: DeliveryVerdict | None = None,
) -> bool:
    """Whether this turn has product write evidence (CEO landing or accepted delivery files)."""
    if landing_succeeded:
        return True
    if delivery_verdict is not None:
        return bool(delivery_verdict.delivered_files)
    from agentcore.runtime.delegate.delivery_status import read_delivery_verdict

    verdict = read_delivery_verdict()
    if verdict is None:
        return False
    return bool(verdict.delivered_files)


def enforce_ceo_mutation_honesty(
    content: str,
    *,
    landing_succeeded: bool = False,
) -> str:
    """No-op: 【落盘说明】soft banner withdrawn (2026-08-04).

    Prefixing a hardcoded warning while leaving model「已落盘/已验收」intact caused
    conflicting status in-bubble (sample 92e9dcaa). Boarded: delete banner only;
    no completion-claim downgrade. ``landing_succeeded`` retained for call-site compat.
    """
    _ = landing_succeeded
    return content or ""


def _zero_write_landing_rework(
    content: str,
    *,
    delivery_verdict: DeliveryVerdict | None = None,
) -> str | None:
    """2026-08-09 定案 B：零写落盘声称扫词硬回炉已撤（恒 None）。

    曾扫「已改好/已落盘」等闭集 → finish_guard 清气泡重写；解释诚实规则时误伤。
    检测器 ``claims_*`` 仍保留（测试 / 观测）；不恢复【落盘说明】横幅；
    不改为软提醒。真吹牛靠对账档位（影子）与磁盘，不扫措辞回炉。
    """
    _ = (content, delivery_verdict)
    return None
