import base64
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


def login(client, name="Phong", identity="id-phong", context_id=None, share=True):
    """Sign in, then tap Share on the consent card (share=False leaves them private)."""
    resp = client.post("/users/login", json={"identity": identity, "name": name, "context_id": context_id})
    if share and resp.status_code == 200:
        client.post(f"/users/{resp.json()['agent_id']}/consent", json={"share": True})
    return resp


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
    assert shifted["shift_to_now"] and "priya" in shifted["agents"]
    # Stand-ins judge plans against the window they checked in with, also after a restart.
    q = {"question_id": "q1", "agent_id": "priya", "question": "join?", "plan": {"window": coord.agents["priya"]["free"]}}
    assert client.post("/agents/priya/question", json=q).json()["answer"] == "yes"
    client.app.state.svc._stand_in_free.clear()
    assert client.post("/agents/priya/question", json=q).json()["answer"] == "yes"


def test_invitation_answer_shows_in_the_chat(client):
    login(client)
    login(client, "Jess", "id-jess")
    q = {"question_id": "q1", "agent_id": "x", "question": "join?", "plan": {"window": "Sat 14:00-16:00", "host": "phong", "size": 4}}
    # Jess borrows Marcus's sample context (90-minute plans), but a real person decides for themselves: their agent
    # only checks the time fits, and never declines for them.
    assert client.post("/agents/jess/question", json=q).json()["answer"] == "yes"
    said = "a 4-person challenge nearby, Sat 2:00–4:00pm. I said yes: fits your free time. It's your call: tap Accept or Not now."
    assert any(said in t for t in lines(client, "jess"))


def test_start_asks_the_coordinator_for_that_size(client, coord):
    login(client)
    assert client.post("/users/phong/start", json={"size": 6}).status_code == 422
    assert client.post("/users/phong/start", json={"size": 1}).status_code == 422  # never alone
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
    assert jess["needs_accept"] and any("You're invited to a 3-person challenge nearby" in t for t in lines(client, "jess"))
    assert not any("Phong" in t for t in lines(client, "jess"))  # the host stays anonymous until everyone accepts
    assert any(t == "Someone joined (2 of 3)." for t in lines(client, "phong"))

    client.post("/users/jess/accept", json={"accept": True})
    tick(client)
    assert coord.matches["m1"]["state"] == "confirmed"
    assert any(t == "Someone joined (3 of 3)." for t in lines(client, "phong"))


def test_challenge_status_and_photo_checks(client, coord):
    login(client)
    login(client, "Jess", "id-jess")
    match = coord.invite("phong", ["jess"], size=2)
    members = [{"agent_id": "phong", "interests": ["puzzles"]}, {"agent_id": "jess", "interests": ["campus"]}]
    challenge = client.post("/challenge", json={"match": match, "members": members}).json()
    rnd = challenge["round"]
    assert rnd["designed_by"] == "hand-written hints (Claude off)" and rnd["title"] == "Spot & Pose"
    match.update(state="confirmed", accepted=["phong", "jess"], challenge=challenge)

    status = client.get("/users/jess").json()
    assert status["my_part"] == rnd["keys"]["jess"] and status["my_part"] != rnd["keys"]["phong"]
    assert "keys" not in status["challenge"] and status["challenge"]["pieces"] == rnd["pieces"]  # others' hints hidden
    assert status["challenge"]["steps"] == [{"id": "photo", "task": "Group photo at the spot, doing the pose"}]  # no check
    assert status["progress"] == {"steps": [{**status["challenge"]["steps"][0], "passed": False, "reason": None}], "done": False}
    tick(client)
    word = f"Your hint: “{status['my_part']}”"
    meet = status["challenge"]["meet"]
    assert meet["spot"] == "the main entrance of the Palace of Fine Arts" and meet["sign"]  # public, like the pieces
    hello = f"Everyone's in! You're meeting Phong. Meet at {meet['spot']}. Look for phones showing {meet['emoji']} {meet['sign']}"
    assert any(t.startswith(hello) and "Spot & Pose:" in t and word in t for t in lines(client, "jess"))
    view = client.get("/challenges/m1").json()
    assert view["members"] == [{"agent_id": "phong", "name": "Phong"}, {"agent_id": "jess", "name": "Jess"}]
    assert view["challenge"]["pose"] == rnd["pose"] and not {"keys", "answer", "spot"} & set(view["challenge"])
    assert view["challenge"]["spot_name"] == rnd["spot_name"]  # the page shows it once everyone's there

    photo = {"agent_id": "jess", "image_url": "https://example.com/photo.jpg"}
    assert client.post("/challenges/m1/check", json={**photo, "step": "s9"}).status_code == 404
    out = client.post("/challenges/m1/check", json={**photo, "step": "photo"}).json()
    assert out["passed"] and "offline" in out["reason"] and out["done"]  # no Claude: photos count
    assert client.get("/challenges/m1/progress").json()["done"]
    assert not any(t.startswith("Step ") for t in lines(client, "phong"))  # one photo: no "Step 1 of 1"
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


