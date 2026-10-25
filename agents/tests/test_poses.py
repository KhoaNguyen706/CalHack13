"""Pose ideas found with Apify: picking candidates, checking Claude's verdicts, and a whole search driven by a fake
Apify and a fake Claude (no network), through to a round and its photo check."""

import base64
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from agents import poses
from agents.config import Settings
from agents.designer import Hint, Hints
from agents.main import create_app
from agents.poses import Judged, PoseSearch, Verdicts
from agents.store import Store
from agents.vision import Verdict

from .test_api import FakeCoordinator, login
from .test_claude_paths import FakeClaude

PNG = b"\x89PNG" + b"0" * 9000  # big enough not to count as an icon
TINY = b"\x89PNG" + b"0" * 100
TRIO = "https://i.pinimg.com/474x/aa/trio.png"


def test_candidates_take_og_images_first_round_robin_and_bigger_pins():
    pages = [
        {"metadata": {"url": "https://a.example", "openGraph": [{"property": "og:image", "content": "https://a.example/og.png"}]},
         "markdown": "![1](https://i.pinimg.com/236x/1.png) ![2](https://a.example/2.png) ![3](https://a.example/3.png) ![4](https://a.example/4.png)"},
        {"metadata": {"url": "https://b.example", "openGraph": {"og:image": "https://b.example/og.png"}}, "markdown": "![x](https://a.example/2.png)"},
    ]
    found = [c.url for c in poses.candidates(pages, limit=5)]
    # Each page's og:image first, then its inline images in turn; Pinterest thumbnails come larger; no repeats.
    assert found == ["https://a.example/og.png", "https://b.example/og.png", "https://i.pinimg.com/736x/1.png", "https://a.example/2.png", "https://a.example/3.png"]
    assert poses.candidates(pages)[1].page == "https://b.example"


def test_checked_keeps_only_poses_that_split_into_one_part_each():
    good = Judged(index=0, usable=True, name="See no evil trio", pose="Each covers eyes, mouth or ears.", cuts=[0.02, 0.35, 0.68, 0.97], top=0.9)
    ok = poses.checked(good, 3)
    assert ok.cuts == [0, 0.35, 0.68, 1] and ok.top == 0.5  # ends pinned to the edges, top capped
    assert poses.checked(good, 2) is None  # three people, not two
    assert poses.checked(good.model_copy(update={"usable": False}), 3) is None
    assert poses.checked(good.model_copy(update={"cuts": [0, 0.04, 0.5, 1]}), 3) is None  # a sliver of a part
    assert poses.checked(good.model_copy(update={"name": " "}), 3) is None


def web(request: httpx.Request) -> httpx.Response:
    """Apify's RAG Web Browser answers every search with one Pinterest page; images download; the rest is missing."""
    if request.url.host == "rag-web-browser.apify.actor":
        page = {
            "metadata": {"url": "https://www.pinterest.com/ideas/trio-poses", "title": "Trio poses", "openGraph": [{"property": "og:image", "content": TRIO}]},
            "markdown": "Trio poses ![collage](https://i.pinimg.com/236x/bb/collage.png) ![icon](https://img.example/tiny.png)",
        }
        return httpx.Response(200, json=[page])
    if request.url.path.endswith("tiny.png"):
        return httpx.Response(200, content=TINY, headers={"content-type": "image/png"})
    if request.url.path.endswith(".png"):
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
    return httpx.Response(404)


TRIO_POSE = Judged(index=0, usable=True, name="See no evil trio", pose="One covers their eyes, one their mouth, one their ears.", cuts=[0, 0.34, 0.67, 1])


