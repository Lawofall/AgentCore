"""线上巡检①：全量 CID 清单 + 失败榜 + 可复算的窗口快照 / 跨窗 diff。

对应 `logs/reviews/README.md`「双轴巡检 · 轴 1 失败榜」。以前每轮巡检都复制一份一次性
脚本重跑，这里沉淀其中**稳定**的那部分：家族表口径（见
:mod:`agentcore.observability.query.failure_families`）、聚合形状、快照 schema、跨窗 diff。
本窗特有的分析（比如按某次部署切 pre/post）仍归临时脚本——那本来就该一次性。

三件这层要解决、`log_stats.py` 没解决的事：

1. **失败榜带反查**。``log_stats`` 的 ``error_clusters`` 只给 ``{签名, 次数, 样本}``，
   从「这类错 42 次」到「给我这 42 条 trace」每次都要另写 join。这里每个聚合行都直接
   带 ``conversation_ids`` / ``trace_ids``。
2. **CID 全量清单**。轴 2 要求窗内每个非空会话都审完，得先有清单（导出目录下会 join
   ``conversations.jsonl`` / ``messages.jsonl`` 拿标题与首末条用户气泡）。
3. **跨窗可复算**。落一份结构化快照，两份快照可离线 diff——「『不是目录』上窗 108 →
   本窗 2」这类核验判定不必再手工翻旧纪要。

纯只读：不写产品库、不进发布门禁、不做任何拦截或自动开案。
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentcore.observability.query.failure_families import (
    FAIL_OPEN_FAMILY_KEY,
    FAIL_OPEN_PULSE_S,
    FAILURE_FAMILIES,
    FAMILIES_BY_KEY,
    UNKNOWN_FAMILY,
    CompiledRegistry,
    clip_diagnostic_value,
    compile_registry,
    family_digests,
    registry_digest,
    resolve_family_key,
)
from agentcore.observability.query.jsonl import (
    JsonlLogSource,
    ReadFilter,
    ReadStats,
    iter_events,
)
from agentcore.observability.query.stats import error_signature
from agentcore.observability.query.timeutil import parse_timestamp
from agentcore.observability.query.tool_end import is_tool_failure

SNAPSHOT_SCHEMA_VERSION = 1

DEFAULT_MAX_IDS = 200
"""每个聚合行默认最多带回多少个 id。0 = 不限。``*_total`` 始终是真实总数。"""

DEFAULT_CLUSTER_LIMIT = 40

MUST_REVIEW_FAMILIES = frozenset(
    {
        "contract_failed",
        "write_pass_exhausted",
        "run_failed",
        "stream_stall",
        "ceiling_finalize",
        "test_run_budget",
        "turn_phase_gate",
        "memory_consolidation",
        "queue_pool",
        "rate_limit_fail_open",
    }
)
"""README 里点名「有量或新模式必审」的家族。命中即把该会话标 must_review。

刻意只做**标注**，不做拦截、不自动开案——巡检纪律见 .cursor/rules/intercept-discipline.mdc。
``rate_limit_fail_open`` 是安全信号（限流失效），不是普通 warning。
"""


class TimePulseGate:
    """First hit always; later hits in the same ``interval_s`` window are suppressed."""

    def __init__(self, interval_s: float = FAIL_OPEN_PULSE_S) -> None:
        self.interval_s = interval_s
        self._last: datetime | None = None

    def accept(self, ts: datetime | None) -> bool:
        if self._last is None:
            self._last = ts
            return True
        if ts is None:
            return False
        if (ts - self._last).total_seconds() >= self.interval_s:
            self._last = ts
            return True
        return False

DIAGNOSTIC_FIELDS = frozenset(
    {
        "body_preview",
        "cause",
        "code",
        "codes",
        "command",
        "detail",
        "details",
        "error",
        "error_code",
        "error_type",
        "event",
        "exc_type",
        "exception",
        "failure",
        "finish_reason",
        "kind",
        "message",
        "msg",
        "outcome",
        "phase",
        "purpose",
        "reason",
        "response_body",
        "runner",
        "state",
        "status",
        "stderr",
        "tool",
        "tools",
    }
)
"""家族 pattern 只匹配这些**诊断**字段，不匹配用户/模型内容。

