import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agents import personas
from agents.main import create_app
from agents.models import Persona, Question
from agents.persona_agent import Decision, decide, rule_decide

PERSONAS = personas.load()


def ask(agent_id: str, window: str = "Sat 14:00-16:00") -> Question:
    return Question(question_id="q1", agent_id=agent_id, question="Does it fit?", plan={"window": window})


def fake_claude(result=None, error: Exception | None = None):
    async def parse(**_):
        if error:
            raise error
        return SimpleNamespace(stop_reason="end_turn", parsed_output=result)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def run_decide(agent_id: str, client, window: str = "Sat 14:00-16:00"):
    return asyncio.run(decide(PERSONAS[agent_id], ask(agent_id, window), client))


def test_personas_load_and_keep_the_mix():
    assert 15 <= len(PERSONAS) <= 20
    assert {"linh", "marcus", "priya"} <= set(PERSONAS)
    assert len({p.area for p in PERSONAS.values()}) == 2
    assert any(not p.open_to_invites for p in PERSONAS.values())
    assert any(p.max_minutes for p in PERSONAS.values())


def test_checkin_sends_coarse_fields_only():
    body = PERSONAS["marcus"].checkin("http://agents/personas/marcus/ask")
    assert set(body) == {"agent_id", "free", "interests", "skills", "group_pref", "area", "callback_url"}


@pytest.mark.parametrize(
    "agent_id, window, answer, free",
    [
        ("linh", "Sat 14:00-16:00", "yes", None),
        ("marcus", "Sat 14:00-16:00", "counter", "Sat 14:00-15:30"),  # max 90 minutes
        ("linh", "Sat 15:00-17:00", "counter", "Sat 15:00-16:00"),  # only part of the window works
        ("linh", "Sat 15:30-17:00", "no", None),  # 30 min overlap
        ("linh", "Sun 14:00-16:00", "no", None),
        ("tomas", "Sat 18:30-20:00", "no", None),  # not open to invites
    ],
)
def test_rule_decide(agent_id, window, answer, free):
    reply = rule_decide(PERSONAS[agent_id], ask(agent_id, window))
    assert (reply.answer, reply.free) == (answer, free)


def test_claude_decision_is_used_when_valid():
    reply = run_decide("marcus", fake_claude(Decision(answer="counter", free="Sat 14:30-16:00", note="later start")))
    assert (reply.answer, reply.free, reply.note) == ("counter", "Sat 14:30-16:00", "later start")


@pytest.mark.parametrize(
    "decision",
    [
        Decision(answer="counter", free="Sat 12:00-14:00", note="outside free time"),
        Decision(answer="counter", free="Sat 15:30-16:00", note="too short"),
        Decision(answer="counter", free="2pm", note="bad format"),
        Decision(answer="counter", note="no window"),
    ],
)
def test_bad_claude_counter_falls_back_to_rules(decision):
    reply = run_decide("marcus", fake_claude(decision))
    assert (reply.answer, reply.free) == ("counter", "Sat 14:00-15:30")


def test_claude_cannot_accept_for_someone_not_taking_plans():
    assert run_decide("tomas", fake_claude(Decision(answer="yes", note="sure")), "Sat 18:30-20:00").answer == "no"


def test_claude_cannot_counter_for_someone_not_taking_plans():
    decision = Decision(answer="counter", free="Sat 18:30-19:30", note="shorter")
    assert run_decide("tomas", fake_claude(decision), "Sat 18:30-20:00").answer == "no"


def test_claude_failure_falls_back_to_rules():
    assert run_decide("linh", fake_claude(error=RuntimeError("down"))).answer == "yes"


def test_api_checkin_all_and_ask():
    seen = []

    def coordinator(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={})

    app = create_app(use_claude=False, public_url="http://agents:8100", transport=httpx.MockTransport(coordinator))
    with TestClient(app) as client:
        assert client.post("/personas/checkin-all").json() == {"checked_in": len(PERSONAS)}
        assert {"callback_url": "http://agents:8100/personas/linh/ask"}.items() <= seen[[s["agent_id"] for s in seen].index("linh")].items()
        assert all("style" not in s for s in seen)

        # The broker's callback body, answered inline.
        q = {"question_id": "q1", "agent_id": "marcus", "question": "fits?", "plan": {"window": "Sat 14:00-16:00"}}
        assert client.post("/personas/marcus/ask", json=q).json() == {
            "answer": "counter",
            "free": "Sat 14:00-15:30",
            "note": "can do 90 minutes",
        }
        assert client.post("/personas/nobody/ask", json=q).status_code == 404


def test_persona_rejects_bad_window():
    with pytest.raises(ValueError):
        Persona(agent_id="x", free="2pm", area="x")
