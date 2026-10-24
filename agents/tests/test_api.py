import json
import re
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agents.config import Settings
from agents.main import create_app


class FakeCoordinator:
    """Just enough of the coordinator's HTTP API, in memory, including the planned POST /challenges."""

    def __init__(self, host_led: bool = True):
        self.host_led = host_led  # False = a coordinator without /challenges yet
        self.agents: dict[str, dict] = {}
        self.matches: dict[str, dict] = {}
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((method, path, body))
        if method == "POST" and path == "/agents/checkin":
            agent = {**body, "match_id": self.agents.get(body["agent_id"], {}).get("match_id")}
            self.agents[body["agent_id"]] = agent
            return httpx.Response(200, json=agent)
        if method == "GET" and (m := re.fullmatch(r"/agents/([^/]+)", path)):
            agent = self.agents.get(m.group(1))
            return httpx.Response(200, json=agent) if agent else httpx.Response(404, json={"detail": "unknown agent"})
        if method == "POST" and path == "/challenges":
            if not self.host_led:
                return httpx.Response(404, json={"detail": "Not Found"})
            match = self.invite(body["agent_id"], [], size=body["size"])
            return httpx.Response(200, json=match)
        if path == "/matching/run":
            return httpx.Response(200, json=[])
        if method == "GET" and path == "/matches":
            return httpx.Response(200, json=list(self.matches.values()))
        if m := re.fullmatch(r"/matches/([^/]+)(/accept)?", path):
            match = self.matches.get(m.group(1))
            if match is None:
                return httpx.Response(404, json={"detail": "unknown match"})
            if m.group(2):
                match["accepted"] = sorted(set(match["accepted"]) | {body["agent_id"]}, key=match["members"].index)
                if len(match["accepted"]) == match["size"]:
                    match["state"] = "confirmed"
            return httpx.Response(200, json=match)
        return httpx.Response(404, json={"detail": "Not Found"})

    def invite(self, host, invitees, size=None, match_id="m1"):
        """A host-led match: the host is in and accepted, invitees whose agents said yes wait for their Accept."""
        members = [host, *invitees]
        self.matches[match_id] = {
            "match_id": match_id, "host": host, "size": size or len(members), "members": members, "roles": {},
            "area": "Palace of Fine Arts", "window": "Sat 14:00-16:00", "accepted": [host], "challenge": None,
            "state": "pending" if len(members) == (size or len(members)) else "inviting",
        }
        for a in members:
            self.agents.setdefault(a, {"agent_id": a})["match_id"] = match_id
        return self.matches[match_id]


@pytest.fixture
def coord():
    return FakeCoordinator()


def make_client(coord, tmp_path, **overrides):
    settings = Settings(
        coordinator_url="http://coordinator", public_url="http://agents", db_path=":memory:", cache_dir=tmp_path,
        use_claude=False, apify_token="", moss_project_id="", moss_project_key="", watch_every_s=0.05, **overrides,
    )
    return TestClient(create_app(settings, coordinator_transport=httpx.MockTransport(coord), watch=False))


@pytest.fixture
def client(coord, tmp_path):
    with make_client(coord, tmp_path) as c:
        yield c


def wait_for(fn, timeout=6.0):
    end = time.time() + timeout
    while time.time() < end:
        if result := fn():
            return result
        time.sleep(0.05)
    raise AssertionError("condition not met")


def login(client, name="Phong", identity="id-phong", context_id=None):
    return client.post("/users/login", json={"identity": identity, "name": name, "context_id": context_id})


def lines(client, agent_id):
    return [line["text"] for line in client.get(f"/users/{agent_id}").json()["transcript"]]


def tick(client):
    client.portal.call(client.app.state.svc.tick)