这是白名单不是黑名单，因为黑名单必然漏。踩过的坑：把 ``args_preview`` / ``content``
一起扫进去后，队员交接摘要里一句「预算耗尽」就把该回合误记成外环验证预算耗尽——失败榜
counts 一旦掺进模型自述，跨窗序列就没意义了。
"""

_TEXT_LIMIT = 4000
_SAMPLE_LIMIT = 200
DEFAULT_REPEAT_USER_MIN = 3
"""同一用户在窗内重复撞同一家族 ≥ 此次数才进「单用户重复」视图。

总次数排序会把「一人 8 小时 17 次」沉到榜底；3 能捞出集中爆发，又滤掉偶发 1–2 次。
"""


def event_text(obj: dict[str, Any], *, limit: int = _TEXT_LIMIT) -> str:
    """把一条事件压成用于家族匹配的失败文本。

    只取 :data:`DIAGNOSTIC_FIELDS` 里的字符串值（顶层 + 一层嵌套，覆盖 ``payload.reason``
    这种形状），不是整行 JSON。每个值走 :func:`clip_diagnostic_value`（首部 + 末行），
    长 traceback 的异常类型才进得了匹配面。除了避开用户内容误伤，还有一条：导出的
    JSONL 里中文是 ``\\uXXXX`` 转义的——直接扫原始行的话，「不是目录」这类中文标记
    **永远匹配不到**（旧的一次性脚本正踩在这上面，只有工具 reason 那条支路把它救了回来）。
    """
    parts: list[str] = []
    size = 0

    def push(value: object) -> bool:
        nonlocal size
        if not isinstance(value, str) or not value:
            return True
        chunk = clip_diagnostic_value(value)
        parts.append(chunk)
        size += len(chunk) + 1
        return size < limit

    def push_maybe_list(value: object) -> bool:
        if isinstance(value, list):
            return all(push(nested) for nested in value[:20])
        return push(value)

    for key, value in obj.items():
        if isinstance(value, str):
            if key in DIAGNOSTIC_FIELDS and not push(value):
                break
        elif isinstance(value, dict):
            stop = False
            for nested_key, nested in value.items():
                if nested_key in DIAGNOSTIC_FIELDS and not push_maybe_list(nested):
                    stop = True
                    break
            if stop:
                break
        elif isinstance(value, list) and key in DIAGNOSTIC_FIELDS and not push_maybe_list(value):
            break
    return "\n".join(parts)


def failure_sample(obj: dict[str, Any]) -> str:
    """一行人读样本：优先真正的错误文案，退化到事件名。"""
    for key in ("error", "message", "msg", "reason", "detail"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            flat = " ".join(value.split())
            return flat[:_SAMPLE_LIMIT]
    status = obj.get("status")
    event = str(obj.get("event") or "?")
    if isinstance(status, str) and status.strip():
        return f"{event} status={status}"[:_SAMPLE_LIMIT]
    return event[:_SAMPLE_LIMIT]


class IdBag:
    """有界 id 收集器：留前 N 个用于反查，同时记真实总数。"""

    __slots__ = ("_ids", "_max", "_seen")

    def __init__(self, max_ids: int = DEFAULT_MAX_IDS) -> None:
        self._max = max_ids
        self._seen: set[str] = set()
        self._ids: list[str] = []

    def add(self, value: object) -> None:
        if not value:
            return
        text = str(value)
        if text in self._seen:
            return
        self._seen.add(text)
        if self._max <= 0 or len(self._ids) < self._max:
            self._ids.append(text)

    def __len__(self) -> int:
        return len(self._seen)

    def to_json(self) -> dict[str, Any]:
        return {
            "ids": list(self._ids),
            "total": len(self._seen),
            "truncated": len(self._seen) > len(self._ids),
        }


@dataclass
class FamilyTally:
    """一个家族在本窗的计数 + 反查 id。"""

    key: str
    events: int = 0
    by_event: Counter[str] = field(default_factory=Counter)
    conversations: IdBag = field(default_factory=IdBag)
    traces: IdBag = field(default_factory=IdBag)
    first_seen: str | None = None
    last_seen: str | None = None
    sample: str = ""

    def to_json(self) -> dict[str, Any]:
        fam = FAMILIES_BY_KEY.get(self.key)
        return {
            "key": self.key,
            "label": fam.label if fam else self.key,
            "events": self.events,
            "by_event": dict(self.by_event.most_common()),
            "conversations": self.conversations.to_json(),
            "traces": self.traces.to_json(),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "sample": self.sample,
        }


@dataclass
class RepeatUserRow:
    """同一用户在窗内重复撞同一家族——捞「总量低、但集中在少数人」的问题。"""

    user_id: str
    family: str
    events: int = 0
    conversations: IdBag = field(default_factory=IdBag)
    traces: IdBag = field(default_factory=IdBag)
    sample: str = ""

    def to_json(self) -> dict[str, Any]:
        fam = FAMILIES_BY_KEY.get(self.family)
        return {
            "user_id": self.user_id,
            "family": self.family,
            "label": fam.label if fam else self.family,
            "events": self.events,
            "conversations": self.conversations.to_json(),
            "traces": self.traces.to_json(),
            "sample": self.sample,
        }


@dataclass
class ClusterRow:
    """一条按归一化文案聚类的失败行——带 id，能直接反查。"""

    signature: str
    family: str
    count: int = 0
    sample: str = ""
    events: Counter[str] = field(default_factory=Counter)
    conversations: IdBag = field(default_factory=IdBag)
    traces: IdBag = field(default_factory=IdBag)

    def to_json(self) -> dict[str, Any]:
        return {
            "signature": self.signature,
            "family": self.family,
            "count": self.count,
            "sample": self.sample,
            "events": dict(self.events.most_common(5)),
            "conversations": self.conversations.to_json(),
            "traces": self.traces.to_json(),
        }


@dataclass
class ConversationRow:
    """CID 清单的一行。"""

    conversation_id: str
    title: str = ""
    user_messages: int = 0
    assistant_messages: int = 0
    first_activity: str | None = None
    last_activity: str | None = None
    first_user_preview: str = ""
    last_user_preview: str = ""
    failure_events: int = 0
    families: Counter[str] = field(default_factory=Counter)
    traces: IdBag = field(default_factory=IdBag)
    log_events: int = 0

    @property
    def nonempty(self) -> bool:
        return (self.user_messages + self.assistant_messages) > 0

    @property
    def must_review(self) -> bool:
        return any(k in MUST_REVIEW_FAMILIES for k in self.families)

    def to_json(self, *, messages_available: bool) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "title": self.title,
            "user_messages": self.user_messages,
            "assistant_messages": self.assistant_messages,
            "nonempty": self.nonempty if messages_available else None,
            "first_activity": self.first_activity,
            "last_activity": self.last_activity,
            "first_user_preview": self.first_user_preview,
            "last_user_preview": self.last_user_preview,
            "log_events": self.log_events,
            "failure_events": self.failure_events,
            "families": dict(self.families.most_common()),
            "must_review": self.must_review,
            "traces": self.traces.to_json(),
        }


@dataclass
class PatrolSnapshot:
    """一个巡检窗的结构化快照。落 ``logs/`` 下，不入仓。"""

    window_label: str = ""
    since: str | None = None
    until: str | None = None
    first_event_at: str | None = None
    last_event_at: str | None = None
    generated_at: str = ""
    source_kind: str = "jsonl"
    export_dir: str | None = None
    files: list[str] = field(default_factory=list)
    events_scanned: int = 0
    bad_lines: int = 0
    excluded_synthetic: int = 0
    messages_available: bool = False
    families: dict[str, FamilyTally] = field(default_factory=dict)
    clusters: list[ClusterRow] = field(default_factory=list)
    conversations: list[ConversationRow] = field(default_factory=list)
    repeat_users: list[RepeatUserRow] = field(default_factory=list)
    failure_events: int = 0
    repeat_user_min: int = DEFAULT_REPEAT_USER_MIN

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "generated_at": self.generated_at,
            "window": {
                "label": self.window_label,
                "since": self.since,
                "until": self.until,
                "first_event_at": self.first_event_at,
                "last_event_at": self.last_event_at,
            },
            "source": {
                "kind": self.source_kind,
                "export_dir": self.export_dir,
                "files": self.files,
                "events_scanned": self.events_scanned,
                "bad_lines": self.bad_lines,
                "excluded_synthetic": self.excluded_synthetic,
                "messages_available": self.messages_available,
            },
            "registry": {
                "digest": registry_digest(),
                "families": {
                    fam.key: {
                        "label": fam.label,
                        "digest": fam.digest(),
                        "revision": fam.revision,
                        "since": fam.since,
                    }
                    for fam in FAILURE_FAMILIES
                },
            },
            "totals": {
                "failure_events": self.failure_events,
                "conversations": len(self.conversations),
                "nonempty_conversations": sum(1 for c in self.conversations if c.nonempty),
                # 窗内只有日志事件、没有消息的会话（消息在窗外 / 只有系统活动）。
                "log_only_conversations": sum(1 for c in self.conversations if not c.nonempty),
                "must_review_conversations": sum(1 for c in self.conversations if c.must_review),
                "repeat_users": len(self.repeat_users),
                "repeat_user_min": self.repeat_user_min,
            },
            "families": {k: v.to_json() for k, v in self.families.items()},
            "repeat_users": [r.to_json() for r in self.repeat_users],
            "clusters": [c.to_json() for c in self.clusters],
            "conversations": [
                c.to_json(messages_available=self.messages_available) for c in self.conversations
            ],
        }


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _preview(text: str, n: int = 60) -> str:
    flat = " ".join((text or "").split())
    return flat[:n] + ("…" if len(flat) > n else "")


def load_conversation_inventory(
    export_dir: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, ConversationRow]:
    """从导出目录流式建 CID 清单（窗内有消息的会话 + 标题 + 首末用户气泡）。

    只在 ``--export-dir`` 模式下可用：``logs/dev.jsonl`` 不含消息正文（正文在 Postgres）。
    """
    rows: dict[str, ConversationRow] = {}
    titles: dict[str, str] = {}

    conv_path = export_dir / "conversations.jsonl"
    if conv_path.exists():
        with open(conv_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cid = obj.get("id") or obj.get("conversation_id")
                if cid:
                    titles[str(cid)] = str(obj.get("title") or "")

    msg_path = export_dir / "messages.jsonl"
    if not msg_path.exists():
        return rows

    with open(msg_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            cid = obj.get("conversation_id")
            role = obj.get("role")
            if not cid or role not in ("user", "assistant"):
                continue
            ts = parse_timestamp(obj.get("created_at"))
            if ts is None:
                continue
            if since is not None and ts < since:
                continue
            if until is not None and ts > until:
                continue
            cid = str(cid)
            row = rows.get(cid)
            if row is None:
                row = ConversationRow(conversation_id=cid, title=titles.get(cid, ""))
                rows[cid] = row
            stamp = _iso(ts)
            if row.first_activity is None or stamp < row.first_activity:
                row.first_activity = stamp
            if row.last_activity is None or stamp > row.last_activity:
                row.last_activity = stamp
            if role == "user":
                row.user_messages += 1
                preview = _preview(str(obj.get("content") or ""))
                if not row.first_user_preview:
                    row.first_user_preview = preview
                row.last_user_preview = preview
            else:
                row.assistant_messages += 1
    return rows


def scan_patrol(
    log_file: Path,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    window_label: str = "",
    include_synthetic: bool = False,
    export_dir: Path | None = None,
    max_ids: int = DEFAULT_MAX_IDS,
    cluster_limit: int = DEFAULT_CLUSTER_LIMIT,
    repeat_user_min: int = DEFAULT_REPEAT_USER_MIN,
    registry: CompiledRegistry | None = None,
) -> PatrolSnapshot:
    """扫一个窗，产出 CID 清单 + 失败榜 + 快照。"""
    reg = registry or compile_registry()
    filt = ReadFilter(since=since, include_synthetic=include_synthetic)
    read_stats = ReadStats()

    conversations: dict[str, ConversationRow] = {}
    if export_dir is not None:
        conversations = load_conversation_inventory(export_dir, since=since, until=until)
    messages_available = bool(conversations) or (
        export_dir is not None and (export_dir / "messages.jsonl").exists()
    )

    families: dict[str, FamilyTally] = {}
    clusters: dict[str, ClusterRow] = {}
    repeat_acc: dict[tuple[str, str], RepeatUserRow] = {}
    first_at: datetime | None = None
    last_at: datetime | None = None
    failure_events = 0
    cid_event_counts: Counter[str] = Counter()
    cid_traces: dict[str, IdBag] = defaultdict(lambda: IdBag(max_ids))
    fail_open_pulse = TimePulseGate()

    def tally(key: str) -> FamilyTally:
        row = families.get(key)
        if row is None:
            row = FamilyTally(
                key=key, conversations=IdBag(max_ids), traces=IdBag(max_ids)
            )
            families[key] = row
        return row

    for obj in iter_events(JsonlLogSource(log_file), filt, stats=read_stats):
        ts = parse_timestamp(obj.get("timestamp"))
        if until is not None and (ts is None or ts > until):
            continue
        if ts is not None:
            if first_at is None or ts < first_at:
                first_at = ts
            if last_at is None or ts > last_at:
                last_at = ts

        event = str(obj.get("event") or "")
        cid = str(obj.get("conversation_id") or "")
        trace_id = str(obj.get("trace_id") or "")
        user_id = str(obj.get("user_id") or "")
        if cid:
            cid_event_counts[cid] += 1

        tool_failed = is_tool_failure(obj)
        level = str(obj.get("level") or "").lower()
        text = event_text(obj)
        hits = reg.match(event=event, text=text, tool_failed=tool_failed)
        if FAIL_OPEN_FAMILY_KEY in hits and not fail_open_pulse.accept(ts):
            hits = [key for key in hits if key != FAIL_OPEN_FAMILY_KEY]

        if not hits and level not in ("error", "critical"):
            continue
        if not hits:
            hits = [UNKNOWN_FAMILY]

        failure_events += 1
        stamp = _iso(ts) if ts else None
        sample = failure_sample(obj)

        for key in hits:
            row = tally(key)
            row.events += 1
            row.by_event[event or "?"] += 1
            row.conversations.add(cid)
            row.traces.add(trace_id)
            if stamp:
                if row.first_seen is None or stamp < row.first_seen:
                    row.first_seen = stamp
                if row.last_seen is None or stamp > row.last_seen:
                    row.last_seen = stamp
            if not row.sample:
                row.sample = sample
            if user_id:
                uk = (user_id, key)
                ru = repeat_acc.get(uk)
                if ru is None:
                    ru = RepeatUserRow(
                        user_id=user_id,
                        family=key,
                        conversations=IdBag(max_ids),
                        traces=IdBag(max_ids),
                    )
                    repeat_acc[uk] = ru
                ru.events += 1
                ru.conversations.add(cid)
                ru.traces.add(trace_id)
                if not ru.sample:
                    ru.sample = sample

        signature = error_signature(f"[{event or '?'}] {sample}")
        cluster = clusters.get(signature)
        if cluster is None:
            cluster = ClusterRow(
                signature=signature,
                family=hits[0],
                sample=sample,
                conversations=IdBag(max_ids),
                traces=IdBag(max_ids),
            )
            clusters[signature] = cluster
        cluster.count += 1
        cluster.events[event or "?"] += 1
        cluster.conversations.add(cid)
        cluster.traces.add(trace_id)

        if cid:
            row_c = conversations.get(cid)
            if row_c is None:
                row_c = ConversationRow(conversation_id=cid)
                conversations[cid] = row_c
            row_c.failure_events += 1
            for key in hits:
                row_c.families[key] += 1
            if trace_id:
                cid_traces[cid].add(trace_id)

    for cid, row_c in conversations.items():
        row_c.log_events = cid_event_counts.get(cid, 0)
        bag = cid_traces.get(cid)
        if bag is not None:
            row_c.traces = bag

    repeat_users = sorted(
        (r for r in repeat_acc.values() if r.events >= repeat_user_min),
        key=lambda r: (-r.events, r.family, r.user_id),
    )
    ordered_clusters = sorted(clusters.values(), key=lambda c: -c.count)[:cluster_limit]
    ordered_conversations = sorted(
        conversations.values(),
        key=lambda c: (
            0 if c.must_review else 1,
            -c.failure_events,
            -(c.user_messages + c.assistant_messages),
            c.conversation_id,
        ),
    )

    return PatrolSnapshot(
        window_label=window_label,
        since=_iso(since) if since else None,
        until=_iso(until) if until else None,
        first_event_at=_iso(first_at) if first_at else None,
        last_event_at=_iso(last_at) if last_at else None,
        generated_at=_iso(datetime.now(UTC)),
        source_kind="export" if export_dir is not None else "jsonl",
        export_dir=str(export_dir) if export_dir is not None else None,
        files=[str(p) for p in read_stats.files],
        events_scanned=read_stats.total_kept,
        bad_lines=read_stats.bad_lines,
        excluded_synthetic=read_stats.excluded_synthetic,
        messages_available=messages_available,
        families=families,
        clusters=ordered_clusters,
        conversations=ordered_conversations,
        repeat_users=repeat_users,
        failure_events=failure_events,
        repeat_user_min=repeat_user_min,
    )


# ---------------------------------------------------------------------------
# 跨窗 diff
# ---------------------------------------------------------------------------


def _snapshot_family_counts(snapshot: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, row in (snapshot.get("families") or {}).items():
        if isinstance(row, dict):
            out[str(key)] = int(row.get("events") or 0)
        else:
            out[str(key)] = int(row or 0)
    return out


def _snapshot_registry(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reg = snapshot.get("registry") or {}
    fams = reg.get("families") or {}
    return {str(k): dict(v) for k, v in fams.items() if isinstance(v, dict)}


def _snapshot_cids(snapshot: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in snapshot.get("conversations") or []:
        if isinstance(row, dict):
            cid = row.get("conversation_id")
            if cid:
                out.append(str(cid))
    return out


def diff_snapshots(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """两份快照的跨窗 diff——巡检纪要里的核验判定从此可复算。

    每个家族给一个 ``status``：

    ``stable``
        两窗都有、口径 digest 一致 → ``delta`` 可信。
    ``new`` / ``retired``
        家族在上窗还不存在 / 已从表里退役 → 数量**不可比**。这正是「上窗是 0」与
        「上窗还没这个家族」的区别，靠 ``prev is None`` 区分。
    ``redefined``
        两窗都有但 digest 变了（改了 pattern / 事件名 / revision）→ ``comparable=false``，
        绝不把口径变化伪装成数量涨跌。

    改过名的家族靠 ``matched_via`` 接回旧序列（旧 key 记在 ``FailureFamily.aliases``）。
    """
    base_counts = _snapshot_family_counts(baseline)
    curr_counts = _snapshot_family_counts(current)
    base_reg = _snapshot_registry(baseline)
    curr_reg = _snapshot_registry(current)

    # 旧 key → 今天的 key（改过名的接回来）。
    base_by_today: dict[str, tuple[str, int]] = {}
    unresolved_base: list[str] = []
    for old_key in set(base_counts) | set(base_reg):
        today = UNKNOWN_FAMILY if old_key == UNKNOWN_FAMILY else resolve_family_key(old_key)
        if today is None:
            unresolved_base.append(old_key)
            continue
        prev_count = base_counts.get(old_key, 0)
        existing = base_by_today.get(today)
        if existing is None:
            base_by_today[today] = (old_key, prev_count)
        else:
            base_by_today[today] = (existing[0], existing[1] + prev_count)

    rows: list[dict[str, Any]] = []
    for key in sorted(set(curr_counts) | set(curr_reg) | set(base_by_today)):
        fam = FAMILIES_BY_KEY.get(key)
        # 家族在本窗表里但一次没命中 = 真实的 0；表里根本没有才是 None（不可比）。
        in_curr_registry = key in curr_reg or (key == UNKNOWN_FAMILY and key in curr_counts)
        curr = curr_counts.get(key, 0 if in_curr_registry else None)
        base_entry = base_by_today.get(key)
        prev = base_entry[1] if base_entry else None
        matched_via = base_entry[0] if base_entry and base_entry[0] != key else None
        prev_digest = (base_reg.get(base_entry[0]) or {}).get("digest") if base_entry else None

        if key == UNKNOWN_FAMILY:
            # 残差桶没有口径可言（它恰恰是「表没覆盖到」），数量只能当线索不能当序列。
            status = "residual"
            comparable = False
        elif base_entry is None:
            status = "new"
            comparable = False
        elif not in_curr_registry:
            status = "retired"
            comparable = False
        else:
            curr_digest = (curr_reg.get(key) or {}).get("digest") or (
                fam.digest() if fam else None
            )
            if prev_digest and curr_digest and prev_digest != curr_digest:
                status = "redefined"
                comparable = False
            else:
                status = "stable"
                comparable = True

        row: dict[str, Any] = {
            "key": key,
            "label": fam.label if fam else key,
            "status": status,
            "prev": prev,
            "curr": curr,
            "delta": curr - prev if comparable and prev is not None and curr is not None else None,
            "comparable": comparable,
        }
        if matched_via:
            row["matched_via"] = matched_via
        if status == "redefined":
            row["prev_digest"] = prev_digest
            row["curr_digest"] = (curr_reg.get(key) or {}).get("digest")
            if fam and fam.note:
                row["note"] = fam.note
        rows.append(row)

    for key in sorted(unresolved_base):
        rows.append(
            {
                "key": key,
                "label": key,
                "status": "unknown_key",
                "prev": base_counts.get(key, 0),
                "curr": None,
                "delta": None,
                "comparable": False,
                "note": (
                    "上窗快照里的 key 今天的表不认识（既非 key 也非 alias）——"
                    "表被改坏，或这份快照比表更旧。"
                ),
            }
        )

    base_cids = set(_snapshot_cids(baseline))
    curr_cids = set(_snapshot_cids(current))

    def _window(snapshot: dict[str, Any]) -> dict[str, Any]:
        win = dict(snapshot.get("window") or {})
        win["generated_at"] = snapshot.get("generated_at")
        win["registry_digest"] = (snapshot.get("registry") or {}).get("digest")
        return win

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "baseline": _window(baseline),
        "current": _window(current),
        "registry_changed": (baseline.get("registry") or {}).get("digest")
        != (current.get("registry") or {}).get("digest"),
        "families": rows,
        "conversations": {
            "carried_over": sorted(base_cids & curr_cids),
            "new": sorted(curr_cids - base_cids),
            "dropped": sorted(base_cids - curr_cids),
            "carried_over_n": len(base_cids & curr_cids),
            "new_n": len(curr_cids - base_cids),
            "dropped_n": len(base_cids - curr_cids),
        },
    }


def load_snapshot(path: Path) -> dict[str, Any]:
    """读一份快照 JSON，顺带校 schema 版本。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: 不是快照对象")
    version = data.get("schema_version")
    if version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"{path}: 快照 schema_version={version}，本版只认 {SNAPSHOT_SCHEMA_VERSION}"
        )
    return data


def write_snapshot(snapshot: PatrolSnapshot, path: Path) -> Path:
    """把快照落到 ``logs/`` 下（不入仓）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(snapshot.to_json_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def family_digest_map() -> dict[str, str]:
    """给 CLI 用的口径指纹表。"""
    return family_digests()
