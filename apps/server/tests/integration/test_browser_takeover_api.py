"""Owner-only auth + live-session semantics for POST …/browser/input.

Auto-skips (via the shared ``client`` fixture) when no PostgreSQL is reachable.
Guards: 401 unauth, 404 unknown / non-owner. Input is 409 when no live session
exists (test process has no gVisor sandbox). Injection on a live session is
unit-covered by driver / schema tests.
"""

import httpx

from tests.integration.conftest import register_and_login


async def _new_conversation(client: httpx.AsyncClient, title: str) -> str:
    r = await client.post("/v1/conversations", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_input_requires_auth(client):
    cid = "00000000-0000-0000-0000-000000000000"
    assert (
        await client.post(f"/v1/conversations/{cid}/browser/input", json={"events": []})
    ).status_code == 401


async def test_input_unknown_conversation_is_404(client):
    await register_and_login(client, "btuser1")
    cid = "11111111-1111-1111-1111-111111111111"
    assert (
        await client.post(f"/v1/conversations/{cid}/browser/input", json={"events": []})
    ).status_code == 404


async def test_input_non_owner_is_404(client, new_client):
    await register_and_login(client, "btowner")
    conv = await _new_conversation(client, "mine")

    async with new_client() as other:
        await register_and_login(other, "btintruder")
        assert (
            await other.post(
                f"/v1/conversations/{conv}/browser/input", json={"events": []}
            )
        ).status_code == 404


async def test_input_conflict_when_no_live_session(client):
    await register_and_login(client, "btuser5")
    conv = await _new_conversation(client, "no-session")
    r = await client.post(f"/v1/conversations/{conv}/browser/input", json={"events": []})
    assert r.status_code == 409, r.text
