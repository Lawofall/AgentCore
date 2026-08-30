"""辩论进宿主图（批 A2）：开工决议机制解析幕 1 MLR 宿主并声明下一幕。

机制携带、工具无感——``debate`` 不长 CEO 可见追加参数。任一环找不到 → 回落独立图，
禁止硬凑、禁止静默错挂。

宿主判据（机械可判，全部满足才进图）：

1. 调研推荐链证据——复用 ``research_first`` 判据源（journal 命题卡 /
   工作区 ``research/`` 产物），**或**对话内可定位到含汇总员的宿主（跨回合 journal 不在
   当前 snapshot 时，定位成功本身即调研成功证据）。
2. 最近一张含汇总员（``synthesizer``）的 ``multi_agent`` execution。
3. 宿主助手 ``message_id`` 可解析。
4. 汇总员 ``run_completed`` 成功。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agentcore.core.logging import get_logger
from agentcore.runtime.kickoff.research_first import has_research_chain_evidence

logger = get_logger(__name__)

_SYNTHESIZER_ID = "synthesizer"
# DAG 铸造：playbook raw id ``synthesizer`` → ``{del_<uuid>|add_<uuid>}_synthesizer``。
_SYNTHESIZER_SUFFIX = "_synthesizer"


def is_mlr_synthesizer_id(run_id: str | None, agent_id: str | None = None) -> bool:
    """True when id/agent_id is the MLR synthesizer (raw or DAG-namespaced)."""
    for raw in (run_id, agent_id):
        text = (raw or "").strip()
        if text == _SYNTHESIZER_ID or text.endswith(_SYNTHESIZER_SUFFIX):
            return True
    return False


@dataclass(frozen=True, slots=True)
class DebateHostAttach:
    """Resolved MLR host for a debate act.

    ``same_turn``: host is this assistant message → grow that graph (new act, no prev).
    Otherwise: new graph chained with ``prev_execution_id``.
    """

    execution_id: str
    host_message_id: str
    anchor_run_id: str
    act_id: str
    same_turn: bool


def host_graph_binding(
    attach: DebateHostAttach, *, mint_id: Callable[[], str]
) -> tuple[str, str | None]:
    """Return ``(execution_id, prev_execution_id)`` for a resolved host.

    Same-turn: reuse the host graph and add a debate act (no prev). Cross-turn:
    mint a new graph chained with ``prev_execution_id``. ``mint_id`` is called
    only on the cross-turn path.
    """
    if attach.same_turn:
        return attach.execution_id, None
    return mint_id(), attach.execution_id


def research_chain_evidence(
    entries: Sequence[Mapping[str, Any]] | None,
    *,
    has_research_artifacts: bool = False,
) -> bool:
    """True when 已有调研链（命题卡 / MLR 成功 / 约定文档产物）。"""
    return has_research_chain_evidence(
        entries, has_research_artifacts=has_research_artifacts
    )


def next_act_id(entries: Sequence[Mapping[str, Any]] | None) -> str:
    """Host journal 已有幕序号的下一幕（缺省从 act-1 起算 → act-2）。"""
    max_n = 1
    if not entries:
        return "act-2"
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind != "run_plan":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        act = payload.get("act")
        if not isinstance(act, Mapping):
            continue
        raw = str(act.get("act_id") or "").strip()
        if not raw.startswith("act-"):
            continue
        try:
            max_n = max(max_n, int(raw[4:]))
        except ValueError:
            continue
    return f"act-{max_n + 1}"


def synthesizer_run_id(entries: Sequence[Mapping[str, Any]] | None) -> str | None:
    """Locate the MLR synthesizer run id from host ``run_plan`` facts (last wins).

    Matches raw ``synthesizer`` **and** DAG-namespaced ``{prefix}_synthesizer``
    (builder namespaces declared ids as ``{del_<uuid>}_{raw}``).
    """
    if not entries:
        return None
    found: str | None = None
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        if kind != "run_plan":
            continue
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        if str(payload.get("plan_type") or "") != "multi_agent":
            continue
        for run in payload.get("runs") or []:
            if not isinstance(run, Mapping):
                continue
            rid = str(run.get("id") or "").strip()
            aid = str(run.get("agent_id") or "").strip()
            if is_mlr_synthesizer_id(rid, aid):
                found = rid or aid
    return found


def synthesizer_completed(
    entries: Sequence[Mapping[str, Any]] | None, run_id: str
) -> bool:
    """True when ``run_id`` has a successful ``run_completed`` (not failed)."""
    if not entries or not run_id:
        return False
    ok = False
    for entry in entries:
        kind = str(entry.get("kind") or entry.get("type") or "")
        payload = entry.get("payload")
        if not isinstance(payload, Mapping):
            continue
        rid = str(payload.get("run_id") or "").strip()
        if rid != run_id:
            continue
        if kind == "run_failed":
            return False
        if kind == "run_completed":
            ok = True
    return ok


async def resolve_debate_host_attach(
    *,
    conversation_id: str,
    append_message_id: str | None,
    journal_entries: Sequence[Mapping[str, Any]] | None = None,
    has_research_artifacts: bool = False,
) -> DebateHostAttach | None:
    """Resolve MLR host for debate act growth, or ``None`` to keep an independent graph."""
    cid = (conversation_id or "").strip()
    if not cid:
        return None

    from agentcore.runtime.delegate.graph_append import (
        load_host_journal_entries,
        resolve_host_message_id,
        resolve_latest_mlr_execution,
    )

    # Soft pre-gate: 当前回合明确「应先调研」且无约定文档时，不抢挂旧图（冷开辩 → 独立图）。
    # 跨回合：snapshot 空但 DB 能定位 MLR 时仍可进图（定位本身即链证据）。
    local_chain = research_chain_evidence(
        journal_entries, has_research_artifacts=has_research_artifacts
    )

    execution_id = await resolve_latest_mlr_execution(conversation_id=cid)
    if not execution_id:
        logger.warning("debate.host_attach_fallback", reason="no_mlr_execution")
        return None
    if not local_chain:
        # 无本地判据时，仍允许：对话内存在 MLR 宿主 = 跨回合调研链证据。
        logger.info(
            "debate.host_attach_chain_via_mlr",
            execution_id=execution_id,
        )

    host_message_id = await resolve_host_message_id(
        conversation_id=cid, execution_id=execution_id
    )
    if not host_message_id:
        logger.warning(
            "debate.host_attach_fallback",
            reason="host_message_unresolved",
            execution_id=execution_id,
        )
        return None

    entries = await load_host_journal_entries(host_message_id)
    anchor = synthesizer_run_id(entries)
    if not anchor:
        logger.warning(
            "debate.host_attach_fallback",
            reason="no_synthesizer",
            execution_id=execution_id,
        )
        return None
    if not synthesizer_completed(entries, anchor):
        logger.warning(
            "debate.host_attach_fallback",
            reason="synthesizer_incomplete",
            execution_id=execution_id,
            anchor_run_id=anchor,
        )
        return None

    act_id = next_act_id(entries)
    same_turn = bool(
        append_message_id and append_message_id.strip() == host_message_id.strip()
    )
    attach = DebateHostAttach(
        execution_id=execution_id,
        host_message_id=host_message_id,
        anchor_run_id=anchor,
        act_id=act_id,
        same_turn=same_turn,
    )
    logger.info(
        "debate.host_attach",
        execution_id=attach.execution_id,
        host_message_id=attach.host_message_id,
        anchor_run_id=attach.anchor_run_id,
        act_id=attach.act_id,
        same_turn=attach.same_turn,
    )
    return attach
