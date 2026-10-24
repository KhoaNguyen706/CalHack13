import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from coordinator.main import create_app
from coordinator.matching import InvitePick, pick_invitees, rank_invitees, validate_invites
from coordinator.models import Agent


@pytest.fixture
def client():
    with TestClient(create_app(db_path=":memory:", use_claude=False, reply_timeout=1)) as c:
        yield c


def wait_for(fn, timeout=4.0):
    end = time.time() + timeout
    while time.time() < end:
        if result := fn():
            return result
        time.sleep(0.02)
    raise AssertionError("condition not met")


def state(client, mid, want):
    return wait_for(lambda: (m := client.get(f"/matches/{mid}").json())["state"] == want and m)


def texts(client):
    return [e["text"] for e in client.get("/log").json()]


def checkin(client, agent_id, free="Sat 14:00-16:00", area="Somewhere"):
    client.post("/agents/checkin", json={"agent_id": agent_id, "free": free, "area": area})


def test_host_invites_until_full_then_everyone_accepts(client):
    client.post("/demo/seed")
    match = client.post("/challenges", json={"agent_id": "linh", "size": 4}).json()
    assert (match["state"], match["host"], match["members"], match["accepted"]) == ("inviting", "linh", ["linh"], ["linh"])
    assert match["roles"] == {}

    full = state(client, match["match_id"], "pending")
    assert full["members"] == ["linh", "marcus", "priya", "hana"] and full["window"] == "Sat 14:00-16:00"
    mid = full["match_id"]
    for who in ("marcus", "priya"):
        assert client.post(f"/matches/{mid}/accept", json={"agent_id": who}).json()["state"] == "pending"
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "hana"}).json()["state"] == "confirmed"

    done = wait_for(lambda: (m := client.get(f"/matches/{mid}").json())["challenge"] and m)
    assert set(done["challenge"]["round"]["keys"]) == set(full["members"])
    log = texts(client)
    assert "Linh wants a 4-person challenge · UC Berkeley · Sat 2:00–4:00pm" in log
    assert "inviting Marcus, Priya, Hana" in log
    assert "Marcus: yes — seeded persona" in log
    assert "all 4 seats filled · waiting for 1/4 approvals" in log
    assert any(t.endswith("→ CONFIRMED") and "Hana ✓" in t for t in log)


def test_size_and_host_rules(client):
    client.post("/demo/seed")
    for size in (1, 6):
        assert client.post("/challenges", json={"agent_id": "linh", "size": size}).status_code == 422
    assert client.post("/challenges", json={"agent_id": "nobody", "size": 3}).status_code == 409
    assert client.post("/challenges", json={"agent_id": "linh", "size": 3}).status_code == 200
    assert client.post("/challenges", json={"agent_id": "linh", "size": 3}).status_code == 409


def test_invitee_who_declines_is_replaced(client):
    client.post("/demo/seed")
    mid = client.post("/challenges", json={"agent_id": "linh", "size": 4}).json()["match_id"]
    state(client, mid, "pending")
    left = client.post(f"/matches/{mid}/accept", json={"agent_id": "marcus", "accept": False}).json()
    assert left["state"] == "inviting" and "marcus" not in left["members"] and left["declined"] == ["marcus"]
    assert client.get("/agents/marcus").json()["match_id"] is None

    refilled = state(client, mid, "pending")
    assert len(refilled["members"]) == 4 and "marcus" not in refilled["members"]
    assert "Marcus ✗ · inviting someone else (3 of 4)" in texts(client)


def test_host_declining_cancels(client):
    client.post("/demo/seed")
    mid = client.post("/challenges", json={"agent_id": "linh", "size": 3}).json()["match_id"]
    state(client, mid, "pending")
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "linh", "accept": False}).json()["state"] == "cancelled"


def test_silent_and_counter_invitees_are_skipped_then_cancel(client):
    for a in ("host", "quiet", "late"):
        checkin(client, a)
    mid = client.post("/challenges", json={"agent_id": "host", "size": 3}).json()["match_id"]
    [q] = wait_for(lambda: client.get("/agents/late/questions").json())
    assert q["plan"]["host"] == "host" and q["plan"]["size"] == 3 and "roles" not in q["plan"]
    assert q["question"] == "Someone nearby wants a 3-person challenge in Somewhere, Sat 2:00–4:00pm. Want to join?"
    reply = {"question_id": q["question_id"], "answer": "counter", "free": "Sat 20:00-22:00", "note": "busy then"}
    client.post("/agents/late/reply", json=reply)

    wait_for(lambda: "1 of 3 joined · nobody else nearby is free then" in texts(client))
    log = texts(client)
    assert "Late: counter (Sat 20:00-22:00) — busy then" in log and "Quiet: no — no reply in time" in log
    match = client.get(f"/matches/{mid}").json()
    assert match["state"] == "inviting" and sorted(match["declined"]) == ["late", "quiet"]

    assert client.post(f"/challenges/{mid}/start").status_code == 409  # only the host accepted
    assert client.post(f"/challenges/{mid}/cancel").json()["state"] == "cancelled"
    assert client.get("/agents/host").json()["match_id"] is None
    assert client.post(f"/challenges/{mid}/cancel").status_code == 409


