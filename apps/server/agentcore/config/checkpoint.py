"""CEO checkpoint / ask_user gate and durable suspension settings."""

from pydantic import BaseModel


class CheckpointSettings(BaseModel):
    checkpoint_gate_enabled: bool = True
    # 提问确认交互统一 D2：默认无限等（None）；运维可设上限。同时覆盖 escalation /
    # debate_round 挂起上限（经 prepare 注入）。timeout 逻辑保留。
    checkpoint_timeout_seconds: float | None = None

    structured_suspension_persist_enabled: bool = True
    paused_turn_retention_days: int = 7
    paused_turn_sweep_interval_seconds: int = 6 * 3600
    paused_turn_sweep_batch_limit: int = 200

    # In-flight stream snapshot TTL (mirror paused_turns 7d). 0 disables the sweep.
    turn_stream_state_retention_days: int = 7
    turn_stream_state_sweep_interval_seconds: int = 6 * 3600
    turn_stream_state_sweep_batch_limit: int = 200

    # Durable RUNNING lease (crash recover): Postgres ownership + heartbeat; sweeper
    # redrives expired leases via recover_turn. Backend swappable for Redis later.
    turn_lease_enabled: bool = True
    turn_lease_ttl_seconds: int = 90
    turn_lease_heartbeat_seconds: float = 20.0
    turn_lease_sweep_interval_seconds: int = 30
    turn_lease_sweep_batch_limit: int = 50
    # Crash recover wall budget for orphan + factory rebuild + recover_turn (to arm).
    # After armed, heartbeat + await drive cover workers — do not bound drive here.
    turn_lease_recover_timeout_seconds: float = 600.0
    # After this many claim→ready cycles without a terminal settle, salvage instead of
    # another ready loop (safety net; primary path must still call redrive).
    turn_lease_recover_max_attempts: int = 3
    # Lifespan shutdown salvage: interrupt live turns like /stop, await unwind, then
    # force-release any leftover leases (never orphan on graceful shutdown).
    turn_shutdown_grace_seconds: float = 20.0
