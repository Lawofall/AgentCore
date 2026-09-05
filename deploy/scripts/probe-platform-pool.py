"""Read-only production probe for the platform credential pool. No secrets."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select, text

from agentcore.config import settings
from agentcore.config.platform import parse_platform_model_credentials
from agentcore.db.base import async_session_factory
from agentcore.db.models.billing import CostCall
from agentcore.db.models.platform import PlatformCredential
from agentcore.llm.byok_provider_presets import is_opencode_go_base_url
from agentcore.llm.credentials import derive_platform_credential_id
from agentcore.llm.platform_credential_service import (
    _master_key_encryptor,
    _member_from_row,
    reload_platform_credential_pool,
)
from agentcore.llm.platform_pool import iter_platform_pool_members, pick_enabled_platform_pool_member
from agentcore.llm.platform_pool_scheduler import (
    account_runtime_for_admin,
    pick_last_resort_platform_pool_member,
    pick_schedulable_platform_pool_member,
    pool_has_enabled_members,
)
from agentcore.llm.platform_pool_state import get_pool_state_store
from agentcore.llm.resolve import platform_llm_credentials


def last4(key: str) -> str:
    return f"****{key[-4:]}" if len(key) >= 4 else "****"


def key_fp(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def describe_env() -> None:
    print("=== env flags (secrets redacted) ===")

    def show(name: str, *, secret: bool = False) -> None:
        raw = os.environ.get(name, "")
        if secret:
            print(f"{name}: set={bool(raw.strip())} len={len(raw.strip())}")
            return
        print(f"{name}: {raw[:120]!r}")

    for name in (
        "BILLING_MODE",
        "RATE_LIMIT_BACKEND",
        "PLATFORM_BASE_URL",
        "PLATFORM_MODEL",
        "PLATFORM_MODELS",
        "PLATFORM_BACKGROUND_MODEL",
        "PLATFORM_GO_SUBSCRIPTION_DAY",
        "WEB_CONCURRENCY",
        "UVICORN_WORKERS",
        "AGENTCORE_API_WORKERS",
        "LOG_FILE",
    ):
        show(name)
    for name in (
        "PLATFORM_API_KEY",
        "PLATFORM_MODEL_CREDENTIALS",
        "PLATFORM_CREDENTIAL_ID",
        "ENCRYPTION_KEY",
    ):
        show(name, secret=True)


async def probe_pool() -> None:
    print("=== pool members + spend + pick ===")
    now = datetime.now(UTC)
    print(f"now_utc={now.isoformat()}")
    print(f"billing_mode={settings.billing_mode}")
    print(f"rate_limit_backend={settings.rate_limit_backend}")
    print(f"platform_base_url={settings.platform_base_url}")
    print(f"platform_model={settings.platform_model}")
    overrides = parse_platform_model_credentials(settings.platform_model_credentials)
    print(f"model_credential_overrides={len(overrides)}")
    for mid, entry in overrides.items():
        print(
            f"  override model={mid!r} has_api_key={bool((entry.get('api_key') or '').strip())} "
            f"base_url={(entry.get('base_url') or '')[:80]!r} id={(entry.get('id') or '')!r}"
        )
    env_key = (settings.platform_api_key or "").strip()
    env_url = (settings.platform_base_url or "").rstrip("/")
    print(
        f"env_key_set={bool(env_key)} env_last4={last4(env_key) if env_key else '-'} "
        f"env_fp={key_fp(env_key) if env_key else '-'} "
        f"env_cred_id={derive_platform_credential_id(env_key, env_url) if env_key else '-'}"
    )
    print(f"env_is_go={is_opencode_go_base_url(env_url)}")

    async with async_session_factory() as session:
        loaded = await reload_platform_credential_pool(session)
        rows = (
            await session.execute(select(PlatformCredential).order_by(PlatformCredential.created_at))
        ).scalars().all()
        print(
            f"db_rows={len(rows)} snapshot_loaded={loaded} "
            f"snapshot_now={len(iter_platform_pool_members())}"
        )
        enc = _master_key_encryptor()
        print(f"encryptor_ok={enc is not None}")
        fps: dict[str, int] = {}
        for row in rows:
            member = _member_from_row(row, enc=enc)
            runtime = account_runtime_for_admin(row.id)
            rec = get_pool_state_store().get(row.id)
            go = is_opencode_go_base_url(row.base_url)
            created = row.created_at.isoformat() if row.created_at else None
            if member is None:
                print(
                    f"  member id={row.id} label={row.label!r} enabled={row.enabled} "
                    f"day={row.subscription_day} go={go} base={row.base_url!r} created={created} "
                    f"DECRYPT_FAILED status={runtime.status} recovery={runtime.recovery_at} "
                    f"limit={runtime.limit_name}"
                )
                continue
            fps[member.api_key] = fps.get(member.api_key, 0) + 1
            same_as_env = (
                bool(env_key)
                and member.api_key == env_key
                and member.base_url.rstrip("/") == env_url
            )
            print(
                f"  member id={row.id} label={row.label!r} enabled={row.enabled} "
                f"day={row.subscription_day} go={go} base={row.base_url!r} created={created} "
                f"last4={last4(member.api_key)} fp={key_fp(member.api_key)} same_as_env={same_as_env} "
                f"status={runtime.status} recovery={runtime.recovery_at} limit={runtime.limit_name} "
                f"store={None if rec is None else rec.status}"
            )
        print(
            f"duplicate_plaintext_keys={sum(1 for n in fps.values() if n > 1)} "
            f"unique_keys={len(fps)}"
        )

        picked = platform_llm_credentials(model=settings.platform_model)
        sched = pick_schedulable_platform_pool_member()
        last = pick_last_resort_platform_pool_member()
        enabled = pick_enabled_platform_pool_member()
        print(
            f"pick_path: has_enabled={pool_has_enabled_members()} "
            f"fallback={'pool' if enabled is not None else ('env' if env_key else 'none')} "
            f"sched={None if sched is None else sched.id} "
            f"last_resort={None if last is None else last.id} "
            f"picked_id={None if picked is None else picked.platform_credential_id} "
            f"picked_last4={last4(picked.api_key) if picked and picked.api_key else '-'}"
        )
        if picked is not None and env_key and picked.api_key == env_key:
            pool_ids = {m.id for m in iter_platform_pool_members()}
            print(
                f"WARNING: live pick equals env key; "
                f"in_pool={picked.platform_credential_id in pool_ids}"
            )

        payer = CostCall.cost["credential_source"].astext
        windows = [
            ("24h", now - timedelta(hours=24)),
            ("7d", now - timedelta(days=7)),
            ("30d", now - timedelta(days=30)),
        ]
        for label, since in windows:
            q = (
                select(
                    CostCall.platform_credential_id,
                    payer.label("payer"),
                    func.count().label("n"),
                    func.coalesce(func.sum(CostCall.cost_total_nano), 0).label("nano"),
                )
                .where(CostCall.created_at >= since)
                .group_by(CostCall.platform_credential_id, payer)
                .order_by(text("n desc"))
            )
            rows_sp = (await session.execute(q)).all()
            print(f"spend_{label}:")
            if not rows_sp:
                print("  (none)")
            for cid, src, n, nano in rows_sp:
                print(
                    f"  cred={cid or '(null)'} payer={src or '(none)'} "
                    f"calls={n} nano={int(nano)} yuan={int(nano) / 1e9:.4f}"
                )

        recent = (
            select(
                CostCall.created_at,
                CostCall.platform_credential_id,
                CostCall.model,
                CostCall.cost_total_nano,
                payer,
            )
            .order_by(CostCall.created_at.desc())
            .limit(20)
        )
        print("recent_calls:")
        for ts, cid, model, nano, src in (await session.execute(recent)).all():
            print(
                f"  {ts.isoformat()} cred={cid} payer={src} model={model} nano={int(nano)}"
            )


def probe_redis() -> None:
    print("=== redis pool keys ===")
    print(f"rate_limit_backend={settings.rate_limit_backend}")
    try:
        from agentcore.cache.redis import redis_client

        r = redis_client()
        keys = sorted(
            k.decode() if isinstance(k, bytes) else str(k) for k in r.keys("ac:ppool:*")
        )
        print(f"ppool_keys={len(keys)}")
        for key in keys[:80]:
            raw = r.get(key)
            ttl = r.ttl(key)
            val = raw.decode() if isinstance(raw, bytes) else str(raw)
            if len(val) > 240:
                val = val[:240] + "..."
            print(f"  {key} ttl={ttl} val={val}")
    except Exception as e:  # noqa: BLE001 — probe
        print(f"redis_error={type(e).__name__}: {e}")


def probe_logs() -> None:
    print("=== log event counts (pool + 429) ===")
    raw = os.environ.get("LOG_FILE") or "logs/prod.jsonl"
    path = Path(raw)
    if not path.is_absolute():
        for root in (Path("/app"), Path("/data"), Path.cwd()):
            candidate = root / raw
            if candidate.exists():
                path = candidate
                break
            if candidate.parent.exists():
                path = candidate
    print(f"LOG_FILE={raw!r} resolved={path} exists={path.exists()}")
    files: list[Path] = []
    if path.exists():
        files.append(path)
    parent = path.parent
    base = path.name
    if parent.exists():
        files.extend(sorted(p for p in parent.glob(base + ".*") if p.is_file())[-8:])
        files.extend(list(parent.glob("*.jsonl"))[:12])
    seen: list[Path] = []
    for f in files:
        if f not in seen:
            seen.append(f)

    wanted = {
        "platform_pool.failover",
        "platform_pool.cooling",
        "platform_pool.blocked",
        "platform_pool.reloaded",
        "platform_pool.decrypt_failed",
        "platform_pool.reload_failed",
        "platform_pool.redis_fail_open",
        "llm.call",
        "llm.call_failed",
        "llm.rate_limit_no_retry",
    }
    counts: Counter[str] = Counter()
    failed_reasons: Counter[tuple] = Counter()
    cooling: Counter[tuple] = Counter()
    failover: Counter[tuple] = Counter()
    call_creds: Counter[str] = Counter()
    limit_names: Counter[str] = Counter()
    lines = 0
    parse_err = 0
    for f in seen:
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    lines += 1
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        parse_err += 1
                        continue
                    name = ev.get("event") or ev.get("event_name") or ""
                    if name not in wanted and not str(name).startswith("platform_pool."):
                        continue
                    counts[name] += 1
                    if name == "platform_pool.cooling":
                        cooling[
                            (
                                ev.get("credential_id"),
                                ev.get("status"),
                                ev.get("limit_name"),
                                ev.get("source"),
                            )
                        ] += 1
                    elif name == "platform_pool.failover":
                        failover[(ev.get("from_credential_id"), ev.get("to_credential_id"))] += 1
                    elif name == "llm.call":
                        call_creds[str(ev.get("platform_credential_id") or "(none)")] += 1
                    elif name == "llm.call_failed":
                        failed_reasons[
                            (
                                ev.get("error_code") or ev.get("code") or "",
                                ev.get("platform_credential_id") or "",
                            )
                        ] += 1
                    ln = ev.get("limit_name")
                    if ln:
                        limit_names[str(ln)] += 1
        except OSError as e:
            print(f"read_error {f}: {e}")

    print(f"files={[str(f) for f in seen]} lines={lines} parse_err={parse_err}")
    print("event_counts:")
    for name, n in counts.most_common():
        print(f"  {name}={n}")
    print("cooling_tuples:")
    for k, n in cooling.most_common(20):
        print(f"  {k}={n}")
    print("failover_pairs:")
    for k, n in failover.most_common(20):
        print(f"  {k}={n}")
    print("llm.call credential_id:")
    for k, n in call_creds.most_common(20):
        print(f"  {k}={n}")
    print("llm.call_failed:")
    for k, n in failed_reasons.most_common(20):
        print(f"  {k}={n}")
    print("limit_names:")
    for k, n in limit_names.most_common(20):
        print(f"  {k}={n}")


def main() -> None:
    describe_env()
    print()
    asyncio.run(probe_pool())
    print()
    probe_redis()
    print()
    probe_logs()


if __name__ == "__main__":
    main()