def test_group_photo_upload_and_serve(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"photo bytes"
    body = {"match_id": "m1", "agent_id": "phong", "image_base64": base64.b64encode(jpeg).decode()}
    up = client.post("/photos", json=body).json()
    got = client.get(up["path"])
    assert got.status_code == 200 and got.content == jpeg and got.headers["content-type"] == "image/jpeg"
    bad = {"match_id": "m1", "image_base64": base64.b64encode(b"not an image").decode()}
    assert client.post("/photos", json=bad).status_code == 422
    assert client.get("/photos/nope").status_code == 404


def test_nothing_is_shared_until_they_tap_share(client, coord):
    me = login(client, share=False).json()
    assert not me["consented"] and not me["checked_in"] and "phong" not in coord.agents
    assert me["would_share"]["area"] == "Palace of Fine Arts" and "puzzles" in me["would_share"]["interests"]
    assert client.post("/users/phong/start", json={"size": 2}).status_code == 409  # can't play before sharing
    client.post("/users/phong/consent", json={"share": False})
    assert "phong" not in coord.agents and "won't share anything" in lines(client, "phong")[-1]

    shared = client.post("/users/phong/consent", json={"share": True}).json()
    assert shared["consented"] and set(coord.agents["phong"]) >= {"free", "area", "interests", "skills"}
    assert "about" not in coord.agents["phong"] and "name" not in coord.agents["phong"]  # coarse fields only


def test_spacetimeauth_sign_in_keeps_only_the_first_name(coord, tmp_path):
    def token(claims):
        enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
        return f"{enc({'alg': 'RS256'})}.{enc(claims)}.sig"

    def spacetimeauth(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://auth.spacetimedb.com/oidc/token"
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        assert form["grant_type"] == "authorization_code" and form["code_verifier"] == "v" * 43
        aud = "krew-client" if form["code"] == "good" else "someone-else"
        claims = {"iss": "https://auth.spacetimedb.com/oidc", "aud": aud, "exp": int(time.time()) + 600, "sub": "u-1234",
                  "given_name": "Minh", "name": "Minh Tran", "email": "minh@example.com", "picture": "https://x/p.jpg"}
        return httpx.Response(200, json={"id_token": token(claims), "access_token": "a"})

    settings = Settings(
        coordinator_url="http://coordinator", public_url="http://agents", db_path=":memory:", cache_dir=tmp_path,
        use_claude=False, apify_token="", moss_project_id="", moss_project_key="", spacetimeauth_client_id="krew-client",
    )
    app = create_app(settings, coordinator_transport=httpx.MockTransport(coord), web_transport=httpx.MockTransport(spacetimeauth), watch=False)
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["spacetimeauth"]["client_id"] == "krew-client"
        body = {"code": "good", "code_verifier": "v" * 43, "redirect_uri": "https://krew.test/"}
        me = client.post("/users/spacetime", json=body).json()
        assert me["name"] == "Minh" and me["agent_id"] == "minh" and me["context_id"] is None
        assert me["profile"]["interests"] == [] and "what are you into" in me["transcript"][0]["text"]
        stored = client.app.state.svc.store.users["minh"].model_dump_json()
        assert "minh@example.com" not in stored and "Tran" not in stored and "p.jpg" not in stored
        assert client.post("/users/spacetime", json=body).json()["agent_id"] == "minh"  # same person, same agent
        assert client.post("/users/spacetime", json={**body, "code": "other-app"}).status_code == 401
        assert not coord.agents  # signing in shares nothing


def test_demo_presenters_sign_in_with_agents_that_know_them(client, coord):
    people = client.get("/demo/people").json()
    assert [p["id"] for p in people] == ["khoa", "phong", "miki", "jessica"] and people[0]["name"] == "Khoa"
    me = client.post("/users/demo", json={"person": "phong"}).json()
    assert me["agent_id"] == "phong" and me["name"] == "Phong" and "puzzles" in me["profile"]["interests"]
    assert "I know you well" in me["transcript"][0]["text"] and not me["consented"]
    assert client.post("/users/demo", json={"person": "phong"}).json()["agent_id"] == "phong"  # same person, same agent
    assert client.post("/users/demo", json={"person": "nobody"}).status_code == 404
    client.post("/chat/phong", json={"message": "hi"})
    assert client.post("/demo/people/reset").json() == {"reset": ["phong"]}
    fresh = client.post("/users/demo", json={"person": "phong"}).json()
    assert len(fresh["transcript"]) == 1 and not fresh["consented"]  # a clean start, same profile
    login(client)  # an ordinary demo sign-in never gets a presenter's profile
    assert client.get("/users/phong-2").status_code == 404 or client.get("/users/phong-2").json()["context_id"] != "phong"


def test_a_too_long_rating_makes_the_next_challenge_shorter(client, coord):
    """The memory demo: rate one challenge "too long", and the next one designed for you is shorter."""
    login(client)
    login(client, "Jess", "id-jess")
    coord.invite("phong", ["jess"], size=2)
    client.post("/feedback", json={"match_id": "m1", "agent_id": "phong", "rating": 2, "meet_again": True, "comment": "fun but too long"})
    client.post("/feedback", json={"match_id": "m1", "agent_id": "jess", "rating": 5, "meet_again": True})

    members = [{"agent_id": a, "interests": [], "skills": []} for a in ("phong", "jess")]
    rnd = client.post("/challenge", json={"match": {"match_id": "m2"}, "members": members}).json()["round"]
    assert rnd["timer_min"] == 10
    assert rnd["tuned"] == {"phong": "Kept it to 10 minutes, since your last one ran long."}  # only Phong hears it
