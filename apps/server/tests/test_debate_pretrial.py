"""庭前取证阶段单测：fast 秒过、Evidence Pack、无 pack 对称有界预算（无调查员舰队）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentcore.runtime.debate.evidence_ledger import (
    EvidenceLedger,
    preregister_turn_research_entries,
)
from agentcore.runtime.debate.evidence_pack import (
    assemble_evidence_pack_from_host,
    parse_attached_file_sources,
)
from agentcore.runtime.debate.pretrial import run_pretrial_phase
from agentcore.runtime.debate.types import (
    DebateConfig,
    DebateForm,
    DebateSide,
    RoundPolicy,
)


def _config(*, thorough: bool = True) -> DebateConfig:
    return DebateConfig(
        motion="是否采用方案 A",
        form=DebateForm.DEBATE,
        sides=[
            DebateSide(key="pro", name="支持方", stance="支持采用方案 A"),
            DebateSide(key="con", name="反对方", stance="反对采用方案 A"),
        ],
        policy=RoundPolicy(thorough=thorough, max_rounds=1 if not thorough else 5),
    )


_ATTACHED_TEXT_PROMPT = """
系统前缀…
<附件>
The user attached the following files as actionable inputs.

--- File: 合同.md (attachments/合同.md) ---
第一条 甲方应在签署后 30 日内支付首期款项。
第二条 争议提交仲裁委员会。
</附件>
"""

_ATTACHED_BINARY_ONLY_PROMPT = """
<附件>
--- File: report.xlsx (attachments/report.xlsx) [binary] ---
This is a binary file saved in the workspace (no text inline).
CEO has no code_execute — delegate a worker to open/parse it with code_execute
on the workspace-relative path above.
</附件>
"""


def test_preregister_turn_research_entries_maps_r_to_e():
    led = EvidenceLedger()
    eids = preregister_turn_research_entries(
        led,
        [
            {"id": "#r1", "url": "https://a.example", "title": "A"},
            {"id": "#r2", "url": "https://b.example", "title": "B"},
            {"id": "#e9", "url": "https://skip", "title": "skip"},  # ignore
        ],
    )
    assert eids == ["#e1", "#e2"]
    assert led.get("#e1")["origin_id"] == "#r1"
    # idempotent
    eids2 = preregister_turn_research_entries(
        led, [{"id": "#r1", "url": "https://a.example", "title": "A"}]
    )
    assert eids2 == ["#e1"]
    assert len(led) == 2


@pytest.mark.asyncio
async def test_pretrial_fast_skips():
    tool = MagicMock()
    tool._sink = MagicMock()
    tool._evidence_ledger = EvidenceLedger()
    tool._depth = 0

    started: list[dict] = []
    completed: list[dict] = []

    async def on_started(p: dict) -> None:
        started.append(p)

    async def on_completed(p: dict) -> None:
        completed.append(p)

    result = await run_pretrial_phase(
        tool,
        execution_id="e1",
        moderator_run_id="mod1",
        config=_config(thorough=False),
        complete_json=AsyncMock(return_value={}),
        on_started=on_started,
        on_completed=on_completed,
    )
    assert result.skipped is True
    assert result.skip_reason == "fast"
    assert result.incomplete is False
    assert started[0].get("skip_reason") == "fast"
    assert completed[0]["status"] == "skipped"
    assert completed[0]["incomplete"] is False


@pytest.mark.asyncio
async def test_pretrial_no_pack_skips_with_symmetric_bounded_budgets():
    """无可用 pack → 不派员；completeness=empty；各方对称有界发言期预算。"""
    from agentcore.runtime.debate.constants import BOUNDED_GAP_FILL_RETRIEVAL_BUDGET

    tool = MagicMock()
    tool._sink = MagicMock()
    tool._evidence_ledger = EvidenceLedger()
    tool._depth = 0
    tool._system_prompt = ""

    cfg = _config(thorough=True)
    result = await run_pretrial_phase(
        tool,
        execution_id="e1",
        moderator_run_id="mod1",
        config=cfg,
        complete_json=AsyncMock(return_value={"orders": {"pro": [], "con": []}}),
    )
    assert result.skipped is True
    assert result.skip_reason == "no_pack"
    assert result.completeness == "empty"
    assert result.incomplete is False  # intentional skip
    assert result.external_evidence_mode == "skip"
    assert result.external_evidence_reason == "no_pack"
    assert cfg.debater_retrieval_budgets == {
        "pro": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
        "con": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
    }
    assert BOUNDED_GAP_FILL_RETRIEVAL_BUDGET > 0
    assert "庭前取证·证据不完整" in (cfg.research_dossier_index or "")
    assert cfg.evidence_completeness == "empty"
    payload = result.to_completed_payload()
    assert payload["status"] == "skipped"


def test_parse_attached_file_sources_text_and_binary():
    text_srcs = parse_attached_file_sources(_ATTACHED_TEXT_PROMPT)
    assert len(text_srcs) == 1
    assert text_srcs[0].label == "合同.md"
    assert "第一条" in text_srcs[0].excerpt

    bin_srcs = parse_attached_file_sources(_ATTACHED_BINARY_ONLY_PROMPT)
    assert len(bin_srcs) == 1
    assert bin_srcs[0].failure == "binary_no_text"
    assert bin_srcs[0].excerpt == ""


def test_assemble_evidence_pack_from_host_full():
    pack = assemble_evidence_pack_from_host(
        system_prompt=_ATTACHED_TEXT_PROMPT,
        motion="是否采用方案 A",
        sides=_config().sides,
        background="背景补充一句。",
    )
    assert pack is not None
    assert pack.has_usable_body()
    assert pack.completeness in ("full", "partial")
    assert any(s.kind == "background" for s in pack.sources)
    assert len(pack.dispute_candidates) == 2
    wire = pack.to_wire()
    assert wire["sources"]
    assert wire["dispute_candidates"]


@pytest.mark.asyncio
async def test_pretrial_with_attachments_skips_fleet():
    """附件已在主持人上下文 → Evidence Pack 路径，不派员。"""
    tool = MagicMock()
    tool._sink = MagicMock()
    tool._evidence_ledger = EvidenceLedger()
    tool._depth = 0
    tool._system_prompt = _ATTACHED_TEXT_PROMPT

    complete = AsyncMock(
        return_value={
            "orders": {
                "pro": [{"query": "深挖合同"}],
                "con": [{"query": "深挖合同"}],
            }
        }
    )

    cfg = _config(thorough=True)
    result = await run_pretrial_phase(
        tool,
        execution_id="e1",
        moderator_run_id="mod1",
        config=cfg,
        complete_json=complete,
    )
    complete.assert_not_awaited()
    assert result.skipped is True
    assert result.skip_reason == "evidence_pack"
    assert result.evidence_ready is True
    assert result.evidence_pack is not None
    assert result.evidence_pack.has_usable_body()
    assert result.completeness == "full"
    assert result.incomplete is False
    assert result.external_evidence_mode == "skip"
    assert result.external_evidence_reason == "evidence_pack_full"
    assert cfg.pretrial_evidence_ready is True
    assert cfg.evidence_pack is not None
    assert cfg.evidence_completeness == "full"
    assert cfg.debater_retrieval_budgets == {"pro": 0, "con": 0}
    assert "共享证据包" in (cfg.research_dossier_index or "")
    assert "完整度=" in (cfg.research_dossier_index or "")
    assert len(tool._evidence_ledger) >= 1
    payload = result.to_completed_payload()
    assert payload["status"] == "skipped"
    assert payload["evidence_pack"]["sources"]
    assert payload["completeness"] == "full"
    assert payload["incomplete"] is False
    assert payload["external_evidence_mode"] == "skip"
    assert payload["external_evidence_reason"] == "evidence_pack_full"


def test_assemble_evidence_pack_truncated_is_partial():
    """截断附件 → pack.completeness=partial，索引带不完整标注。"""
    from agentcore.runtime.debate.evidence_pack import format_evidence_pack_index

    long_body = "条款正文。" * 400
    prompt = f"""
