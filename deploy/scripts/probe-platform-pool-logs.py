"""Count platform-pool events in production jsonl. No secrets."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

path = Path(os.environ.get("LOG_FILE") or "/data/logs/prod.jsonl")
files: list[Path] = []
if path.exists():
    files.append(path)
if path.parent.exists():
    files.extend(sorted(p for p in path.parent.glob(path.name + ".*") if p.is_file())[-8:])
print("log_files")
for f in files:
    print(f"  {f} exists={f.exists()} size={f.stat().st_size if f.exists() else 0}")

wanted: Counter[str] = Counter()
creds: Counter[str] = Counter()
fail: Counter[tuple] = Counter()
cool: Counter[tuple] = Counter()
fo: Counter[tuple] = Counter()
rl: Counter[tuple] = Counter()
blocked: Counter[tuple] = Counter()
reloaded_members: Counter[int] = Counter()
lines = 0
parse_err = 0
first_ts = ""
last_ts = ""

for f in files:
    if not f.exists():
        continue
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            lines += 1
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                parse_err += 1
                continue
            ts = str(ev.get("timestamp") or "")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
            name = ev.get("event") or ""
            if name.startswith("platform_pool.") or name in {
                "llm.call",
                "llm.call_failed",
                "llm.rate_limit_no_retry",
            }:
                wanted[name] += 1
            if name == "llm.call" and ev.get("credential_source") == "platform":
                creds[str(ev.get("platform_credential_id") or "(none)")] += 1
            if name == "llm.call_failed" and ev.get("credential_source") == "platform":
                fail[
                    (
                        str(ev.get("error_code") or ev.get("code") or ""),
                        str(ev.get("platform_credential_id") or ""),
                    )
                ] += 1
            if name == "platform_pool.cooling":
                cool[
                    (
                        ev.get("credential_id"),
                        ev.get("status"),
                        ev.get("limit_name"),
                        ev.get("source"),
                    )
                ] += 1
            if name == "platform_pool.failover":
                fo[(ev.get("from_credential_id"), ev.get("to_credential_id"))] += 1
            if name == "platform_pool.blocked":
                blocked[(ev.get("credential_id"), ev.get("reason"))] += 1
            if name == "platform_pool.reloaded":
                reloaded_members[int(ev.get("members") or 0)] += 1
            if name == "llm.rate_limit_no_retry" and ev.get("credential_source") == "platform":
                rl[
                    (
                        ev.get("reason"),
                        ev.get("cooldown_source"),
                        ev.get("retry_after_sec"),
                    )
                ] += 1

print(f"lines={lines} parse_err={parse_err} first_ts={first_ts} last_ts={last_ts}")
print("event_counts")
for k, n in wanted.most_common():
    print(f"  {k}={n}")
print("platform_llm.call_creds")
for k, n in creds.most_common():
    print(f"  {k}={n}")
print("platform_llm.call_failed")
for k, n in fail.most_common(20):
    print(f"  {k}={n}")
print("cooling")
for k, n in cool.most_common():
    print(f"  {k}={n}")
print("failover")
for k, n in fo.most_common():
    print(f"  {k}={n}")
print("blocked")
for k, n in blocked.most_common():
    print(f"  {k}={n}")
print("reloaded_members")
for k, n in reloaded_members.most_common():
    print(f"  members={k} count={n}")
print("platform_rate_limit_no_retry")
for k, n in rl.most_common(20):
    print(f"  {k}={n}")
