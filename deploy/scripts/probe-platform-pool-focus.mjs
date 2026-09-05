#!/usr/bin/env node
import { loadDeployEnv, sshCapture } from "./load-deploy-env.mjs";

loadDeployEnv();

const remote = `set -euo pipefail
docker exec agentcore-api python -c "
import asyncio, json, os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from sqlalchemy import func, select
from agentcore.cache.redis import redis_client
from agentcore.db.base import async_session_factory
from agentcore.db.models.billing import CostCall

r = redis_client()
keys = [k.decode() if isinstance(k, bytes) else str(k) for k in r.keys('ac:ppool:*')]
acct = [k for k in keys if ':acct:' in k]
sticky = [k for k in keys if ':sticky:' in k]
vals = Counter()
for k in sticky:
    raw = r.get(k)
    vals[(raw.decode() if isinstance(raw, bytes) else str(raw))] += 1
print('redis_total', len(keys), 'acct', len(acct), 'sticky', len(sticky))
print('sticky_values', dict(vals))
for k in acct:
    print('acct', k, r.get(k), 'ttl', r.ttl(k))

async def spend():
    now = datetime.now(UTC)
    async with async_session_factory() as s:
        last = (await s.execute(
            select(CostCall.created_at, CostCall.platform_credential_id, CostCall.cost['credential_source'].astext, CostCall.model)
            .where(CostCall.cost['credential_source'].astext == 'platform')
            .order_by(CostCall.created_at.desc()).limit(8)
        )).all()
        print('last_platform_calls')
        for row in last:
            print(' ', row[0].isoformat(), row[1], row[2], row[3])
        q = (
            select(func.date_trunc('day', CostCall.created_at), CostCall.platform_credential_id, func.count())
            .where(CostCall.created_at >= now - timedelta(days=14), CostCall.cost['credential_source'].astext == 'platform')
            .group_by(func.date_trunc('day', CostCall.created_at), CostCall.platform_credential_id)
            .order_by(func.date_trunc('day', CostCall.created_at).desc())
        )
        print('platform_by_day')
        for day, cid, n in (await s.execute(q)).all():
            print(' ', day.date().isoformat(), cid or '(null)', n)
asyncio.run(spend())

path = Path(os.environ.get('LOG_FILE') or '/data/logs/prod.jsonl')
files = [path] if path.exists() else []
if path.parent.exists():
    files.extend(sorted(p for p in path.parent.glob(path.name + '.*') if p.is_file())[-6:])
print('log_files', [str(f) for f in files], 'sizes', [f.stat().st_size if f.exists() else 0 for f in files])
wanted = Counter()
creds = Counter()
fail = Counter()
cool = Counter()
fo = Counter()
rl = Counter()
for f in files:
    if not f.exists():
        continue
    with f.open(encoding='utf-8', errors='replace') as fh:
        for line in fh:
            try:
                ev = json.loads(line)
            except Exception:
                continue
            name = ev.get('event') or ''
            if name.startswith('platform_pool.') or name in ('llm.call','llm.call_failed','llm.rate_limit_no_retry'):
                wanted[name] += 1
            if name == 'llm.call' and ev.get('credential_source') == 'platform':
                creds[str(ev.get('platform_credential_id') or '(none)')] += 1
            if name == 'llm.call_failed' and ev.get('credential_source') == 'platform':
                fail[(ev.get('error_code') or ev.get('code') or '', str(ev.get('platform_credential_id') or ''))] += 1
            if name == 'platform_pool.cooling':
                cool[(ev.get('credential_id'), ev.get('status'), ev.get('limit_name'), ev.get('source'))] += 1
            if name == 'platform_pool.failover':
                fo[(ev.get('from_credential_id'), ev.get('to_credential_id'))] += 1
            if name == 'llm.rate_limit_no_retry' and ev.get('credential_source') == 'platform':
                rl[(ev.get('reason'), ev.get('cooldown_source'), ev.get('retry_after_sec'))] += 1
print('event_counts')
for k,n in wanted.most_common():
    print(' ', k, n)
print('platform_llm.call_creds')
for k,n in creds.most_common():
    print(' ', k, n)
print('platform_llm.call_failed')
for k,n in fail.most_common(15):
    print(' ', k, n)
print('cooling', dict(cool))
print('failover', dict(fo))
print('platform_rate_limit_no_retry')
for k,n in rl.most_common(15):
    print(' ', k, n)
"
`

const result = sshCapture(remote, { allowFail: true });
if (result.stdout) process.stdout.write(result.stdout);
if (result.stderr) process.stderr.write(result.stderr);
process.exit(result.status ?? 1);
