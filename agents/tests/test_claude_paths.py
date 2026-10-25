"""The Claude code paths, driven by a scripted fake client built from the real SDK types (no network)."""

import base64
import json
from types import SimpleNamespace

import httpx
import pytest
from anthropic.types import Message
from fastapi.testclient import TestClient

from agents.config import Settings
from agents.designer import Hint, Hints
from agents.main import create_app
from agents.models import Answer
from agents.vision import Verdict

from .test_api import FakeCoordinator, login


def message(content: list[dict], stop_reason: str) -> Message:
    return Message.model_validate(
        {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": content,
            "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


class FakeClaude:
    """messages.create replays scripted turns; messages.parse answers by output type."""

    def __init__(self, turns: list[Message], parsed: dict):
        self.turns, self.parsed, self.requests = list(turns), parsed, []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.turns.pop(0)

    async def parse(self, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=self.parsed[kwargs["output_format"]])


def web(request: httpx.Request) -> httpx.Response:
    """The open web, for downloading photos: images exist, an HTML page isn't a photo, the rest is missing."""
    if request.url.path.endswith((".png", ".jpg")):
        return httpx.Response(200, content=b"\x89PNG fake", headers={"content-type": "image/png"})
    if request.url.path.endswith(".html"):
        return httpx.Response(200, text="<html></html>", headers={"content-type": "text/html"})
    return httpx.Response(404)


HINTS = {"phong": "Look up at the tall columns.", "jess": "It's made of stone."}


@pytest.fixture
def setup(tmp_path):
    coord = FakeCoordinator()
    claude = FakeClaude(
        turns=[
            message(
                [
                    {"type": "thinking", "thinking": "", "signature": "sig"},
                    {"type": "tool_use", "id": "t1", "name": "save_profile", "input": {"about": "came with a film camera"}},
                    {"type": "tool_use", "id": "t2", "name": "start_challenge", "input": {"size": 4}},
                ],
                "tool_use",
            ),
            message([{"type": "text", "text": "Started a 4-person challenge! I'll tell you who joins."}], "end_turn"),
        ],
        parsed={
            Answer: Answer(answer="no", note="prefers smaller groups"),
            Hints: Hints(hints=[Hint(agent_id=a, hint=h) for a, h in HINTS.items()]),
            Verdict: Verdict(passed=True, reason="That's the spot, and you're both pointing. Nice!"),
        },
    )
    settings = Settings(
        coordinator_url="http://coordinator", public_url="http://agents", db_path=":memory:", cache_dir=tmp_path,
        use_claude=False, apify_token="", moss_project_id="", moss_project_key="",
    )
    app = create_app(
        settings, claude=claude, coordinator_transport=httpx.MockTransport(coord), web_transport=httpx.MockTransport(web), watch=False
    )
    with TestClient(app) as client:
        yield client, coord, claude


def test_chat_starts_a_challenge_and_keeps_history_append_only(setup):
    client, coord, claude = setup
    login(client)
    out = client.post("/chat/phong", json={"message": "Let's do something with 4 people!"}).json()
    assert out["reply"].startswith("Started a 4-person challenge")
    assert "film camera" in out["profile"]["about"]
    assert ("POST", "/challenges", {"agent_id": "phong", "size": 4}) in coord.calls

    convo = client.app.state.svc.user("phong").convo
    assert [m["role"] for m in convo] == ["user", "assistant", "user", "assistant"]
    assert "saved profile" in convo[0]["content"] and "puzzles" in convo[0]["content"]  # knows its person
    assert convo[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}  # passed back unchanged
    assert [r["tool_use_id"] for r in convo[2]["content"]] == ["t1", "t2"]
    assert not any(r.get("is_error") for r in convo[2]["content"])
    second = claude.requests[1]
    assert second["messages"] is convo and second["output_config"] == {"effort": "low"}
    assert second["extra_body"] == {"fallbacks": "default"}


def test_invitation_answer_comes_from_claude(setup):
    client, _, claude = setup
    q = {"question_id": "q1", "agent_id": "kenji", "question": "join?", "plan": {"window": "Sat 15:30-17:00", "host": "phong", "size": 5}}
    assert client.post("/agents/kenji/question", json=q).json() == {"answer": "no", "free": None, "note": "prefers smaller groups"}
    sent = claude.requests[-1]["messages"][0]["content"]
    assert '"size": 5' in sent and "Kenji" in sent


def test_claude_picks_the_spot_and_checks_the_photo(setup):
    client, coord, claude = setup
    login(client)
    login(client, "Jess", "id-jess")
    match = coord.invite("phong", ["jess"], size=2)
    members = [{"agent_id": "phong", "interests": ["puzzles"]}, {"agent_id": "jess", "interests": ["campus"]}]
    challenge = client.post("/challenge", json={"match": match, "members": members}).json()
    rnd = challenge["round"]
    assert rnd["keys"] == HINTS and rnd["designed_by"] == "Claude + built-in place info"
    spot = next(s for s in client.app.state.svc.spots if s.id == rnd["spot"])
    assert rnd["answer"].startswith(f"{spot.name} · ") and len(rnd["pose"]["cuts"]) == 3  # a 2-person meme
    sent = json.loads(claude.requests[-1]["messages"][0]["content"])
    assert {"agent_id": "jess", "name": "Jess", "interests": ["campus"], "skills": []} in sent["players"]
    assert sent["spot"]["name"] == spot.name and "spots" not in sent  # Claude only writes the hints for this spot
    apify = next(s for s in client.app.state.svc.spots if s.id == "apify_booth")  # only verified booths are spots
    assert {s.id for s in client.app.state.svc.spots} == {"weeping_ladies", "rotunda", "lagoon", "apify_booth"}
    assert "near the snacks" in apify.about and "apify" in apify.giveaways

    match.update(state="confirmed", accepted=["phong", "jess"], challenge=challenge)
    out = client.post("/challenges/m1/check", json={"step": "photo", "agent_id": "jess", "image_base64": "aGk=", "media_type": "image/png"}).json()
    assert out["passed"] and out["reason"].startswith("That's the spot") and out["done"]
    content = claude.requests[-1]["messages"][0]["content"]
    images = [b["source"] for b in content if b["type"] == "image"]
    png = base64.b64encode(b"\x89PNG fake").decode()
    assert images == [{"type": "base64", "media_type": "image/png", "data": png}, {"type": "base64", "media_type": "image/png", "data": "aGk="}]
    assert f"Taken at {spot.name}" in content[-1]["text"]  # the meme is the reference; the check names the spot

    # A photo URL is downloaded and sent inline; one that isn't an image fails instead of counting.
    out = client.post("/challenges/m1/check", json={"step": "photo", "image_url": "https://photos.example/us.png"}).json()
    assert claude.requests[-1]["messages"][0]["content"][3]["source"]["type"] == "base64" and out["passed"]
    out = client.post("/challenges/m1/check", json={"step": "photo", "image_url": "https://photos.example/page.html"}).json()
    assert not out["passed"] and "couldn't open" in out["reason"]
