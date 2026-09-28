"""Integration tests for the local→云 handoff dispatch + jobs API (双模式工作区 P2e / e2).

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Covers the parts of the surface that need no desktop / SSE: auth, the local-mode
gate on dispatch (422 for a cloud conversation — nothing local to hand off), the
empty job list for a fresh conversation, an unknown-job 404, and IDOR isolation.
The full dispatch happy path (which needs a bound desktop to fulfil the ARCHIVE
op) is covered at the unit level in ``test_handoff_job.py``.
"""

import httpx

from agentcore.core.types import new_id
from agentcore.db.models import Conversation, HandoffJob
from agentcore.db.repositories import ConversationRepository, HandoffJobRepository
from tests.integration.conftest import register_and_login


async def _new_conversation(client: httpx.AsyncClient, title: str) -> str:
    r = await client.post("/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_handoff_requires_auth(client):
    cid = "00000000-0000-0000-0000-000000000000"
    assert (
        await client.post(
            f"/v1/conversations/{cid}/workspace/handoff/dispatch",
            json={"task": "x"},
        )
    ).status_code == 401
    assert (await client.get(f"/v1/conversations/{cid}/handoff/jobs")).status_code == 401
    assert (
        await client.post(
            f"/v1/conversations/{cid}/handoff/jobs/{cid}/apply",
            json={"selections": []},
        )
    ).status_code == 401
    assert (
        await client.post(f"/v1/conversations/{cid}/handoff/jobs/{cid}/discard")
    ).status_code == 401


async def test_dispatch_rejects_cloud_conversation(client):
    await register_and_login(client, "hjuser1")
    conv = await _new_conversation(client, "cloud")  # cloud by default (no binding)

    # Nothing local to hand off → 422 before any SSE starts.
    r = await client.post(
        f"/v1/conversations/{conv}/workspace/handoff/dispatch",
        json={"task": "refactor"},
    )
    assert r.status_code == 422, r.text


async def test_jobs_list_empty_unknown_and_idor(client, new_client):
    await register_and_login(client, "hjowner")
    conv = await _new_conversation(client, "mine")

    # A fresh conversation has no jobs.
    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs")
    assert r.status_code == 200, r.text
    assert r.json() == {"data": [], "total": 0}

    # An unknown job id under the conversation is a 404.
    assert (await client.get(f"/v1/conversations/{conv}/handoff/jobs/{conv}")).status_code == 404

    # IDOR: a non-owner cannot list another user's conversation jobs.
    async with new_client() as other:
        await register_and_login(other, "hjintruder")
        assert (await other.get(f"/v1/conversations/{conv}/handoff/jobs")).status_code == 404


async def _seed_job(session_factory, *, user_id, source_conversation_id, succeeded=False):
    """Insert a HandoffJob (the e3 gates fire before any snapshot read).

    ``succeeded=True`` marks it done with a (dummy) result snapshot id, so the
    apply/diff status gate passes and the *next* gate (local mode) can be exercised.
    """
    host_id = new_id()
    async with session_factory() as session:
        session.add(
            Conversation(id=host_id, user_id=user_id, title="host", mode="handoff")
        )
        repo = HandoffJobRepository(session)
        job = await repo.create(
            user_id=user_id,
            source_conversation_id=source_conversation_id,
            job_conversation_id=host_id,
            base_snapshot_id="base-snap",
            task="refactor",
        )
        if succeeded:
            await repo.mark_succeeded(job.id, result_snapshot_id="result-snap")
        return job.id


async def test_diff_unknown_job_is_404(client):
    await register_and_login(client, "hjdiff404")
    conv = await _new_conversation(client, "mine")

    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs/{new_id()}/diff")
    assert r.status_code == 404, r.text


async def test_diff_unfinished_job_is_409(client, session_factory):
    user_id = await register_and_login(client, "hjdiff409")
    conv = await _new_conversation(client, "mine")
    job_id = await _seed_job(session_factory, user_id=user_id, source_conversation_id=conv)

    # The job is still pending (no result snapshot) → 409, before any snapshot read.
    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs/{job_id}/diff")
    assert r.status_code == 409, r.text


async def test_diff_idor_is_404(client, new_client, session_factory):
    user_id = await register_and_login(client, "hjdiffowner")
    conv = await _new_conversation(client, "mine")
    job_id = await _seed_job(session_factory, user_id=user_id, source_conversation_id=conv)

    async with new_client() as other:
        await register_and_login(other, "hjdiffintruder")
        r = await other.get(f"/v1/conversations/{conv}/handoff/jobs/{job_id}/diff")
        assert r.status_code == 404, r.text


async def test_apply_unknown_job_is_404(client):
    await register_and_login(client, "hjapply404")
    conv = await _new_conversation(client, "mine")

    r = await client.post(
        f"/v1/conversations/{conv}/handoff/jobs/{new_id()}/apply",
        json={"selections": []},
    )
    assert r.status_code == 404, r.text


async def test_apply_unfinished_job_is_409(client, session_factory):
    user_id = await register_and_login(client, "hjapply409")
    conv = await _new_conversation(client, "mine")
    job_id = await _seed_job(session_factory, user_id=user_id, source_conversation_id=conv)

    r = await client.post(
        f"/v1/conversations/{conv}/handoff/jobs/{job_id}/apply",
        json={"selections": []},
    )
    assert r.status_code == 409, r.text


async def test_apply_cloud_conversation_is_422(client, session_factory):
    user_id = await register_and_login(client, "hjapply422")
    conv = await _new_conversation(client, "cloud")  # no local binding
    # A *succeeded* job clears the status gate, so the local-mode gate is what trips:
    # there is nothing local to apply onto.
    job_id = await _seed_job(
        session_factory, user_id=user_id, source_conversation_id=conv, succeeded=True
    )

    r = await client.post(
        f"/v1/conversations/{conv}/handoff/jobs/{job_id}/apply",
        json={"selections": []},
    )
    assert r.status_code == 422, r.text


async def test_discard_succeeded_job_exposes_discarded_on_list_get(client, session_factory):
    """§7.6 discard: succeeded → discarded; list/get surface the new status."""
    user_id = await register_and_login(client, "hjdiscard1")
    conv = await _new_conversation(client, "mine")
    job_id = await _seed_job(
        session_factory, user_id=user_id, source_conversation_id=conv, succeeded=True
    )

    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "succeeded"

    r = await client.post(f"/v1/conversations/{conv}/handoff/jobs/{job_id}/discard")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "discarded"
    assert r.json()["id"] == job_id

    # Idempotent re-discard.
    r = await client.post(f"/v1/conversations/{conv}/handoff/jobs/{job_id}/discard")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "discarded"

    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs")
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1
    assert r.json()["data"][0]["status"] == "discarded"

    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs/{job_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "discarded"


async def test_discard_pending_is_409_and_applied_is_409(client, session_factory):
    user_id = await register_and_login(client, "hjdiscard409")
    conv = await _new_conversation(client, "mine")

    pending_id = await _seed_job(session_factory, user_id=user_id, source_conversation_id=conv)
    r = await client.post(f"/v1/conversations/{conv}/handoff/jobs/{pending_id}/discard")
    assert r.status_code == 409, r.text

    applied_id = await _seed_job(
        session_factory, user_id=user_id, source_conversation_id=conv, succeeded=True
    )
    async with session_factory() as session:
        assert await HandoffJobRepository(session).mark_applied(applied_id)

    r = await client.get(f"/v1/conversations/{conv}/handoff/jobs/{applied_id}")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "applied"

    r = await client.post(f"/v1/conversations/{conv}/handoff/jobs/{applied_id}/discard")
    assert r.status_code == 409, r.text


async def test_discard_unknown_and_idor(client, new_client, session_factory):
    user_id = await register_and_login(client, "hjdiscardowner")
    conv = await _new_conversation(client, "mine")
    job_id = await _seed_job(
        session_factory, user_id=user_id, source_conversation_id=conv, succeeded=True
    )

    assert (
        await client.post(f"/v1/conversations/{conv}/handoff/jobs/{new_id()}/discard")
    ).status_code == 404

    async with new_client() as other:
        await register_and_login(other, "hjdiscardintruder")
        r = await other.post(f"/v1/conversations/{conv}/handoff/jobs/{job_id}/discard")
        assert r.status_code == 404, r.text


async def test_hard_delete_either_conversation_removes_the_job(client, session_factory):
    user_id = await register_and_login(client, "hjfk")
    source = await _new_conversation(client, "source")
    job_id = await _seed_job(session_factory, user_id=user_id, source_conversation_id=source)
    async with session_factory() as session:
        host_id = (await session.get(HandoffJob, job_id)).job_conversation_id
        await ConversationRepository(session).hard_delete(host_id)
    async with session_factory() as session:
        assert await session.get(HandoffJob, job_id) is None
        assert await session.get(Conversation, source) is not None

    other = await _new_conversation(client, "other")
    other_job = await _seed_job(
        session_factory, user_id=user_id, source_conversation_id=other
    )
    async with session_factory() as session:
        host_id = (await session.get(HandoffJob, other_job)).job_conversation_id
        await ConversationRepository(session).hard_delete(other)
    async with session_factory() as session:
        assert await session.get(HandoffJob, other_job) is None
        assert await session.get(Conversation, host_id) is not None
