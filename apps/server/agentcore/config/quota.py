"""Cost display and quota settings.

Single default group ``quota_*`` for platform-paid paths (``billing_mode=platform``,
and any remaining platform-origin turns that share the same caps):

* 月成本 ¥10 · 日成本 ¥10 · 日请求 500 · 日 token 0（退出值守）

``0`` = that dimension is unlimited. Monthly / daily cost are CNY (float), converted
to nano-CNY at check time via ``NANO_PER_CNY``. API no longer ships an FX rate.
"""

from pydantic import BaseModel


class QuotaSettings(BaseModel):
    # Global defaults for platform-paid paths (人民币台账步骤 2).
    # daily_tokens=0 → 退出值守 (token 帽在多模型目录下映射不了钱, 由日成本维兜底).
    quota_daily_tokens: int = 0
    quota_monthly_cost_cny: float = 10.0
    quota_daily_cost_cny: float = 10.0
    quota_daily_requests: int = 500