def test_login_loads_a_personal_context(client):
    me = login(client).json()
    assert me["agent_id"] == "phong" and me["context_id"] == "linh"  # the first free sample
    assert "puzzles" in me["profile"]["interests"] and me["profile"]["free"] is None
    assert login(client).json()["agent_id"] == "phong"  # same identity, same agent
    assert login(client, "Jess", "id-jess").json()["context_id"] == "marcus"  # the next free one
    assert login(client, "Khoa", "id-khoa", context_id="linh").status_code == 409  # taken
    assert login(client, "Khoa", "id-khoa", context_id="nobody").status_code == 404
    assert login(client, "Linh", "id-real-linh").json()["agent_id"] == "linh-2"  # sample ids stay free
    wait_for(lambda: client.get("/users/jess").json()["checked_in"])  # available to be invited, like a real agent
    taken = {c["id"]: c["taken_by"] for c in client.get("/contexts").json()}
    assert taken["linh"] == "Phong" and taken["marcus"] == "Jess" and taken["kenji"] is None
    assert client.get("/users/nobody").status_code == 404


def test_stand_ins_skip_claimed_contexts(client, coord):
    login(client)  # Phong takes linh
    out = client.post("/contexts/seed").json()
    assert out["seeded"] == 19 and "linh" not in out["agents"]
    marcus, alex = coord.agents["marcus"], coord.agents["alex"]
    assert marcus["callback_url"] == "http://agents/agents/marcus/question"
    assert marcus["area"] == "Palace of Fine Arts" and alex["area"] == "SJSU"
    shifted = client.post("/contexts/seed?shift_to_now=true").json()
    assert shifted["shift_to_now"] and "marcus" in shifted["agents"]
    # Stand-ins judge plans against the window they checked in with, also after a restart.
    q = {"question_id": "q1", "agent_id": "marcus", "question": "join?", "plan": {"window": coord.agents["marcus"]["free"]}}
    assert client.post("/agents/marcus/question", json=q).json()["answer"] == "yes"
    client.app.state.svc._stand_in_free.clear()
    assert client.post("/agents/marcus/question", json=q).json()["answer"] == "yes"


def test_invitation_answer_shows_in_the_chat(client):
    login(client)
    login(client, "Jess", "id-jess")
    q = {"question_id": "q1", "agent_id": "x", "question": "join?", "plan": {"window": "Sat 14:00-16:00", "host": "phong", "size": 4}}
    assert client.post("/agents/jess/question", json=q).json()["answer"] == "yes"
    assert any("Phong's 4-person challenge, Sat 2:00–4:00pm. I said yes" in t for t in lines(client, "jess"))


def test_start_asks_the_coordinator_for_that_size(client, coord):
    login(client)
    assert client.post("/users/phong/start", json={"size": 6}).status_code == 422
    assert client.post("/users/phong/start", json={"size": 2}).status_code == 422  # never fewer than 3
    status = client.post("/users/phong/start", json={"size": 4}).json()
    assert status["checked_in"] and coord.agents["phong"]["callback_url"] == "http://agents/agents/phong/question"
    wait_for(lambda: ("POST", "/challenges", {"agent_id": "phong", "size": 4}) in coord.calls)
    assert any(t.startswith("Starting a 4-person challenge") for t in lines(client, "phong"))
    status = client.get("/users/phong").json()
    assert status["is_host"] and status["match"]["state"] == "inviting" and not status["needs_accept"]


def test_start_falls_back_while_the_coordinator_has_no_challenges(tmp_path):
    coord = FakeCoordinator(host_led=False)
    with make_client(coord, tmp_path) as client:
        login(client)
        client.post("/users/phong/start")
        wait_for(lambda: any("waiting for more people" in t for t in lines(client, "phong")))
        assert ("POST", "/matching/run", None) in coord.calls


def test_invitations_joins_and_stand_ins(client, coord):
    login(client)  # Phong (host)
    login(client, "Jess", "id-jess")
    coord.invite("phong", ["jess", "kenji"], size=3)
    tick(client)
    wait_for(lambda: "kenji" in coord.matches["m1"]["accepted"])  # a stand-in accepts on its own
    tick(client)
    jess = client.get("/users/jess").json()
    assert jess["needs_accept"] and any("Phong invited you to a 3-person challenge" in t for t in lines(client, "jess"))
    assert any(t == "Kenji joined (2 of 3)." for t in lines(client, "phong"))

    client.post("/users/jess/accept", json={"accept": True})
    tick(client)
    assert coord.matches["m1"]["state"] == "confirmed"
    assert any(t == "Jess joined (3 of 3)." for t in lines(client, "phong"))


