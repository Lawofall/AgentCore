"""辩论检索 token 预算并入统一 worker 顶。"""

from __future__ import annotations

from agentcore.config.engine import EngineSettings


def test_debate_uses_unified_worker_token_ceiling():
    """辩手不再有独立 ceiling；统一顶默认 8M（高于原辩论档 120k）。"""
    s = EngineSettings()
    assert not hasattr(s, "engine_debate_token_ceiling")
    assert not hasattr(s, "engine_worker_token_wind_down_ratio")
    assert s.engine_worker_token_ceiling == 8_000_000
    assert s.engine_worker_token_ceiling >= 120_000
    assert s.engine_worker_token_wind_down_reserve == 200_000


def test_worker_token_ceiling_override_covers_debate():
    """覆盖统一顶即覆盖辩手路径；≤0 关闭硬顶语义保留。"""
    s = EngineSettings(engine_worker_token_ceiling=50_000)
    assert s.engine_worker_token_ceiling == 50_000

    off = EngineSettings(engine_worker_token_ceiling=0)
    assert off.engine_worker_token_ceiling == 0

    reserve_off = EngineSettings(engine_worker_token_wind_down_reserve=0)
    assert reserve_off.engine_worker_token_wind_down_reserve == 0