@pytest.fixture
def setup(tmp_path):
    coord = FakeCoordinator()
    claude = FakeClaude(
        turns=[],
        parsed={
            # Every group size's search sees the same two images: the trio photo, and a collage that isn't usable.
            Verdicts: Verdicts(images=[TRIO_POSE, Judged(index=1, usable=False)]),
            Hints: Hints(hints=[Hint(agent_id=a, hint=f"Hint for {a}.") for a in ("phong", "jess", "khoa")]),
            Verdict: Verdict(passed=True, reason="Everyone's covering something. Nice!"),
        },
    )
    settings = Settings(
        coordinator_url="http://coordinator", public_url="http://agents", db_path=":memory:", cache_dir=tmp_path,
        use_claude=False, apify_token="apify-test", moss_project_id="", moss_project_key="",
    )
    app = create_app(settings, claude=claude, coordinator_transport=httpx.MockTransport(coord), web_transport=httpx.MockTransport(web), watch=False)
    with TestClient(app) as client:
        yield client, coord, claude


def test_apify_poses_reach_the_round_and_the_photo_check(setup, monkeypatch):
    client, coord, claude = setup
    monkeypatch.setattr(poses, "TARGET", 1)  # one found pose is enough: no hand-picked memes mixed in
    counts = client.post("/poses/refresh").json()
    assert counts == {"2": 0, "3": 1, "4": 0, "5": 0}  # the trio only fits a group of three
    found = client.get("/poses").json()["3"]
    assert [(p["name"], p["people"], p["source"]) for p in found] == [("See no evil trio", 3, "https://www.pinterest.com/ideas/trio-poses")]
    stored = client.get(found[0]["image"])  # a copy on this service, so an expiring link can't break a round
    assert stored.status_code == 200 and stored.content == PNG
    assert client.get("/health").json()["poses_found"] == {"2": 0, "3": 1, "4": 0, "5": 0}
    judged = next(r for r in claude.requests if r["output_format"] is Verdicts)
    assert sum(b["type"] == "image" for b in judged["messages"][0]["content"]) == 2  # the icon was never sent

    for name, identity in (("Phong", "id-phong"), ("Jess", "id-jess"), ("Khoa", "id-khoa")):
        login(client, name, identity)
    match = coord.invite("phong", ["jess", "khoa"], size=3)
    members = [{"agent_id": a, "interests": [], "skills": []} for a in ("phong", "jess", "khoa")]
    challenge = client.post("/challenge", json={"match": match, "members": members}).json()
    rnd = challenge["round"]
    assert rnd["pose"] == {"image": found[0]["image"], "cuts": [0, 0.34, 0.67, 1], "top": 0} and rnd["answer"].endswith("· See no evil trio")

    # A group of two gets a hand-picked meme: nothing was found for pairs.
    pair = client.post("/challenge", json={"match": {"match_id": "m2"}, "members": members[:2]}).json()["round"]
    assert pair["pose"]["image"].startswith("https://")

    match.update(state="confirmed", accepted=["phong", "jess", "khoa"], challenge=challenge)
    photo = base64.b64encode(b"\xff\xd8\xff group").decode()
    out = client.post("/challenges/m1/check", json={"step": "photo", "agent_id": "jess", "image_base64": photo}).json()
    assert out["passed"]
    sent = claude.requests[-1]["messages"][0]["content"]
    images = [b["source"]["data"] for b in sent if b["type"] == "image"]
    assert images == [base64.b64encode(PNG).decode(), photo]  # the stored pose is the reference


def test_without_apify_groups_get_the_hand_picked_memes(tmp_path):
    settings = Settings(db_path=":memory:", cache_dir=tmp_path, use_claude=False, apify_token="", moss_project_id="", moss_project_key="")
    app = create_app(settings, coordinator_transport=httpx.MockTransport(FakeCoordinator()), web_transport=httpx.MockTransport(web), watch=False)
    with TestClient(app) as client:
        assert client.post("/poses/refresh").status_code == 409
        members = [{"agent_id": a, "interests": [], "skills": []} for a in ("phong", "jess")]
        rnd = client.post("/challenge", json={"match": {"match_id": "m1"}, "members": members}).json()["round"]
        assert rnd["pose"]["image"].startswith("https://i.imgflip.com/")
        assert json.loads(client.get("/poses").text) == {"2": [], "3": [], "4": [], "5": []}