def test_challenge_status_and_photo_checks(client, coord):
    login(client)
    login(client, "Jess", "id-jess")
    match = coord.invite("phong", ["jess"], size=2)
    members = [{"agent_id": "phong", "interests": ["puzzles"]}, {"agent_id": "jess", "interests": ["campus"]}]
    challenge = client.post("/challenge", json={"match": match, "members": members}).json()
    assert challenge["round"]["designed_by"].startswith("built-in color hunt")
    match.update(state="confirmed", accepted=["phong", "jess"], challenge=challenge)

    status = client.get("/users/jess").json()
    assert status["my_part"] == challenge["round"]["keys"]["jess"] and "Phong" in status["my_part"]
    assert "keys" not in status["challenge"] and len(status["challenge"]["steps"]) == 3  # others' parts hidden
    assert status["progress"] == {"steps": [{**s, "passed": False, "reason": None} for s in status["progress"]["steps"]], "done": False}
    tick(client)
    assert any(t.startswith("Everyone's in!") and "Your part" in t for t in lines(client, "jess"))

    photo = {"agent_id": "jess", "image_url": "https://example.com/photo.jpg"}
    assert client.post("/challenges/m1/check", json={**photo, "step": "s9"}).status_code == 404
    for step in ("s1", "s2", "s3"):
        out = client.post("/challenges/m1/check", json={**photo, "step": step}).json()
        assert out["passed"] and "offline" in out["reason"]  # no Claude: photos count
    assert out["done"] and client.get("/challenges/m1/progress").json()["done"]
    assert any(t.startswith("Step 3 of 3 done") for t in lines(client, "phong"))
    assert any(t.startswith("Challenge complete!") for t in lines(client, "phong"))

    coord.invite("phong", ["jess"], size=2, match_id="m2")
    assert client.post("/challenges/m2/check", json={**photo, "step": "s1"}).status_code == 409  # no challenge yet


def test_feedback_is_private_until_mutual(client, coord):
    login(client)
    login(client, "Jess", "id-jess")
    login(client, "Khoa", "id-khoa")
    coord.invite("phong", ["jess", "khoa"], size=3)
    fb = {"match_id": "m1", "rating": 5, "share_ok": True}
    out = client.post("/feedback", json={**fb, "agent_id": "phong", "meet_again": True, "comment": "fun"}).json()
    assert out["my_matches"] == [] and out["waiting_for"] == ["jess", "khoa"] and not out["mutual_meet_again"]
    client.post("/feedback", json={**fb, "agent_id": "jess", "meet_again": True})
    two = client.get("/feedback/m1").json()
    assert two["waiting_for"] == ["khoa"] and not two["share_ok"]  # no photo until everyone said OK
    client.post("/feedback", json={**fb, "agent_id": "khoa", "meet_again": False, "share_ok": False})
    assert client.get("/feedback/m1?agent_id=phong").json()["my_matches"] == ["Jess"]
    assert client.get("/feedback/m1?agent_id=khoa").json()["my_matches"] == []
    summary = client.get("/feedback/m1").json()
    assert summary["mutual_meet_again"] and not summary["share_ok"] and summary["avg_rating"] == 5
    assert client.get("/stats").json()["text"] == "1 of 1 groups wanted to meet again"
    client.post("/feedback", json={**fb, "agent_id": "phong", "meet_again": True, "comment": "fun"})  # resubmitted
    ratings = [m for m in client.app.state.svc.store.recent_memories("phong", 10) if "rated it" in m]
    assert ratings == ['Played an outdoor challenge with Jess and Khoa (3 people) and rated it 5/5. They said: "fun". Wants to meet them again.']


def test_chat_without_claude_points_to_start(client):
    login(client)
    out = client.post("/chat/phong", json={"message": "hi"}).json()
    assert "Start" in out["reply"] and out["transcript"][-1]["role"] == "agent"


def test_place_builtin_without_apify(client):
    place = client.get("/place").json()
    assert not place["scraped"] and "Palace of Fine Arts" in place["facts"][0]["text"] and place["images"] == []
