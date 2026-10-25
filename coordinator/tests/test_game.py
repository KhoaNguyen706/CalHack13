"""Coordinator ↔ SpacetimeDB live board, with an in-memory stand-in for the game database."""

import time

import pytest
from fastapi.testclient import TestClient

from coordinator.main import create_app
from coordinator.spacetime import SpacetimeError


class FakeSpacetime:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.seats: list[dict] = []
        self.rounds: list[dict] = []

    async def call(self, reducer: str, args: dict) -> None:
        if self.fail:
            raise SpacetimeError("start_round: 503 down")
        self.calls.append((reducer, args))
        if reducer != "start_round":
            return
        mid = args["matchId"]
        self.rounds = [{"match_id": mid, "state": "playing", "answer": [1, []]}]
        self.seats = [{"match_id": mid, "agent_id": s["agent_id"], "checked_in": False} for s in args["seats"]]

    async def sql(self, query: str) -> list[dict]:
        return self.seats if "seat" in query else self.rounds


def wait_for(fn, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if result := fn():
            return result
        time.sleep(0.02)
    raise AssertionError("condition not met")


def confirmed_demo_match(fake):
    client = TestClient(create_app(db_path=":memory:", use_claude=False, spacetime=lambda _: fake, game_poll_s=0.05))
    client.__enter__()
    client.post("/demo/seed")
    match = next(m for m in client.post("/matching/run").json() if "linh" in m["members"])
    for who in match["members"]:
        client.post(f"/matches/{match['match_id']}/accept", json={"agent_id": who})
    return client, match["match_id"]


@pytest.fixture
def fake():
    return FakeSpacetime()


def texts(client):
    return [e["text"] for e in client.get("/log").json()]


def test_confirm_starts_round_and_marks_playing(fake):
    client, mid = confirmed_demo_match(fake)
    wait_for(lambda: client.get(f"/matches/{mid}").json()["state"] == "playing")
    reducer, args = fake.calls[0]
    assert reducer == "start_round" and args["matchId"] == mid and args["timerMin"] == 15
    assert args["answer"] == "the Apify booth" and args["kind"] == "spot_and_pose"
    assert {s["agent_id"]: s["role"] for s in args["seats"]} == {"linh": "riddles", "marcus": "map", "priya": "camera"}
    assert all(s["key"] for s in args["seats"])
    assert {s["key"] for s in args["seats"]} == {"It's a sponsor booth.", "They help you scrape websites.", "It's near the snacks."}
    assert all("hint" in s for s in args["seats"])  # the demo round has none; designed rounds pass theirs on
    assert "round live · 3 phones can join" in texts(client)
    client.__exit__(None, None, None)


def test_board_events_reach_the_log_and_finish_the_match(fake):
    client, mid = confirmed_demo_match(fake)
    wait_for(lambda: fake.calls)
    for s in fake.seats:
        s["checked_in"] = True
    fake.rounds[0]["state"] = "unlocked"
    wait_for(lambda: "everyone's here → group photo unlocked" in texts(client))
    assert texts(client).count("Linh: we're here ✓") == 1

    fake.rounds[0].update(state="done", answer=[0, "the Apify booth"])
    wait_for(lambda: client.get(f"/matches/{mid}").json()["state"] == "done")
    assert "photo in · challenge complete! answer: the Apify booth" in texts(client)
    assert client.get("/agents/linh").json()["match_id"] is None  # back in the pool
    client.__exit__(None, None, None)


def test_board_down_leaves_match_confirmed():
    client, mid = confirmed_demo_match(FakeSpacetime(fail=True))
    wait_for(lambda: "live board unavailable · round not started" in texts(client))
    assert client.get(f"/matches/{mid}").json()["state"] == "confirmed"
    client.__exit__(None, None, None)


def test_leaving_mid_game_drops_the_seat_then_calls_it_off(fake):
    client, mid = confirmed_demo_match(fake)
    wait_for(lambda: client.get(f"/matches/{mid}").json()["state"] == "playing")

    left = client.post(f"/matches/{mid}/leave", json={"agent_id": "marcus"}).json()
    assert left["state"] == "playing" and left["members"] == ["linh", "priya"]
    rnd = left["challenge"]["round"]
    assert set(rnd["keys"]) == {"linh", "priya"} and set(rnd["shared"]) == {"marcus"}  # the rest still get the word
    assert fake.calls[-1] == ("drop_seat", {"matchId": mid, "agentId": "marcus"})
    assert client.get("/agents/marcus").json()["match_id"] is None

    off = client.post(f"/matches/{mid}/leave", json={"agent_id": "linh"}).json()  # only Priya would be left
    assert off["state"] == "cancelled"
    assert fake.calls[-1] == ("end_round", {"matchId": mid})
    assert all(client.get(f"/agents/{a}").json()["match_id"] is None for a in ("linh", "priya"))
    assert client.post(f"/matches/{mid}/leave", json={"agent_id": "priya"}).status_code == 409
    client.__exit__(None, None, None)
