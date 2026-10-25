import asyncio
from datetime import datetime

import pytest

from agents import designer, personal, timeutil
from agents.designer import Hint, Hints
from agents.models import Answer, PhotoCheck, Profile, Question
from agents.service import pretty, public_round, slug_id


def test_windows():
    assert timeutil.parse("Sat 14:00-16:00") == ("Sat", 840, 960)
    for bad in ("Sat 16:00-14:00", "Saturday 2-4", "Sat 14:00-25:00"):
        with pytest.raises(ValueError):
            timeutil.parse(bad)
    assert timeutil.fits("Sat 14:00-15:00", "Sat 13:00-16:00")
    assert not timeutil.fits("Sat 14:00-15:00", "Sun 13:00-16:00")
    assert timeutil.from_now("UTC", 2, at=datetime(2026, 10, 24, 22, 30)) == "Sat 22:30-23:59"
    assert timeutil.shift("Sat 14:00-16:00", 60, "Fri") == "Fri 15:00-17:00"
    assert timeutil.shift("Sat 14:00-16:00", 9 * 60 + 30, "Fri") is None  # under an hour before midnight
    assert pretty("Sat 14:00-16:00") == "Sat 2:00–4:00pm"
    assert slug_id("Linh Tran!") == "linh-tran" and slug_id("😀") == "player"


def ask(window="Sat 14:00-16:00", **plan):
    return Question(question_id="q1", agent_id="linh", question="join?", plan={"window": window, **plan})


def test_rule_answer_and_checks():
    free = Profile(free="Sat 13:00-17:00")
    assert personal.rule_answer(free, ask()).answer == "yes"
    assert personal.rule_answer(free, ask("Sat 18:00-19:00")).answer == "no"
    assert personal.rule_answer(Profile(), ask()).answer == "yes"  # nothing known: open to it
    assert personal.checked(Answer(answer="counter", free="later"), free, ask()) is None  # rules decide
    assert personal.checked(Answer(answer="counter", free="Sat 15:00-16:00"), free, ask()).free == "Sat 15:00-16:00"
    assert personal.checked(Answer(answer="yes", free="Sat 15:00-16:00"), free, ask()).free is None
    assert "groups of 4" in personal.memory_query(ask(host="phong", size=4))


SPOTS, POSES = designer.load_spots(), designer.load_poses()


def test_designer_validation():
    members = ["linh", "marcus", "priya"]
    spots = {s.id: s for s in SPOTS}
    hints = {"linh": "It has a huge dome on top.", "marcus": "It was built in 1915.", "priya": "It's in the middle."}

    def check(hints, spot="rotunda"):
        return designer.validate(Hints(hints=[Hint(agent_id=a, hint=h) for a, h in hints.items()]), members, spots[spot])

    assert check(hints) is None
    assert check({"linh": "It has a huge dome on top."}) == "hints must have exactly one entry per member"
    assert check({**hints, "priya": "x" * 91}) == "every hint must be 1-90 characters"
    assert check({**hints, "priya": "it has a huge dome on top."}) == "hints must all be different"
    assert check({**hints, "priya": "Go to the Rotunda."}) == "a hint names the spot"
    assert check({**hints, "priya": "Ducks swim in the pond."}, spot="lagoon") is None  # plain words are fine
    assert check({**hints, "priya": "It's the lagoon."}, spot="lagoon") == "a hint names the spot"
    assert designer.fitting(POSES, 3) == [p for p in POSES if p.people == 3]
    assert [p.people for p in designer.fitting(POSES, 6)] == [5]  # no 6-person meme: the closest


def test_hand_picked_spots_and_poses_are_usable():
    assert {p.people for p in POSES} >= {2, 3, 4, 5}
    for p in POSES:
        assert len(p.cuts) == p.people + 1 and p.cuts[0] == 0 and p.cuts[-1] == 1 and p.cuts == sorted(p.cuts)
        assert p.image.startswith("https://") and 0 <= p.top < 1
    for s in SPOTS:
        assert len({h.casefold() for h in s.hints}) >= 5 and all(len(h) <= designer.MAX_HINT_CHARS for h in s.hints)
        assert not any(designer.reveals(h, s) for h in s.hints)


