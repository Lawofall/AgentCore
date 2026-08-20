"""Conversation timeline — merges DB messages + runtime log events into one view.

Thin CLI over ``agentcore.observability.query``. Run from apps/server:

    uv run python scripts/log_timeline.py <conversation_id>
    uv run python scripts/log_timeline.py --recent
    uv run python scripts/log_timeline.py --trace <trace_id>
    uv run python scripts/log_timeline.py --json --trace <trace_id>
    uv run python scripts/log_timeline.py --raw --trace <trace_id>
    uv run python scripts/log_timeline.py --since 24h --trace <trace_id>
    uv run python scripts/log_timeline.py --export-dir ../../logs/prod-export --recent
    uv run python scripts/log_timeline.py --pack <dir> --trace <trace_id>
    uv run python scripts/log_timeline.py --pack <dir> --full --trace <trace_id>

Default output is ``decision_spine`` (human + ``--json`` isomorphic). Pass
``--raw`` for the full ``log_events`` firehose. ``--pack`` writes an investigation
pack (decision_spine.json + timeline.jsonl + meta.json; optional previews /
turn_metrics; redacted journal when the store has rows; ``--full`` adds
messages.json without LLM bodies — never raw turn_journal). Exact-ID queries
always include synthetic ``traffic=eval|test`` lines. Message bodies live in
Postgres; turn traces live in logs/dev.jsonl (+ rotation backups) or
``--export-dir`` (``events.jsonl`` + joinable ``turn_metrics.jsonl`` /
``cost_events.jsonl``; journal is redacted by default after ``pnpm sync:logs``).
See .cursor/rules/conversation-logs.mdc.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_BARE_HEX32_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_EMPTY_HIT_SYNC_HINT = (
    "本地无命中且像线上 → 先 `pnpm sync:logs`，再加 "
    "`--export-dir ../../logs/prod-export`。\n"
    "ID 形态：无连字符 32-hex = trace_id；带连字符 UUID = conversation_id。"
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
LOG_FILE = _REPO_ROOT / "logs" / "dev.jsonl"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentcore.core.logging import ROLLOVER_FAILED_EVENT  # noqa: E402
from agentcore.observability.query.decision_spine import (  # noqa: E402
    format_decision_spine,
)
from agentcore.observability.query.jsonl import discover_log_files  # noqa: E402
from agentcore.observability.query.pack import write_investigation_pack  # noqa: E402
from agentcore.observability.query.store import open_conversation_store  # noqa: E402
from agentcore.observability.query.timeline import (  # noqa: E402
    extract_conversation_id,
    load_conversation_spine_events,
    query_conversation_timeline,
    query_recent,
    query_trace,
)
from agentcore.observability.query.timeline import (
    load_log_events as _query_load_log_events,
)
from agentcore.observability.query.timeutil import (  # noqa: E402
    parse_since,
    parse_timestamp,
)

# Re-exports for ad-hoc scripts / older imports
_discover_log_files = discover_log_files

# Consecutive jsonl timestamps beyond this → likely rotation/handle drop, not idle LLM.
_JSONL_GAP_HINT_SECONDS = 120.0
_JOURNAL_SOURCE_HINT = (
    "⚠ jsonl 时间线疑似断档；以 Postgres journal 为准 "
    "(Windows 日志轮转失败时 structlog 可能静默丢段，journal 更完整)。"
)


def detect_jsonl_timeline_gap(
    log_events: list[dict],
    *,
    min_gap_seconds: float = _JSONL_GAP_HINT_SECONDS,
) -> dict[str, Any] | None:
    """Detect a suspicious hole in jsonl timestamps (or an explicit rollover alert).

    Returns a small dict describing the gap / rollover signal, or ``None``.
    Used by the CLI to nudge readers toward Postgres journal when firehose
    truncation is likely.
    """
    for ev in log_events:
        if ev.get("event") == ROLLOVER_FAILED_EVENT:
            return {
                "reason": "rollover_failed",
                "event": ROLLOVER_FAILED_EVENT,
                "timestamp": ev.get("timestamp"),
            }

    stamped: list[tuple[datetime, dict[str, Any]]] = []
    for ev in log_events:
        raw = ev.get("timestamp")
        if not raw:
            continue
        dt = parse_timestamp(raw)
        if dt is None:
            continue
        stamped.append((dt, ev))
    stamped.sort(key=lambda pair: pair[0])
    for i in range(1, len(stamped)):
        delta = (stamped[i][0] - stamped[i - 1][0]).total_seconds()
        if delta >= min_gap_seconds:
            return {
                "reason": "timestamp_gap",
                "gap_seconds": delta,
                "before_ts": stamped[i - 1][1].get("timestamp"),
                "after_ts": stamped[i][1].get("timestamp"),
                "before_event": stamped[i - 1][1].get("event"),
                "after_event": stamped[i][1].get("event"),
            }
    return None


def _format_jsonl_gap_hint(log_events: list[dict]) -> str | None:
    gap = detect_jsonl_timeline_gap(log_events)
    if gap is None:
        return None
    if gap["reason"] == "rollover_failed":
        return _JOURNAL_SOURCE_HINT
    secs = int(gap.get("gap_seconds") or 0)
    return f"{_JOURNAL_SOURCE_HINT}  gap≈{secs}s"


def is_bare_trace_id(value: str) -> bool:
    """True when *value* is a no-hyphen 32-hex string (trace_id shape)."""
    return bool(_BARE_HEX32_RE.fullmatch(value))


def normalize_trace_id_arg(value: str) -> str:
    """CLI-only: hyphenated UUID → bare 32-hex when the hex payload is 32 chars."""
    if "-" not in value:
        return value
    stripped = value.replace("-", "")
    if _BARE_HEX32_RE.fullmatch(stripped):
        return stripped
    return value


def format_empty_hit_hint(*, using_export_dir: bool) -> str:
    """Guidance appended on empty hits when not already querying an export dir."""
    if using_export_dir:
        return ""
    return _EMPTY_HIT_SYNC_HINT


def _parse_cli_args(
    argv: list[str],
) -> tuple[
    Path,
    Path | None,
    datetime | None,
    bool,
    bool,
    Path | None,
    bool,
    list[str],
]:
    log_file = LOG_FILE
    export_dir: Path | None = None
    since: datetime | None = None
    as_json = False
    raw = False
    pack_dir: Path | None = None
    full = False
    positional: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--file" and i + 1 < len(argv):
            log_file = Path(argv[i + 1])
            i += 2
        elif arg == "--export-dir" and i + 1 < len(argv):
            export_dir = Path(argv[i + 1])
            i += 2
        elif arg == "--pack" and i + 1 < len(argv):
            pack_dir = Path(argv[i + 1])
            i += 2
        elif arg == "--since" and i + 1 < len(argv):
            try:
                since = parse_since(argv[i + 1])
            except ValueError as e:
                raise SystemExit(str(e)) from e
            i += 2
        elif arg == "--json":
            as_json = True
            i += 1
        elif arg == "--raw":
            raw = True
            i += 1
        elif arg == "--full":
            full = True
            i += 1
        else:
            positional.append(arg)
            i += 1
    return log_file, export_dir, since, as_json, raw, pack_dir, full, positional


def _fmt_log_line(item: dict, indent: str = "  ", hide: tuple[str, ...] = ()) -> str:
    ts = item.get("timestamp", "")[:19]
    event = item.get("event", "?")
    icon = {"error": "[E]", "warning": "[W]"}.get(item.get("level", ""), "   ")
    skip = ("type", "timestamp", "event", "level", *hide)
    detail_keys = {k: v for k, v in item.items() if k not in skip}
    detail = " ".join(f"{k}={v}" for k, v in detail_keys.items())
    if len(detail) > 120:
        detail = detail[:120] + "..."
    return f"{indent}{ts}  {icon} {event}  {detail}"


def _format_delegate_plan_dag(item: dict, indent: str = "      ") -> list[str]:
    plan = item.get("plan")
    waves = item.get("waves")
    if not plan or not waves:
        return []

    id_to_role: dict[str, str] = {}
    id_to_deps: dict[str, list[str]] = {}
    for node in plan:
        nid = node.get("id", "?")
        id_to_role[nid] = node.get("role") or nid
        id_to_deps[nid] = node.get("depends_on") or []

    lines: list[str] = []
    for wave_idx, wave in enumerate(waves):
        if wave_idx == 0:
            label = "Wave 0 (独立):"
        elif wave_idx == 1:
            label = "Wave 1 (依赖 Wave 0):"
        else:
            label = f"Wave {wave_idx}:"
        lines.append(f"{indent}├── {label}")
        for node_id in wave:
            role = id_to_role.get(node_id, node_id)
            deps = id_to_deps.get(node_id, [])
            if deps:
                dep_roles = ", ".join(id_to_role.get(d, d) for d in deps)
                lines.append(f"{indent}│     {role} ({node_id}) ← {dep_roles}")
            else:
                lines.append(f"{indent}│     {role} ({node_id})")
    return lines


def _fmt_log_item(item: dict, indent: str = "  ", hide: tuple[str, ...] = ()) -> list[str]:
    dag_hide: tuple[str, ...] = ()
    if item.get("event") == "delegate.started" and item.get("plan") and item.get("waves"):
        dag_hide = ("plan", "waves")
    lines = [_fmt_log_line(item, indent=indent, hide=(*hide, *dag_hide))]
    if dag_hide:
        lines.extend(_format_delegate_plan_dag(item, indent=indent + "    "))
    return lines


_WORKER_EVENT_PREFIXES = ("react.", "tool.")
_WORKER_REDUNDANT_KEYS = ("agent_id", "run_id", "depth", "trace_id")


def _worker_key(item: dict) -> tuple[str, str] | None:
    if item.get("type") != "log":
        return None
    if not item.get("event", "").startswith(_WORKER_EVENT_PREFIXES):
        return None
    depth = item.get("depth")
    if not depth:
        return None
    return (item.get("trace_id", ""), item.get("run_id") or item.get("agent_id") or "?")


def _partition_worker_groups(log_events: list[dict]) -> list[dict]:
    spine: list[dict] = []
    groups: dict[tuple[str, str], dict] = {}
    for ev in log_events:
        key = _worker_key(ev)
        if key is None:
            spine.append(ev)
            continue
        grp = groups.get(key)
        if grp is None:
            grp = {
                "type": "worker_group",
                "agent_id": ev.get("agent_id") or ev.get("run_id") or "?",
                "depth": ev.get("depth") or 1,
                "timestamp": ev.get("timestamp", ""),
                "events": [],
            }
            groups[key] = grp
        grp["events"].append(ev)
        ts = ev.get("timestamp", "")
        if ts and (not grp["timestamp"] or ts < grp["timestamp"]):
            grp["timestamp"] = ts
    for grp in groups.values():
        grp["events"].sort(key=lambda x: x.get("timestamp", ""))
    return spine + list(groups.values())


def _fmt_worker_group(grp: dict) -> list[str]:
    depth = grp.get("depth", 1)
    agent = grp.get("agent_id", "?")
    rounds = sum(1 for e in grp["events"] if e.get("event") == "react.round_end")
    tools = sum(1 for e in grp["events"] if e.get("event") == "tool.execute_end")
    meta = [f"d{depth}"]
    if rounds:
        meta.append(f"{rounds} round{'s' if rounds != 1 else ''}")
    if tools:
        meta.append(f"{tools} tool{'s' if tools != 1 else ''}")
    pad = "  " + "    " * depth
    child_indent = pad + "│  "
    lines = [f"{pad}┌─ worker {agent}  ({' · '.join(meta)})"]
    for ev in grp["events"]:
        lines.extend(_fmt_log_item(ev, indent=child_indent, hide=_WORKER_REDUNDANT_KEYS))
    return lines


def _summarize_turn_preview(preview: str, max_len: int = 60) -> str:
    text = (preview or "").replace("\n", " ").strip()
    if not text:
        return "(no preview)"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def format_conversation_context(
    conversation_id: str, spine_events: list[dict], current_trace_id: str
) -> str:
    by_trace: dict[str, dict[str, Any]] = {}
    for ev in spine_events:
        tid = ev.get("trace_id") or ""
        if not tid:
            continue
        slot = by_trace.setdefault(tid, {"trace_id": tid})
        if ev["event"] == "chat.turn_start":
            slot["start"] = ev
        elif ev["event"] == "chat.turn_complete":
            slot["complete"] = ev

    turns = sorted(
        by_trace.values(),
        key=lambda t: (t.get("start") or t.get("complete") or {}).get("timestamp", ""),
    )
    if not turns:
        return ""

    lines = [
        "",
        "─" * 70,
        f"  对话上下文  (conversation_id: {conversation_id})",
        f"  回合: {len(turns)}",
        "─" * 70,
    ]
    for turn in turns:
        tid = turn["trace_id"]
        start = turn.get("start")
        complete = turn.get("complete")
        ts = ((start or complete) or {}).get("timestamp", "")[:19]
        preview = _summarize_turn_preview((start or {}).get("preview", ""))
        is_current = tid == current_trace_id
        marker = ">>> " if is_current else "    "
        current_tag = " [当前]" if is_current else ""

        if complete:
            status = "✓"
            extras: list[str] = []
            if complete.get("delegated"):
                extras.append("委派")
            status_suffix = f"  {' · '.join(extras)}" if extras else ""
        elif start:
            status = "⚠️ 未完成（进行中或仅 kickoff）"
            status_suffix = ""
        else:
            status = "?"
            status_suffix = ""

        lines.append(f'{marker}{ts}{current_tag}  "{preview}"  {status}{status_suffix}')
        if is_current:
            lines.append(f"    trace_id: {tid}")
    lines.append("")
    return "\n".join(lines)


def format_trace(
    trace_id: str,
    log_events: list[dict],
    log_file: Path = LOG_FILE,
    since: datetime | None = None,
    traffic: str | None = None,
    *,
    using_export_dir: bool = False,
) -> str:
    lines = [
        "=" * 70,
        f"  Trace: {trace_id}",
        f"  Log events: {len(log_events)}",
    ]
    if traffic:
        lines.append(f"  Traffic: {traffic} (合成流量)")
    has_start = any(ev.get("event") == "chat.turn_start" for ev in log_events)
    has_complete = any(ev.get("event") == "chat.turn_complete" for ev in log_events)
    if has_start and not has_complete:
        lines.append("  Status: ⚠️ 未完成（进行中或仅 kickoff）")
    gap_hint = _format_jsonl_gap_hint(log_events)
    if gap_hint:
        lines.append(f"  {gap_hint}")
    lines.append("=" * 70)
    items = _partition_worker_groups(log_events)
    for item in sorted(items, key=lambda x: x.get("timestamp", "")):
        if item["type"] == "worker_group":
            lines += _fmt_worker_group(item)
        else:
            lines.extend(_fmt_log_item(item))
    lines.append("")
    output = "\n".join(lines)
    if not log_events:
        hint = format_empty_hit_hint(using_export_dir=using_export_dir)
        if hint:
            output += hint + "\n"
    conv_id = extract_conversation_id(log_events)
    if conv_id:
        spine = load_conversation_spine_events(
            conv_id,
            log_file=log_file,
            since=since,
        )
        output += format_conversation_context(conv_id, spine, trace_id)
    return output


def format_timeline(
    conv: dict,
    messages: list[dict],
    log_events: list[dict],
    traffic: str | None = None,
) -> str:
    lines = [
        "=" * 70,
        f"  Conversation: {conv.get('title', '(untitled)')}",
        f"  ID: {conv['id']}",
        f"  Agent: {conv.get('agent_id', '?')}  |  Created: {conv.get('created_at', '?')}",
        f"  Messages: {len(messages)}  |  Log events: {len(log_events)}",
    ]
    if traffic:
        lines.append(f"  Traffic: {traffic} (合成流量)")
    gap_hint = _format_jsonl_gap_hint(log_events)
    if gap_hint:
        lines.append(f"  {gap_hint}")
    lines.append("=" * 70)
    items = messages + _partition_worker_groups(log_events)
    for item in sorted(items, key=lambda x: x.get("timestamp", "")):
        if item["type"] == "worker_group":
            lines += _fmt_worker_group(item)
        elif item["type"] == "message":
            role = item["role"]
            icon = {"user": "[user]", "assistant": "[asst]", "system": "[sys ]"}.get(role, "[?]")
            preview = item["content_preview"].replace("\n", " ")
            line = f"  {item['timestamp'][:19]}  {icon} {preview}"
            if item["content_len"] > 200:
                line += f"... ({item['content_len']} chars)"
            extras = []
            if item["tool_calls_count"]:
                extras.append(f"tools:{item['tool_calls_count']}")
            if item["runs_count"]:
                extras.append(f"runs:{item['runs_count']}")
            if item["finish_reason"]:
                extras.append(f"finish:{item['finish_reason']}")
            if item.get("trace_id"):
                extras.append(f"trace:{item['trace_id']}")
            if extras:
                line += f"  [{', '.join(extras)}]"
            lines.append(line)
        else:
            lines.extend(_fmt_log_item(item))
    lines.append("")
    return "\n".join(lines)


def format_decision_spines_for_conversation(
    conv: dict,
    messages: list[dict],
    spines: list[dict],
    traffic: str | None = None,
) -> str:
    """Human default for conversation mode: message previews + decision spines."""
    lines = [
        "=" * 70,
        f"  Conversation: {conv.get('title', '(untitled)')}",
        f"  ID: {conv['id']}",
        f"  Agent: {conv.get('agent_id', '?')}  |  Created: {conv.get('created_at', '?')}",
        f"  Messages: {len(messages)}  |  Decision spines: {len(spines)}",
    ]
    if traffic:
        lines.append(f"  Traffic: {traffic} (合成流量)")
    lines.append("=" * 70)
    for item in sorted(messages, key=lambda x: x.get("timestamp", "")):
        role = item.get("role")
        icon = {"user": "[user]", "assistant": "[asst]", "system": "[sys ]"}.get(
            role, "[?]"
        )
        preview = (item.get("content_preview") or "").replace("\n", " ")
        line = f"  {str(item.get('timestamp', ''))[:19]}  {icon} {preview}"
        if (item.get("content_len") or 0) > 200:
            line += f"... ({item['content_len']} chars)"
        extras = []
        if item.get("finish_reason"):
            extras.append(f"finish:{item['finish_reason']}")
        if item.get("trace_id"):
            extras.append(f"trace:{item['trace_id']}")
        if extras:
            line += f"  [{', '.join(extras)}]"
        lines.append(line)
    lines.append("")
    for spine in spines:
        lines.append(format_decision_spine(spine).rstrip())
        lines.append("")
    return "\n".join(lines)


async def main() -> None:
    log_file, export_dir, since, as_json, raw, pack_dir, full, args = _parse_cli_args(
        sys.argv[1:]
    )
    if export_dir:
        log_file = export_dir / "events.jsonl"

    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return

    if full and pack_dir is None:
        raise SystemExit("--full 仅用于排查包：请同时传 --pack <dir>")

    store = open_conversation_store(export_dir=export_dir)

    try:
        if args[0] == "--recent":
            if pack_dir is not None:
                raise SystemExit("--pack 需要 --trace <trace_id>（或裸 32-hex）")
            n = int(args[1]) if len(args) > 1 else 5
            result = await query_recent(n, store=store)
            if as_json:
                print(json.dumps(result.to_json_dict(raw=raw), ensure_ascii=False, default=str))
                return
            print(f"\n  Recent {len(result.recent)} conversations:\n")
            for r in result.recent:
                print(
                    f"  {str(r.get('created_at', ''))[:19]}  {r.get('id')}  "
                    f"{r.get('title') or '(untitled)'}"
                )
            print()
            return

        if args[0] == "--trace" or is_bare_trace_id(args[0]):
            if args[0] == "--trace":
                if len(args) < 2:
                    print("usage: log_timeline.py --trace <trace_id>")
                    return
                raw_trace = args[1]
            else:
                raw_trace = args[0]
                if not as_json and pack_dir is None:
                    print(f"已按 trace_id 解释（无连字符 32-hex）: {raw_trace}")
            trace_id = normalize_trace_id_arg(raw_trace)
            # Pre-load events for gap detection before query builds the spine.
            pre_events, _ = _query_load_log_events(
                trace_id, field="trace_id", log_file=log_file, since=since
            )
            gap = detect_jsonl_timeline_gap(pre_events)
            result = await query_trace(
                trace_id,
                log_file=log_file,
                since=since,
                store=store,
                jsonl_gap=gap,
            )
            if pack_dir is not None:
                meta = await write_investigation_pack(
                    result,
                    out_dir=pack_dir,
                    store=store,
                    full=full,
                    log_file=log_file,
                    export_dir=export_dir,
                )
                print(f"Investigation pack → {pack_dir.resolve()}")
                print(f"  schema_version: {meta.get('schema_version')}")
                print(f"  files: {', '.join(meta.get('files') or [])}")
                return
            if as_json:
                print(json.dumps(result.to_json_dict(raw=raw), ensure_ascii=False, default=str))
                return
            if raw:
                print(
                    format_trace(
                        trace_id,
                        result.log_events,
                        log_file=log_file,
                        since=since,
                        traffic=result.meta.get("traffic"),
                        using_export_dir=export_dir is not None,
                    )
                )
                return
            assert result.decision_spine is not None
            if not result.log_events:
                hint = format_empty_hit_hint(using_export_dir=export_dir is not None)
                # Still useful when export/DB has turn_metrics but jsonl missed the trace.
                joined = (result.decision_spine.get("health") or {}).get(
                    "turn_metrics_joined"
                )
                if joined:
                    print(format_decision_spine(result.decision_spine))
                    if hint:
                        print(hint)
                    return
                print(f"Trace: {trace_id}\n  Log events: 0")
                if hint:
                    print(hint)
                return
            print(format_decision_spine(result.decision_spine))
            if result.meta.get("conversation_id"):
                print(
                    format_conversation_context(
                        result.meta["conversation_id"],
                        result.spine_events,
                        trace_id,
                    )
                )
            return

        if pack_dir is not None:
            raise SystemExit("--pack 需要 --trace <trace_id>（或裸 32-hex），不支持会话模式")

        conv_id = args[0]
        result = await query_conversation_timeline(
            conv_id,
            store=store,
            log_file=log_file,
            since=since,
        )
        if as_json:
            print(json.dumps(result.to_json_dict(raw=raw), ensure_ascii=False, default=str))
            return
        if result.meta.get("error") == "conversation_not_found":
            where = "export" if export_dir else "database"
            print(f"Conversation '{conv_id}' not found in {where}.")
            hint = format_empty_hit_hint(using_export_dir=export_dir is not None)
            if hint:
                print(hint)
            return
        assert result.conversation is not None
        if raw:
            print(
                format_timeline(
                    result.conversation,
                    result.messages,
                    result.log_events,
                    traffic=result.meta.get("traffic"),
                )
            )
            return
        print(
            format_decision_spines_for_conversation(
                result.conversation,
                result.messages,
                result.decision_spines,
                traffic=result.meta.get("traffic"),
            )
        )
    finally:
        await store.aclose()


def load_log_events(
    value: str,
    field: str = "conversation_id",
    log_file: Path = LOG_FILE,
    since: datetime | None = None,
) -> list[dict]:
    """Compat shim: returns projected events only (matches pre-P0 signature)."""
    events, _stats = _query_load_log_events(
        value,
        field,
        log_file=log_file,
        since=since,
    )
    return events


if __name__ == "__main__":
    asyncio.run(main())
