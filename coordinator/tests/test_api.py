import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from coordinator.broker import Broker
from coordinator.main import create_app
from coordinator.models import Agent, Reply


@pytest.fixture
def client():
    with TestClient(create_app(db_path=":memory:", use_claude=False, reply_timeout=1)) as c:
        yield c


@pytest.fixture
def demo_match(client):
    client.post("/demo/seed")
    matches = client.post("/matching/run").json()
    return next(m for m in matches if "linh" in m["members"])


def wait_for(fn, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if result := fn():
            return result
        time.sleep(0.02)
    raise AssertionError("condition not met")


def test_seed_and_match(client, demo_match):
    assert demo_match["members"] == ["linh", "marcus", "priya"]
    assert demo_match["roles"] == {"linh": "riddles", "marcus": "map", "priya": "camera"}
    assert demo_match["state"] == "pending" and demo_match["window"] == "Sat 14:00-16:00"
    matches = client.get("/matches").json()
    assert {tuple(m["members"]) for m in matches} >= {("alex", "bao", "carmen")}
    everyone = [m for match in matches for m in match["members"]]
    assert len(everyone) == len(set(everyone))  # nobody in two groups
    texts = [e["text"] for e in client.get("/log").json()]
    assert "overlap window: Sat 2:00–4:00pm" in texts
    assert "roles → Linh: riddles · Marcus: map · Priya: camera" in texts


def test_all_accept_confirms_and_attaches_challenge(client, demo_match):
    mid = demo_match["match_id"]
    for who in ("linh", "marcus"):
        assert client.post(f"/matches/{mid}/accept", json={"agent_id": who}).json()["state"] == "pending"
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "priya"}).json()["state"] == "confirmed"

    match = wait_for(lambda: (m := client.get(f"/matches/{mid}").json())["challenge"] and m)
    keys = match["challenge"]["round"]["keys"]
    assert set(keys) == {"linh", "marcus", "priya"} and len(set(keys.values())) == 3  # a different hint each
    assert any(e["text"].endswith("→ CONFIRMED") for e in client.get("/log").json())

    assert client.post(f"/matches/{mid}/state", json={"state": "done"}).status_code == 409
    assert client.post(f"/matches/{mid}/state", json={"state": "playing"}).json()["state"] == "playing"
    assert client.post(f"/matches/{mid}/state", json={"state": "done"}).json()["state"] == "done"
    assert client.get("/agents/linh").json()["match_id"] is None


def test_decline_returns_everyone_to_pool(client, demo_match):
    mid = demo_match["match_id"]
    client.post(f"/matches/{mid}/accept", json={"agent_id": "linh"})
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "marcus", "accept": False}).json()["state"] == "cancelled"
    assert all(client.get(f"/agents/{a}").json()["match_id"] is None for a in demo_match["members"])
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "priya"}).status_code == 409


def test_accept_errors(client, demo_match):
    assert client.post("/matches/nope/accept", json={"agent_id": "linh"}).status_code == 404
    assert client.post(f"/matches/{demo_match['match_id']}/accept", json={"agent_id": "diego"}).status_code == 409


def test_challenge_push_is_validated(client, demo_match):
    mid = demo_match["match_id"]
    bad = {"round": {"keys": {"linh": "x"}}}
    assert client.put(f"/matches/{mid}/challenge", json=bad).status_code == 409  # still pending
    for who in demo_match["members"]:
        client.post(f"/matches/{mid}/accept", json={"agent_id": who})
    assert client.put(f"/matches/{mid}/challenge", json=bad).status_code == 422
    good = {"round": {"keys": {m: "k" for m in demo_match["members"]}, "timer_min": 10}}
    assert client.put(f"/matches/{mid}/challenge", json=good).json()["challenge"] == good


def test_silent_agent_times_out_as_no(client):
    for a in ("x1", "x2", "x3"):
        client.post("/agents/checkin", json={"agent_id": a, "free": "Sat 14:00-16:00", "area": "Somewhere"})
    assert client.post("/matching/run").json() == []
    assert any("trying another group" in e["text"] for e in client.get("/log").json())


def test_bad_checkin_rejected(client):
    assert client.post("/agents/checkin", json={"agent_id": "Linh Tran", "free": "Sat 14:00-16:00", "area": "x"}).status_code == 422
    assert client.post("/agents/checkin", json={"agent_id": "linh", "free": "2pm", "area": "x"}).status_code == 422


def test_websocket_replays_log(client):
    client.post("/demo/seed")
    with client.websocket_connect("/ws/log") as ws:
        first = ws.receive_json()
        assert first["kind"] == "coordinator" and "checked in" in first["text"]


def test_broker_polling_reply_and_counter():
    async def scenario():
        async with httpx.AsyncClient() as http:
            broker = Broker(http, timeout=2)
            agent = Agent(agent_id="web", free="Sat 14:00-16:00", area="x")
            task = asyncio.create_task(broker.ask(agent, "fits?"))
            await asyncio.sleep(0.05)
            [q] = broker.questions_for("web")
            assert not broker.reply("other", Reply(question_id=q.question_id, answer="yes"))
            assert broker.reply("web", Reply(answer="counter", free="Sat 15:00-17:00"))
            reply = await task
            assert reply.answer == "counter" and reply.free == "Sat 15:00-17:00"
            assert broker.questions_for("web") == []

    asyncio.run(scenario())


def test_broker_callback_answers_inline():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/agent"
        return httpx.Response(200, json={"answer": "no", "note": "busy"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            agent = Agent(agent_id="cb", free="Sat 14:00-16:00", area="x", callback_url="http://agent.test/agent")
            return await Broker(http, timeout=2).ask(agent, "fits?")

    assert asyncio.run(scenario()).answer == "no"
