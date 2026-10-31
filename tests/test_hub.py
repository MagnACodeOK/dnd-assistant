from datetime import datetime

import pytest

from dnd_assistant.hub import make_app

TOKEN = "s3cret-token"
ORIGIN = "https://foundry.example"


@pytest.fixture
def received():
    return []


@pytest.fixture
async def client(aiohttp_client, received):
    def handle(e):
        received.append(e)
        return len(received)

    return await aiohttp_client(make_app(handle, TOKEN, [ORIGIN]))


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status == 200
    assert await resp.json() == {"ok": True}


async def test_post_requires_token(client, received):
    body = {"kind": "chat", "text": "hi"}
    assert (await client.post("/event", json=body)).status == 401
    assert (await client.post("/event", json=body, headers={"X-Token": "wrong"})).status == 401
    assert received == []


async def test_post_rejects_bad_kind(client, received):
    for kind in ("speech", "gap", "nonsense", None):
        resp = await client.post("/event", json={"kind": kind, "text": "x"}, headers={"X-Token": TOKEN})
        assert resp.status == 400
    assert received == []


async def test_post_rejects_non_json(client, received):
    resp = await client.post("/event", data="not json", headers={"X-Token": TOKEN})
    assert resp.status == 400
    resp = await client.post("/event", json=["a", "list"], headers={"X-Token": TOKEN})
    assert resp.status == 400
    resp = await client.post("/event", json={"kind": "chat", "text": "x", "data": "nope"}, headers={"X-Token": TOKEN})
    assert resp.status == 400
    assert received == []


async def test_post_accepted_and_ts_is_receive_time(client, received):
    before = datetime.now().astimezone()
    resp = await client.post(
        "/event",
        json={"kind": "roll", "speaker": "Paylon", "text": "", "data": {"total": 19}, "ts": "2020-01-01T00:00:00Z"},
        headers={"X-Token": TOKEN, "Origin": ORIGIN},
    )
    after = datetime.now().astimezone()
    assert resp.status == 202
    assert await resp.json() == {"id": 1}
    assert resp.headers["Access-Control-Allow-Origin"] == ORIGIN
    (e,) = received
    assert (e.source, e.kind, e.speaker, e.text) == ("foundry", "roll", "Paylon", "")
    assert e.data == {"total": 19, "foundry_ts": "2020-01-01T00:00:00Z"}
    assert e.ts.tzinfo is not None
    assert before <= e.ts <= after


async def test_post_without_optional_fields(client, received):
    resp = await client.post("/event", json={"kind": "combat", "text": "Round 1"}, headers={"X-Token": TOKEN})
    assert resp.status == 202
    assert received[0].speaker is None and received[0].data == {}


async def test_preflight_allowed_origin_headers(client):
    resp = await client.options("/event", headers={
        "Origin": ORIGIN,
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Private-Network": "true",
    })
    assert resp.status == 204
    assert resp.headers["Access-Control-Allow-Origin"] == ORIGIN
    assert resp.headers["Access-Control-Allow-Methods"] == "POST"
    assert resp.headers["Access-Control-Allow-Headers"] == "Content-Type, X-Token"
    assert resp.headers["Access-Control-Allow-Private-Network"] == "true"


async def test_preflight_unknown_origin_403(client):
    resp = await client.options("/event", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert resp.status == 403
    assert "Access-Control-Allow-Origin" not in resp.headers


async def test_error_responses_to_allowed_origin_carry_cors(client):
    resp = await client.post("/event", json={"kind": "chat", "text": "x"}, headers={"Origin": ORIGIN})
    assert resp.status == 401
    assert resp.headers["Access-Control-Allow-Origin"] == ORIGIN