def test_found_poses_survive_a_restart_and_images_are_judged_once(tmp_path):
    db = str(tmp_path / "agents.db")  # a file, so a second app is the same service after a restart
    claude = FakeClaude(turns=[], parsed={Verdicts: Verdicts(images=[TRIO_POSE, Judged(index=1, usable=False)])})
    searched: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rag-web-browser.apify.actor" and "pose" in request.url.params["query"]:
            searched.append(request.url.params["query"])
        return web(request)

    def app():
        settings = Settings(
            coordinator_url="http://coordinator", db_path=db, cache_dir=tmp_path, use_claude=False, apify_token="apify-test",
            moss_project_id="", moss_project_key="",
        )
        return create_app(settings, claude=claude, coordinator_transport=httpx.MockTransport(FakeCoordinator()), web_transport=httpx.MockTransport(recording), watch=False)

    def judged():
        return sum(r["output_format"] is Verdicts for r in claude.requests)

    with TestClient(app()) as client:
        assert client.post("/poses/refresh").json()["3"] == 1
        first, calls = list(searched), judged()
        assert calls == 4  # one look per group size
        assert client.post("/poses/refresh").json()["3"] == 1  # search again: other phrasings, the same images
        assert judged() == calls  # images already judged never go to Claude again
        assert set(searched[len(first):]).isdisjoint(first)

    searched.clear()
    with TestClient(app()) as client:  # a restart or a deploy: the poses are still there, and nothing's due yet
        assert client.get("/health").json()["poses_found"]["3"] == 1
        assert client.get(client.get("/poses").json()["3"][0]["image"]).content == PNG
    assert searched == []


def test_a_size_searches_until_it_has_enough_then_once_a_day():
    store = Store(":memory:")
    search = PoseSearch(Settings(apify_token="apify-test"), httpx.AsyncClient(), object(), store)
    pose = {"id": "p", "name": "x", "people": 3, "image": "/photos/p", "cuts": [0, 0.3, 0.6, 1], "pose": "x"}
    now = time.time()

    store.save_pose_cache(3, {"poses": [pose], "fetched_at": now - 20 * 60})
    assert search.stale(3)  # below TARGET: again after 15 minutes
    every = {q: now for q in poses.queries(3)}
    store.save_pose_cache(3, {"poses": [pose], "fetched_at": now - 20 * 60, "queries": every})
    assert not search.stale(3)  # every phrasing tried today: nothing new to search
    store.save_pose_cache(3, {"poses": [pose] * poses.TARGET, "fetched_at": now - 3600})
    assert not search.stale(3)  # enough: once a day
    store.save_pose_cache(3, {"poses": [pose] * poses.TARGET, "fetched_at": now - 25 * 3600})
    assert search.stale(3)
    assert search._next_queries(3, {"queries": {poses.queries(3)[0]: now}}) == poses.queries(3)[1:3]  # untried first


def test_hand_picked_memes_fill_in_until_a_size_has_enough(monkeypatch):
    from agents.designer import load_poses

    store = Store(":memory:")
    search = PoseSearch(Settings(apify_token=""), httpx.AsyncClient(), None, store)
    pose = {"id": "p", "name": "x", "people": 3, "image": "/photos/p", "cuts": [0, 0.3, 0.6, 1], "pose": "x"}
    store.save_pose_cache(3, {"poses": [pose], "fetched_at": time.time()})
    memes = load_poses()
    assert [p.id for p in search.poses_for(3, memes)] == ["p", *(m.id for m in memes)]
    store.save_pose_cache(3, {"poses": [pose] * poses.TARGET, "fetched_at": time.time()})
    assert {p.id for p in search.poses_for(3, memes)} == {"p"}  # enough found: only those
