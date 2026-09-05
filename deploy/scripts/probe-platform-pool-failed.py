"""Inspect platform llm.call_failed rows. No user/message bodies."""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

path = Path(os.environ.get("LOG_FILE") or "/data/logs/prod.jsonl")
files = [path] if path.exists() else []
if path.parent.exists():
    files.extend(sorted(p for p in path.parent.glob(path.name + ".*") if p.is_file())[-8:])

keys_of_interest = (
    "event",
    "timestamp",
    "error_code",
    "code",
    "error_type",
    "status_code",
    "upstream_status",
    "platform_credential_id",
    "credential_source",
    "provider",
    "scenario",
    "retry_after_sec",
    "cooldown_source",
    "reason",
    "limit_name",
    "model",
    "exc_type",
    "error",
    "message",
)

failed: list[dict] = []
platform_calls: list[dict] = []
type_counts: Counter[tuple] = Counter()

for f in files:
    if not f.exists():
        continue
    with f.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = ev.get("event") or ""
            src = ev.get("credential_source")
            cid = ev.get("platform_credential_id")
            if name == "llm.call_failed" and (src == "platform" or cid):
                slim = {k: ev.get(k) for k in keys_of_interest if ev.get(k) is not None}
                failed.append(slim)
                type_counts[
                    (
                        slim.get("error_code") or slim.get("code") or "",
                        slim.get("exc_type") or "",
                        slim.get("upstream_status") or slim.get("status_code") or "",
                        str(slim.get("error") or slim.get("message") or "")[:80],
                    )
                ] += 1
            if name == "llm.call" and src == "platform":
                platform_calls.append(
                    {k: ev.get(k) for k in ("timestamp", "platform_credential_id", "model", "scenario") if ev.get(k) is not None}
                )

print(f"platform_or_tagged_call_failed={len(failed)}")
print("failed_shape")
for k, n in type_counts.most_common(20):
    print(f"  n={n} {k}")
print("failed_samples")
for row in failed[:25]:
    print(" ", json.dumps(row, ensure_ascii=False)[:500])
print(f"platform_llm.call={len(platform_calls)}")
for row in platform_calls[:10]:
    print(" ", row)