def test_someone_who_checks_in_later_is_invited(client):
    checkin(client, "host")
    checkin(client, "early")
    mid = client.post("/challenges", json={"agent_id": "host", "size": 3}).json()["match_id"]
    [q] = wait_for(lambda: client.get("/agents/early/questions").json())
    client.post("/agents/early/reply", json={"question_id": q["question_id"], "answer": "yes"})
    wait_for(lambda: "2 of 3 joined · nobody else nearby is free then" in texts(client))

    checkin(client, "late")  # arrives after the coordinator ran out of people
    [q] = wait_for(lambda: client.get("/agents/late/questions").json())
    client.post("/agents/late/reply", json={"question_id": q["question_id"], "answer": "yes"})
    full = state(client, mid, "pending")
    assert full["members"] == ["host", "early", "late"]


def test_two_people_can_play(client):
    checkin(client, "host")
    checkin(client, "friend")
    mid = client.post("/challenges", json={"agent_id": "host", "size": 2}).json()["match_id"]
    [q] = wait_for(lambda: client.get("/agents/friend/questions").json())
    client.post("/agents/friend/reply", json={"question_id": q["question_id"], "answer": "yes"})
    state(client, mid, "pending")
    assert client.post(f"/matches/{mid}/accept", json={"agent_id": "friend"}).json()["state"] == "confirmed"


def test_start_early_with_three(client):
    client.post("/demo/seed")
    mid = client.post("/challenges", json={"agent_id": "alex", "size": 4}).json()["match_id"]
    wait_for(lambda: "3 of 4 joined · nobody else nearby is free then" in texts(client))
    for who in ("bao", "carmen"):
        assert client.post(f"/matches/{mid}/accept", json={"agent_id": who}).json()["state"] == "inviting"
    started = client.post(f"/challenges/{mid}/start").json()
    assert started["state"] == "confirmed" and started["size"] == 3
    assert sorted(started["members"]) == ["alex", "bao", "carmen"]


def test_run_matching_skips_people_in_a_challenge(client):
    client.post("/demo/seed")
    mid = client.post("/challenges", json={"agent_id": "linh", "size": 3}).json()["match_id"]
    members = state(client, mid, "pending")["members"]
    groups = client.post("/matching/run").json()
    assert not set(members) & {m for g in groups for m in g["members"]}


# --- ranking -------------------------------------------------------------------


def agent(agent_id, free="Sat 14:00-16:00", skills=(), interests=(), area="UC Berkeley", pref="small"):
    return Agent(agent_id=agent_id, free=free, skills=list(skills), interests=list(interests), area=area, group_pref=pref)


def test_rank_invitees_filters_and_prefers_a_mix():
    host = agent("h", skills=["riddles"], interests=["puzzles"])
    pool = [
        agent("same", skills=["riddles"], interests=["puzzles"]),
        agent("mix", skills=["camera"], interests=["puzzles"]),
        agent("far", area="SJSU"),
        agent("short", free="Sat 15:30-18:00"),
        agent("big", pref="large"),
    ]
    assert [c.agent_id for c in rank_invitees([host], pool, 3)] == ["mix", "same"]
    assert "big" in [c.agent_id for c in rank_invitees([host], pool, 4)]


def test_pick_invitees_validates_claude():
    ranked = rank_invitees([agent("h")], [agent(f"a{i}", free=f"Sat 14:00-{16 + i}:00") for i in range(4)], 3)
    assert validate_invites(InvitePick(invite=["a0", "a1"], reason="r"), ranked, 2) == ["a0", "a1"]
    for bad in (["a0"], ["a0", "a0"], ["a0", "zz"]):
        assert validate_invites(InvitePick(invite=bad, reason="r"), ranked, 2) is None

    def fake(result):
        async def parse(**kwargs):
            return SimpleNamespace(stop_reason="end_turn", parsed_output=result)

        return SimpleNamespace(messages=SimpleNamespace(parse=parse))

    agents = {a: agent(a) for a in ("h", "a0", "a1", "a2", "a3")}
    good = asyncio.run(pick_invitees(ranked, [agents["h"]], 2, agents, fake(InvitePick(invite=["a3", "a0"], reason="mix"))))
    assert good == (["a3", "a0"], "mix", "claude")
    bad = asyncio.run(pick_invitees(ranked, [agents["h"]], 2, agents, fake(InvitePick(invite=["zz"], reason="?"))))
    assert bad[2] == "rules" and bad[0] == [c.agent_id for c in ranked[:2]]


def test_leaving_before_the_start_reopens_the_seat(client):
    client.post("/demo/seed")
    mid = client.post("/challenges", json={"agent_id": "linh", "size": 3}).json()["match_id"]
    full = state(client, mid, "pending")
    leaver = full["members"][1]
    client.post(f"/matches/{mid}/accept", json={"agent_id": leaver})
    after = client.post(f"/matches/{mid}/leave", json={"agent_id": leaver}).json()
    assert after["state"] == "inviting" and leaver not in after["members"] and leaver in after["declined"]
    assert client.post(f"/matches/{mid}/leave", json={"agent_id": "nobody"}).status_code == 409