def test_fallback_fits_any_group():
    for n in (2, 3, 4, 5):
        players = [{"agent_id": f"p{i}", "name": f"P{i}"} for i in range(n)]
        c = designer.fallback(players, SPOTS, POSES, "m1")
        rnd = c["round"]
        assert c["group"] == [p["agent_id"] for p in players] and rnd["type"] == "spot_and_pose"
        assert set(rnd["keys"]) == set(c["group"]) and len(set(rnd["keys"].values())) == n  # a different hint each
        assert sorted(rnd["pieces"].values()) == list(range(n)) and len(rnd["pose"]["cuts"]) == n + 1  # a piece each
        assert rnd["reference_photo"] == rnd["pose"]["image"] and [s["id"] for s in rnd["steps"]] == ["photo"]
        spot = next(s for s in SPOTS if s.id == rnd["spot"])
        assert rnd["answer"].startswith(spot.name) and spot.name in rnd["steps"][0]["check"]
    assert designer.fallback(players, SPOTS, POSES, "m1") == c  # the same match always gets the same round
    six = [{"agent_id": f"p{i}", "name": f"P{i}"} for i in range(6)]  # no 6-person meme: equal pieces of the closest
    assert designer.fallback(six, SPOTS, POSES, "m1")["round"]["pose"]["cuts"] == [0, 0.1667, 0.3333, 0.5, 0.6667, 0.8333, 1]
    assert designer.fallback([], SPOTS, POSES, "m1")["round"]["keys"] == {}  # an empty group doesn't crash


def test_photo_check_needs_one_image():
    with pytest.raises(ValueError):
        PhotoCheck(step="s1")
    with pytest.raises(ValueError):
        PhotoCheck(step="s1", image_url="https://x/y.jpg", image_base64="abc")
    assert PhotoCheck(step="s1", image_url="https://x/y.jpg").media_type == "image/jpeg"


def test_meetup_sign_is_the_same_for_the_whole_group():
    a, b = designer.meetup("m1", "the rotunda steps"), designer.meetup("m1", "the rotunda steps")
    assert a == b and a["spot"] == "the rotunda steps"
    assert a["sign"].split()[0] in {c for c, _ in designer.SIGN_COLORS} and a["color"].startswith("#") and a["emoji"]
    assert len({designer.meetup(f"m{i}", "x")["sign"] for i in range(40)}) > 8  # groups get different signs


def test_fallback_meets_at_the_default_spot():
    players = [{"agent_id": a, "name": a.title()} for a in ("phong", "linh")]
    assert designer.fallback(players, SPOTS, POSES, "m1")["round"]["meet"]["spot"] == designer.DEFAULT_SPOT
    assert designer.fallback(players, SPOTS, POSES, "m1", "the fountain")["round"]["meet"]["spot"] == "the fountain"
    for i in range(20):  # never sends a group to the spot it meets at
        assert designer.fallback(players, SPOTS, POSES, f"m{i}", "the lagoon")["round"]["spot"] != "lagoon"


def design_with(monkeypatch, spot_from_claude: str, organizer: str = "", spots=None):
    from agents.place import Place

    out = Hints(hints=[Hint(agent_id="phong", hint="Look up."), Hint(agent_id="linh", hint="It's made of stone.")], meeting_point=spot_from_claude)

    async def parse(*_, **__):
        return out

    monkeypatch.setattr(designer.llm, "parse", parse)
    place = Place(event="Cal Hacks 13", location="Palace of Fine Arts", city="San Francisco", hour="2026-10-24T14", facts=[], images=[], scraped=False, fetched_at=0)
    players = [{"agent_id": a, "name": a.title()} for a in ("phong", "linh")]
    rotunda = [s for s in SPOTS if s.id == "rotunda"]
    challenge, _ = asyncio.run(designer.design(object(), "m", 5, players, place, "Saturday", spots or rotunda, POSES, "m1", organizer))
    return challenge["round"]["meet"]["spot"]


def test_meeting_point_comes_from_the_organizer_then_claude_then_the_entrance(monkeypatch):
    assert design_with(monkeypatch, "the Apify booth, near the snacks") == "the Apify booth, near the snacks"
    assert design_with(monkeypatch, "the Apify booth", organizer="the main stage") == "the main stage"
    assert design_with(monkeypatch, "") == "the main entrance of the Palace of Fine Arts"
    assert design_with(monkeypatch, "x" * 200) == "the main entrance of the Palace of Fine Arts"
    assert design_with(monkeypatch, "the rotunda steps") == "the main entrance of the Palace of Fine Arts"  # the answer
    assert design_with(monkeypatch, "The Lyon Street doors") == "the Lyon Street doors"  # it follows "Meet at"


