"""Personal agents' decisions (rules first written for the persona agents) and the old /personas paths."""

import asyncio
from types import SimpleNamespace

import pytest

from agents.main import load_contexts
from agents.models import Answer, Question
from agents.personal import decide, rule_answer

from .test_api import FakeCoordinator, make_client

CONTEXTS = {c.id: c for c in load_contexts()}


def ask(agent_id: str, window: str = "Sat 14:00-16:00") -> Question:
    return Question(question_id="q1", agent_id=agent_id, question="Want to join?", plan={"window": window})


def fake_claude(result=None, error: Exception | None = None):
    async def parse(**_):
        if error:
            raise error
        return SimpleNamespace(stop_reason="end_turn", parsed_output=result)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def run_decide(agent_id: str, client, window: str = "Sat 14:00-16:00") -> Answer:
    profile = CONTEXTS[agent_id].profile
    return asyncio.run(decide(client, "claude-opus-5-5", 5, agent_id.title(), profile, [], ask(agent_id, window)))


def test_contexts_load_and_keep_the_mix():
    assert len(CONTEXTS) == 20 and {"linh", "marcus", "priya"} <= set(CONTEXTS)
    assert {c.area for c in CONTEXTS.values()} == {None, "SJSU"}  # None = the event's area
    assert [c.id for c in CONTEXTS.values() if not c.open_to_invites] == ["tomas"]
    assert CONTEXTS["marcus"].max_minutes == 90 and CONTEXTS["linh"].style


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
def test_rule_answer(agent_id, window, answer, free):
    reply = rule_answer(CONTEXTS[agent_id].profile, ask(agent_id, window))
    assert (reply.answer, reply.free) == (answer, free)


def test_someone_free_from_now_only_caps_the_length():
    profile = CONTEXTS["marcus"].profile.model_copy(update={"free": None})  # a real person: free from whenever
    assert (rule_answer(profile, ask("marcus")).answer, rule_answer(profile, ask("marcus")).free) == ("counter", "Sat 14:00-15:30")


def test_claude_decision_is_used_when_valid():
    reply = run_decide("marcus", fake_claude(Answer(answer="counter", free="Sat 14:30-16:00", note="later start")))
    assert (reply.answer, reply.free, reply.note) == ("counter", "Sat 14:30-16:00", "later start")


@pytest.mark.parametrize(
    "decision",
    [
        Answer(answer="counter", free="Sat 12:00-14:00", note="outside free time"),
        Answer(answer="counter", free="Sat 15:30-16:00", note="too short"),
        Answer(answer="counter", free="2pm", note="bad format"),
        Answer(answer="counter", note="no window"),
    ],
)
def test_bad_claude_counter_falls_back_to_rules(decision):
    reply = run_decide("marcus", fake_claude(decision))
    assert (reply.answer, reply.free) == ("counter", "Sat 14:00-15:30")


def test_claude_cannot_accept_or_counter_for_someone_not_taking_plans():
    assert run_decide("tomas", fake_claude(Answer(answer="yes", note="sure")), "Sat 18:30-20:00").answer == "no"
    shorter = Answer(answer="counter", free="Sat 18:30-19:30", note="shorter")
    assert run_decide("tomas", fake_claude(shorter), "Sat 18:30-20:00").answer == "no"


def test_claude_failure_falls_back_to_rules():
    assert run_decide("linh", fake_claude(error=RuntimeError("down"))).answer == "yes"


def test_old_persona_paths_still_work(tmp_path):
    coord = FakeCoordinator()
    with make_client(coord, tmp_path) as client:
        assert client.post("/personas/checkin-all").json()["seeded"] == 20
        marcus = coord.agents["marcus"]
        assert marcus["callback_url"] == "http://agents/agents/marcus/question"
        assert not {"style", "max_minutes", "open_to_invites", "bio"} & set(marcus)  # coarse fields only
        q = {"question_id": "q1", "agent_id": "marcus", "question": "fits?", "plan": {"window": marcus["free"]}}
        assert client.post("/personas/marcus/ask", json=q).json() == {
            "answer": "counter", "free": "Sat 13:00-14:30", "note": "can do 90 minutes",
        }
        assert {c["id"] for c in client.get("/personas").json()} == set(CONTEXTS)
