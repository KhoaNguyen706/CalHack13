import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from coordinator.matching import ClaudePick, RoleAssignment, candidate_groups, pick, score_group, validate
from coordinator.models import Agent, CheckIn, parse_free

SEEDS = json.loads((Path(__file__).parent.parent / "seeds" / "personas.json").read_text(encoding="utf-8"))


def agent(agent_id, free="Sat 14:00-16:00", skills=(), interests=(), area="UC Berkeley", pref="small"):
    return Agent(agent_id=agent_id, free=free, skills=list(skills), interests=list(interests), area=area, group_pref=pref)


def test_parse_free():
    assert parse_free("Sat 14:00-16:00") == ("Sat", 840, 960)
    for bad in ("Sat 16:00-14:00", "Saturday 2-4", "Sat 14:00-25:00"):
        with pytest.raises(ValueError):
            parse_free(bad)


def test_gateway_drops_identifying_fields():
    c = CheckIn.model_validate(
        {"agent_id": "linh", "free": "Sat 14:00-16:00", "area": "UC Berkeley", "name": "Linh Tran", "lat": 37.87}
    )
    assert "name" not in c.model_dump() and "lat" not in c.model_dump()


def test_filters():
    a, b = agent("a"), agent("b")
    assert score_group([a, b, agent("c", area="SJSU")]) is None  # different area
    assert score_group([a, b, agent("c", free="Sat 15:30-18:00")]) is None  # 30 min overlap
    assert score_group([a, b, agent("c", free="Sun 14:00-16:00")]) is None  # different day
    assert score_group([a, b, agent("c", pref="large")]) is None
    assert score_group([a, b, agent("c", free="Sat 15:00-18:00")]).overlap_min == 60


def test_complementary_skills_score_highest():
    puzzle, nav, cam = agent("p", skills=["riddles"]), agent("n", skills=["map"]), agent("c", skills=["camera"])
    best = candidate_groups([puzzle, nav, cam, agent("p2", skills=["puzzles"])])[0]
    assert set(best.members) == {"p", "n", "c"}
    assert best.roles == {"p": "riddles", "n": "map", "c": "camera"}


def test_tie_breaks_on_longer_overlap():
    base = [agent("a", free="Sat 12:00-18:00"), agent("b", free="Sat 12:00-18:00")]
    short, long_ = agent("s", free="Sat 12:00-13:00"), agent("l", free="Sat 12:00-15:00")
    assert "l" in candidate_groups(base + [short, long_])[0].members


def test_seeded_demo_trio_is_the_top_group():
    best = candidate_groups([Agent(**p) for p in SEEDS])[0]
    assert best.members == ("linh", "marcus", "priya")
    assert best.roles == {"linh": "riddles", "marcus": "map", "priya": "camera"}


def fake_claude(result):
    async def parse(**_):
        return SimpleNamespace(stop_reason="end_turn", parsed_output=result)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse))


def run_pick(client):
    agents = {a.agent_id: a for a in (agent("p", skills=["riddles"]), agent("n", skills=["map"]), agent("c", skills=["camera"]))}
    return asyncio.run(pick(candidate_groups(list(agents.values())), agents, client))


def roles(**kw):
    return [RoleAssignment(agent_id=k, role=v) for k, v in kw.items()]


def test_claude_pick_is_used_when_valid():
    group, reason, by = run_pick(fake_claude(ClaudePick(candidate=0, roles=roles(p="map", n="riddles", c="camera"), reason="ok")))
    assert by == "claude" and group.roles["p"] == "map" and reason == "ok"


@pytest.mark.parametrize(
    "bad",
    [
        ClaudePick(candidate=7, roles=roles(p="riddles", n="map", c="camera"), reason="x"),  # not shortlisted
        ClaudePick(candidate=0, roles=roles(p="riddles", n="riddles", c="camera"), reason="x"),  # duplicate role
        ClaudePick(candidate=0, roles=roles(p="riddles", n="map", z="camera"), reason="x"),  # wrong member
        None,  # refusal / unparseable
    ],
)
def test_invalid_claude_pick_falls_back_to_rules(bad):
    group, _, by = run_pick(fake_claude(bad))
    assert by == "rules" and group.roles == {"p": "riddles", "n": "map", "c": "camera"}


def test_claude_error_falls_back_to_rules():
    async def boom(**_):
        raise RuntimeError("network down")

    _, reason, by = run_pick(SimpleNamespace(messages=SimpleNamespace(parse=boom)))
    assert by == "rules" and "unavailable" in reason


def test_validate_keeps_rule_window():
    cands = candidate_groups([agent("p"), agent("n"), agent("c")])
    v = validate(ClaudePick(candidate=0, roles=roles(p="camera", n="map", c="riddles"), reason=""), cands)
    assert v.window == cands[0].window
