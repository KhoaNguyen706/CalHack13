import asyncio
from collections import Counter
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient

from agents.designer import ChallengeRequest, DesignedRound, KeyCard, Member, design, fallback, problems
from agents.main import create_app
from agents.sponsors import load_booths, load_sponsors

BOOTHS = load_booths()
SPONSORS = load_sponsors()
MEMBERS = [
    Member(agent_id="linh", role="riddles", skills=["riddles"], interests=["puzzles"]),
    Member(agent_id="marcus", role="map", skills=["map"]),
    Member(agent_id="priya", role="camera", skills=["camera"]),
]
REQUEST = ChallengeRequest(match={"match_id": "m1", "area": "UC Berkeley"}, members=MEMBERS)


def good_round(**changes) -> DesignedRound:
    base = DesignedRound(
        booth_id="spacetime_booth",
        keys=[
            KeyCard(agent_id="linh", key="Their database is also the server, so every player sees changes instantly."),
            KeyCard(agent_id="marcus", key="Head for the registration desk."),
            KeyCard(agent_id="priya", key="Ask them: what's the biggest multiplayer world running on it?"),
        ],
        timer_min=12,
        note="a live-data booth for a puzzle-loving group",
    )
    return base.model_copy(update=changes)


def fake_claude(*results):
    """Returns each result in turn; an Exception is raised instead."""
    calls = []

    async def parse(**kwargs):
        calls.append(kwargs)
        result = results[len(calls) - 1]
        if isinstance(result, Exception):
            raise result
        return SimpleNamespace(stop_reason="end_turn", parsed_output=result)

    return SimpleNamespace(messages=SimpleNamespace(parse=parse), calls=calls)


def run(client, uses=None):
    return asyncio.run(design(REQUEST, BOOTHS, SPONSORS, client, uses if uses is not None else Counter()))


def test_good_round_passes():
    assert problems(good_round(), MEMBERS, BOOTHS) == []


def test_bad_rounds_are_caught():
    leak = good_round(keys=[*good_round().keys[:2], KeyCard(agent_id="priya", key="Ask them at SpacetimeDB: why?")])
    cases = {
        "booth_id": good_round(booth_id="nowhere"),
        "exactly one entry": good_round(keys=good_round().keys[:2]),
        "timer_min": good_round(timer_min=45),
        "gives the sponsor away": leak,
        "Ask them": good_round(keys=[*good_round().keys[:2], KeyCard(agent_id="priya", key="Take a photo.")]),
        "different": good_round(keys=[KeyCard(agent_id=m.agent_id, key="Same key.") for m in MEMBERS]),
    }
    for expected, designed in cases.items():
        assert any(expected in p for p in problems(designed, MEMBERS, BOOTHS)), expected


def test_hand_written_rounds_pass_validation():
    for booth in BOOTHS:
        uses = Counter({b.id: 0 if b.id == booth.id else 1 for b in BOOTHS})
        r = fallback(MEMBERS, BOOTHS, uses)["round"]
        keys = [KeyCard(agent_id=a, key=k) for a, k in r["keys"].items()]
        designed = DesignedRound(booth_id=r["answer"], keys=keys, timer_min=r["timer_min"], note="")
        assert r["answer"] == booth.id and problems(designed, MEMBERS, BOOTHS) == []


def test_claude_round_is_used_when_valid():
    challenge = run(fake_claude(good_round()))
    assert challenge["designer"]["by"] == "claude"
    assert challenge["round"]["answer"] == "spacetime_booth" and challenge["round"]["timer_min"] == 12
    assert set(challenge["round"]["keys"]) == {"linh", "marcus", "priya"}


def test_invalid_round_is_retried_with_the_problems():
    client = fake_claude(good_round(timer_min=45), good_round())
    assert run(client)["designer"]["by"] == "claude"
    assert "timer_min must be between 5 and 20" in client.calls[1]["messages"][0]["content"]


def test_two_invalid_rounds_fall_back_to_hand_written():
    challenge = run(fake_claude(good_round(timer_min=45), good_round(booth_id="nowhere")))
    assert challenge["designer"]["by"] == "fallback" and challenge["round"]["answer"] == "apify_booth"


def test_claude_error_falls_back_and_groups_spread_across_booths():
    uses = Counter()
    first = run(fake_claude(RuntimeError("down")), uses)
    second = run(None, uses)
    assert first["round"]["keys"]["marcus"] == "Their booth is near the snacks."
    assert first["round"]["answer"] != second["round"]["answer"]


def test_only_booths_in_the_match_area_are_offered():
    elsewhere = BOOTHS[0].model_copy(update={"id": "far_booth", "area": "SJSU"})
    client = fake_claude(good_round())
    asyncio.run(design(REQUEST, [elsewhere, *BOOTHS], SPONSORS, client, Counter()))
    assert "far_booth" not in client.calls[0]["messages"][0]["content"]


def test_area_match_ignores_case():
    elsewhere = BOOTHS[0].model_copy(update={"id": "far_booth", "area": "SJSU"})
    client = fake_claude(good_round())
    request = ChallengeRequest(match={"area": "uc berkeley "}, members=MEMBERS)
    asyncio.run(design(request, [elsewhere, *BOOTHS], SPONSORS, client, Counter()))
    content = client.calls[0]["messages"][0]["content"]
    assert "far_booth" not in content and "spacetime_booth" in content


def test_booth_taken_by_a_concurrent_group_is_picked_again():
    uses = Counter()

    async def both():
        gate = asyncio.Event()

        async def slow(**kwargs):
            await gate.wait()
            return SimpleNamespace(stop_reason="end_turn", parsed_output=good_round())

        slow_client = SimpleNamespace(messages=SimpleNamespace(parse=slow))
        first = asyncio.create_task(design(REQUEST, BOOTHS, SPONSORS, slow_client, uses))
        await asyncio.sleep(0)
        second = await design(REQUEST, BOOTHS, SPONSORS, fake_claude(good_round()), uses)
        gate.set()
        return await first, second

    first, second = asyncio.run(both())
    assert second["round"]["answer"] == "spacetime_booth"
    assert first["round"]["answer"] != "spacetime_booth"


def test_api_matches_the_coordinator_contract():
    app = create_app(use_claude=False, transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    with TestClient(app) as client:
        body = {"match": {"match_id": "m1", "members": ["linh", "marcus", "priya"]}, "members": [m.model_dump() for m in MEMBERS]}
        challenge = client.post("/challenge", json=body).json()
    rnd = challenge["round"]
    assert challenge["group"] == ["linh", "marcus", "priya"]
    assert set(rnd["keys"]) == set(challenge["group"])  # what the coordinator's set_challenge checks
    assert rnd["type"] == "split_riddle" and rnd["unlock"] == "all_checked_in + photo"
