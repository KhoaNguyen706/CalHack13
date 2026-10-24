from datetime import datetime

import pytest

from agents import designer, personal, timeutil
from agents.designer import Invented, Part, Step
from agents.models import Answer, PhotoCheck, Profile, Question
from agents.service import pretty, slug_id


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
    assert personal.checked(Answer(answer="counter", free="later")).answer == "no"
    assert personal.checked(Answer(answer="counter", free="Sat 15:00-16:00")).free == "Sat 15:00-16:00"
    assert personal.checked(Answer(answer="yes", free="Sat 15:00-16:00")).free is None
    assert "groups of 4" in personal.memory_query(ask(host="phong", size=4))


def invented(parts, steps=1, photo=None):
    return Invented(
        title="Coral Hunt", goal="Find coral things.", connection_hook="Each finds one for a teammate.",
        steps=[Step(task=f"task {i}", check=f"check {i}") for i in range(steps)],
        parts=[Part(agent_id=a, part=p) for a, p in parts.items()], timer_min=15, reference_photo=photo,
    )


def test_designer_validation():
    members = ["linh", "marcus", "priya", "kenji"]
    parts = {m: f"Find one for the next person, {m}." for m in members}
    assert designer.validate(invented(parts), members, set()) is None
    assert designer.validate(invented({**parts, "kenji": ""}), members, set()) == "every part must be 1-280 characters"
    assert designer.validate(invented({"linh": "x"}), members, set()) == "parts must have exactly one entry per member"
    assert designer.validate(invented(parts, steps=0), members, set()) == "1-6 steps, each with a task and a check"
    made_up = invented(parts, photo="https://example.com/made-up.jpg")
    assert designer.validate(made_up, members, set()) == "reference photo isn't one of the scraped photos"
    assert designer.validate(made_up, members, {"https://example.com/made-up.jpg"}) is None


def test_fallback_color_hunt_fits_any_group():
    players = [{"agent_id": a, "name": n} for a, n in (("phong", "Phong"), ("linh", "Linh"))]
    c = designer.fallback(players, "m1")
    rnd = c["round"]
    assert c["group"] == ["phong", "linh"] and rnd["type"] == "photo_challenge"
    assert set(rnd["keys"]) == {"phong", "linh"} and "Linh" in rnd["keys"]["phong"]
    assert [s["id"] for s in rnd["steps"]] == ["s1", "s2", "s3"]  # one find each, then a group photo
    assert all(s["check"] for s in rnd["steps"])


def test_photo_check_needs_one_image():
    with pytest.raises(ValueError):
        PhotoCheck(step="s1")
    with pytest.raises(ValueError):
        PhotoCheck(step="s1", image_url="https://x/y.jpg", image_base64="abc")
    assert PhotoCheck(step="s1", image_url="https://x/y.jpg").media_type == "image/jpeg"