def test_past_feedback_shortens_the_fallback_and_tells_only_that_player():
    said_long = 'Played "Spot & Pose" with Linh (2 people) and rated it 2/5. They said: "fun but too long". Wants to meet them again.'
    loved_it = 'Played "Spot & Pose" with Phong (2 people) and rated it 5/5. They said: "loved it". Wants to meet them again.'
    players = [
        {"agent_id": "phong", "name": "Phong", "past_challenges": [said_long]},
        {"agent_id": "linh", "name": "Linh", "past_challenges": [loved_it]},
    ]
    rnd = designer.fallback(players, SPOTS, POSES, "m2")["round"]
    assert rnd["timer_min"] == designer.SHORT_TIMER_MIN
    assert rnd["tuned"] == {"phong": "Kept it to 10 minutes, since your last one ran long."}
    assert "tuned" not in public_round(rnd)  # private, like the hints

    # Only the players' own words count: a "long" elsewhere in the memory (a name, a title) doesn't.
    plain = [{"agent_id": "x", "name": "X", "past_challenges": ['Played "The Long Walk" and rated it 4/5.']}]
    assert designer.fallback(plain, SPOTS, POSES, "m3")["round"]["timer_min"] == 15


def test_claude_adjustments_reach_only_known_players(monkeypatch):
    from agents.place import Place

    out = Hints(
        hints=[Hint(agent_id="phong", hint="Look up."), Hint(agent_id="linh", hint="It's made of stone.")],
        timer_min=10,
        adjustments=[
            designer.Adjustment(agent_id="phong", note="Kept it short, since your last one ran long."),
            designer.Adjustment(agent_id="stranger", note="Not in this group."),
        ],
    )

    async def parse(*_, **__):
        return out

    monkeypatch.setattr(designer.llm, "parse", parse)
    place = Place(event="Cal Hacks 13", location="Palace of Fine Arts", city="San Francisco", hour="2026-10-24T14", facts=[], images=[], scraped=False, fetched_at=0)
    players = [{"agent_id": a, "name": a.title()} for a in ("phong", "linh")]
    rotunda = [s for s in SPOTS if s.id == "rotunda"]
    rnd = asyncio.run(designer.design(object(), "m", 5, players, place, "Saturday", rotunda, POSES, "m1"))[0]["round"]
    assert rnd["tuned"] == {"phong": "Kept it short, since your last one ran long."} and rnd["timer_min"] == 10


def test_the_chat_agent_never_sees_the_spot():
    import json
    from types import SimpleNamespace

    from agents import chat

    status = {
        "match": {"state": "playing", "members": ["phong"], "window": "Sat 14:00-16:00"}, "checked_in": True,
        "challenge": {"title": "Spot & Pose", "spot_name": "the Rotunda", "spot_about": "The big dome."},
        "my_part": "It has a huge dome on top.", "progress": None,
    }

    async def get_status(user):
        return status

    svc = SimpleNamespace(status=get_status, display=lambda a: a.title())
    out = json.loads(asyncio.run(chat._my_challenge(svc, None, {})))
    assert out["challenge"] == {"title": "Spot & Pose"} and out["my_part"] == "It has a huge dome on top."


def test_geo_lookup_caches_and_survives_failure(tmp_path):
    import httpx

    from agents.config import Settings
    from agents.geo import GeoService, parse_coords

    assert parse_coords("37.8, -122.4") == (37.8, -122.4)
    assert parse_coords("north of the lagoon") is None and parse_coords("91,0") is None

    calls = []

    def web(request):
        calls.append(request)
        return httpx.Response(503)

    async def where():
        async with httpx.AsyncClient(transport=httpx.MockTransport(web)) as http:
            return await GeoService(Settings(cache_dir=tmp_path), http).where()

    # The lookup fails: the address text still opens in a map app, there's just no pin.
    assert asyncio.run(where()) == {"address": "Palace of Fine Arts, San Francisco", "lat": None, "lng": None}
    (tmp_path / "geo.json").write_text('{"query": "Palace of Fine Arts, San Francisco", "address": "3601 Lyon St", "lat": 1.0, "lng": 2.0}')
    assert asyncio.run(where()) == {"address": "3601 Lyon St", "lat": 1.0, "lng": 2.0}  # from the cache
    assert len(calls) == 1  # the cached run didn't call OpenStreetMap
