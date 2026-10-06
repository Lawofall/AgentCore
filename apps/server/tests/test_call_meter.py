"""In-process call metering: proxy scenario must not enqueue a second ledger row."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentcore.billing import cost_ledger_queue as queue_mod
from agentcore.billing.call_meter import PROXY_LLM_SCENARIO, maybe_enqueue_inprocess_call
from agentcore.core.log_context import log_context
from agentcore.costing import PERSONA_REWRITE, ROLE_ASSIST
from agentcore.llm.provider.protocol import TokenUsage


class _AliveTask:
    def done(self) -> bool:
        return False


@pytest.fixture
def running_ledger(monkeypatch, tmp_path: Path):
    queue = queue_mod.reset_cost_ledger_queue_for_tests()
    monkeypatch.setattr(queue_mod.settings, "data_dir", str(tmp_path))
    queue._task = _AliveTask()
    return queue, tmp_path


def _usage() -> TokenUsage:
    return TokenUsage(input_tokens=10, output_tokens=5)


def _pending_rows(queue) -> list[dict]:
    return [r for r in queue._backend._rows.values() if r.get("status") == "pending"]


@pytest.mark.asyncio
async def test_maybe_enqueue_skips_proxy_scenario(running_ledger):
    """Proxy unary still logs llm.call, but billing is proxy_spend-only."""
    queue, _tmp_path = running_ledger
    with log_context(user_id="u1", conversation_id="c1"):
        assert (
            maybe_enqueue_inprocess_call(
                model="deepseek-v4-flash",
                usage=_usage(),
                scenario=PROXY_LLM_SCENARIO,
            )
            is None
        )
    await queue._await_pending_enqueues()
    assert _pending_rows(queue) == []


@pytest.mark.asyncio
async def test_maybe_enqueue_records_non_proxy_scenario(running_ledger):
    queue, _tmp_path = running_ledger
    with log_context(user_id="u1", conversation_id="c1"):
        rid = maybe_enqueue_inprocess_call(
            model="deepseek-v4-flash",
            usage=_usage(),
            scenario="chat",
        )
    assert rid is not None
    await queue._await_pending_enqueues()
    rows = _pending_rows(queue)
    assert len(rows) == 1
    assert rows[0]["source"] == "inprocess_call"
    assert rows[0]["materialize_runs"] is True


@pytest.mark.asyncio
async def test_maybe_enqueue_records_a_conversation_less_call(running_ledger):
    """An account-level call (AI 改写) belongs to no conversation
    and is now billed as such: the ledger takes ``conversation_id = NULL`` rather
    than dropping real spend or inventing a conversation to hang it on. Only
    ``user_id`` is required — that is what the account windows / 配额 SUM on."""
    queue, _tmp_path = running_ledger
    with log_context(user_id="u1", cost_role=ROLE_ASSIST, persona=PERSONA_REWRITE):
        rid = maybe_enqueue_inprocess_call(
            model="deepseek-v4-flash", usage=_usage(), scenario="file.rewrite"
        )
    assert rid is not None
    await queue._await_pending_enqueues()
    rows = _pending_rows(queue)
    assert len(rows) == 1
    assert rows[0]["user_id"] == "u1"
    assert rows[0]["conversation_id"] is None
    assert rows[0]["message_id"] is None
    call = rows[0]["calls"][0]
    assert call["role"] == ROLE_ASSIST
    assert call["persona"] == PERSONA_REWRITE


@pytest.mark.asyncio
async def test_maybe_enqueue_skips_call_without_an_account(running_ledger):
    """No bound ``user_id`` (evals / 设置·测连 probes) → nobody to charge, so no row.

    The owner key is the one part of the envelope the ledger cannot do without:
    every account window and quota SUM keys on it.
    """
    queue, _tmp_path = running_ledger
    with log_context(conversation_id="c1"):
        assert (
            maybe_enqueue_inprocess_call(
                model="deepseek-v4-flash", usage=_usage(), scenario="chat"
            )
            is None
        )
    await queue._await_pending_enqueues()
    assert _pending_rows(queue) == []


def test_no_unwired_second_billing_builder():
    """``background_run_cost`` was dead after ``call_meter`` took over: nothing
    called it, so its presence only suggested off-turn calls were already billed."""
    from agentcore.runtime import costing

    assert not hasattr(costing, "background_run_cost")
    assert "background_run_cost" not in costing.__all__