<附件>
--- File: 长约.md (attachments/长约.md) [truncated] ---
{long_body}
</附件>
"""
    pack = assemble_evidence_pack_from_host(
        system_prompt=prompt,
        motion="是否采用方案 A",
        sides=_config().sides,
    )
    assert pack is not None
    assert pack.completeness == "partial"
    assert any(s.failure == "truncated" for s in pack.sources)
    index = format_evidence_pack_index(pack)
    assert "完整度=partial" in index
    assert "证据不完整" in index


@pytest.mark.asyncio
async def test_pretrial_binary_attachments_no_fleet():
    """仅 binary 附件（无可用正文）→ 不走 pack，亦不派员（no_pack + 有界预算）。"""
    from agentcore.runtime.debate.constants import BOUNDED_GAP_FILL_RETRIEVAL_BUDGET

    tool = MagicMock()
    tool._sink = MagicMock()
    tool._evidence_ledger = EvidenceLedger()
    tool._depth = 0
    tool._system_prompt = _ATTACHED_BINARY_ONLY_PROMPT

    cfg = _config(thorough=True)
    result = await run_pretrial_phase(
        tool,
        execution_id="e1",
        moderator_run_id="mod1",
        config=cfg,
        complete_json=AsyncMock(return_value={"orders": {}}),
    )
    assert result.skip_reason == "no_pack"
    assert result.skipped is True
    assert result.completeness == "empty"
    assert cfg.debater_retrieval_budgets == {
        "pro": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
        "con": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
    }


def test_resolve_external_evidence_plan_always_skips():
    """完整度驱动：任意路径均 skip 外证舰队；发言期预算另算。"""
    from agentcore.runtime.debate.constants import BOUNDED_GAP_FILL_RETRIEVAL_BUDGET
    from agentcore.runtime.debate.evidence_pack import (
        debater_budgets_from_completeness,
        resolve_external_evidence_plan,
    )

    full = resolve_external_evidence_plan(
        completeness="full",
        path="evidence_pack",
    )
    assert full.mode == "skip"
    assert full.allow_external is False
    assert full.retrieval_budget == 0
    assert full.reason == "evidence_pack_full"

    partial = resolve_external_evidence_plan(
        completeness="partial",
        path="evidence_pack",
    )
    assert partial.mode == "skip"
    assert partial.allow_external is False
    assert partial.reason == "evidence_pack_partial"

    no_pack = resolve_external_evidence_plan(
        completeness="empty",
        path="no_pack",
    )
    assert no_pack.mode == "skip"
    assert no_pack.reason == "no_pack"

    budgets = debater_budgets_from_completeness(
        side_keys=["pro", "con"],
        completeness="partial",
    )
    assert budgets == {
        "pro": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
        "con": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
    }
    full_budgets = debater_budgets_from_completeness(
        side_keys=["pro", "con"],
        completeness="full",
    )
    assert full_budgets == {"pro": 0, "con": 0}


@pytest.mark.asyncio
async def test_pretrial_partial_pack_skips_fleet_with_bounded_budgets():
    """截断附件 → partial pack → 不派员；各方对称有界发言期预算。"""
    from agentcore.runtime.debate.constants import BOUNDED_GAP_FILL_RETRIEVAL_BUDGET

    long_body = "条款正文。" * 400
    prompt = f"""
<附件>
--- File: 长约.md (attachments/长约.md) [truncated] ---
{long_body}
</附件>
"""
    tool = MagicMock()
    tool._sink = MagicMock()
    tool._evidence_ledger = EvidenceLedger()
    tool._depth = 0
    tool._system_prompt = prompt

    complete = AsyncMock(return_value={"orders": {}})
    cfg = _config(thorough=True)
    result = await run_pretrial_phase(
        tool,
        execution_id="e1",
        moderator_run_id="mod1",
        config=cfg,
        complete_json=complete,
    )
    complete.assert_not_awaited()
    assert result.skipped is True
    assert result.skip_reason == "evidence_pack"
    assert result.external_evidence_mode == "skip"
    assert result.external_evidence_reason == "evidence_pack_partial"
    assert result.completeness == "partial"
    assert result.evidence_ready is True
    assert result.evidence_pack is not None
    assert cfg.debater_retrieval_budgets == {
        "pro": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
        "con": BOUNDED_GAP_FILL_RETRIEVAL_BUDGET,
    }
    assert BOUNDED_GAP_FILL_RETRIEVAL_BUDGET > 0
    assert "证据不完整" in (cfg.research_dossier_index or "")
