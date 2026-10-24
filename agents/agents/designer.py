"""Challenge designer: Claude invents an outdoor challenge for a confirmed group, from who's in it and the place.

The coordinator POSTs {"match", "members"} here on confirm and expects the challenge back. Every step is
proven by one photo (checked later by vision.py), and every player gets a personal part that ties them to a
teammate. `round.keys` carries those parts, one per member, which is what the coordinator validates.
Code checks Claude's challenge (one part per member, 1-6 steps, only real reference photos); anything wrong →
a simple built-in color hunt.
"""

import asyncio
import json
import logging

import anthropic
from pydantic import BaseModel

from . import llm
from .place import Place

log = logging.getLogger(__name__)

MAX_PART_CHARS = 280
COLORS = ("red", "yellow", "blue", "green", "orange", "purple", "pink", "white")

SYSTEM = """You invent one short outdoor challenge for {n} people who just met at {event} \
({location}, {city}). It is {time}. The point: strangers start talking, laugh, and want to meet again.

Be creative. Invent something new for these people, this place and this hour. For the style, some examples:
- Find this spot: show a tightly cropped photo of a nearby spot; the group walks around to find it and match \
the angle. They have to split up and coordinate.
- Checkpoint run: visit three nearby landmarks and take a group photo at each; at each stop a different person \
answers a question.
- Recreate the reference: show an old or famous photo of a nearby spot; the group recreates the angle and pose, \
directing each other.
- Highest view: find the highest viewpoint within a 10-minute walk; the person who loves views leads.
- Color hunt: find five things in one color; each person finds one for a teammate.
- Personal theme hunt: find something that represents a teammate, from their interests ("something for the \
person who loves film cameras"); the teammate says if it fits.
- Alphabet hunt: objects starting with each letter of a teammate's name, so people learn names fast.
- Shape hunt: a circle, a triangle and a star in the real world, not printed. Light and easy for shy groups.
- Nature find: a bird, a flower and a leaf bigger than your hand.

Rules:
- 10 to 20 minutes, on foot, public places only, free, nothing risky. If it's dark, keep to lit, busy areas.
- Every step is proven by ONE photo. `check` says exactly what the photo must show for an AI looking at it \
to pass it. Make checks about places and objects, never about how a person looks.
- No strangers' faces in photos; photos of the players themselves are fine. Ask owners before photographing pets.
- Connection hook: it only works if they talk to each other. Give every player a personal part that involves \
a teammate by name (`parts`, exactly one per player, using their agent_id).
- Give every part a `hint`: one short nudge for that player if they get stuck (where to look, what to ask). It \
helps without giving the answer away, and it is only shown to that player when they ask for it.
- Use what you know: the players' first names and interests, and the facts about the place. If a step needs \
a reference photo, put one URL from `reference_photos` in `reference_photo`, exactly as given; otherwise leave \
it empty. Never invent URLs.
- Sponsor booths at the venue (`sponsor_booths`) can be spots too, e.g. "ask the sponsor a question, then a group \
photo there". Only send people to a booth whose `location` is given; never make one up.
- Refer to players by name or "they"; never guess anyone's gender.
- Plain, warm, short sentences. Title: 2-5 words."""


class Step(BaseModel):
    task: str  # what the group does and photographs
    check: str  # what the photo must show to pass


class Part(BaseModel):
    agent_id: str
    part: str
    hint: str = ""  # a nudge for that player only, shown when they tap "Need a hint?"


class Invented(BaseModel):
    title: str
    goal: str  # one sentence for everyone
    connection_hook: str
    steps: list[Step]
    parts: list[Part]
    timer_min: int
    reference_photo: str | None = None


def validate(c: Invented, members: list[str], photos: set[str]) -> str | None:
    """Why the challenge can't be used, or None when it's fine."""
    parts = {p.agent_id: p.part.strip() for p in c.parts}
    if set(parts) != set(members) or len(c.parts) != len(members):
        return "parts must have exactly one entry per member"
    if any(not p or len(p) > MAX_PART_CHARS for p in parts.values()):
        return "every part must be 1-280 characters"
    if any(len(p.hint.strip()) > MAX_PART_CHARS for p in c.parts):
        return "every hint must be at most 280 characters"
    if not 1 <= len(c.steps) <= 6 or any(not s.task.strip() or not s.check.strip() for s in c.steps):
        return "1-6 steps, each with a task and a check"
    if c.reference_photo and c.reference_photo not in photos:
        return "reference photo isn't one of the scraped photos"
    return None


def template(members: list[str], c: Invented, sources: list[str]) -> dict:
    return {
        "group": members,
        "round": {
            "type": "photo_challenge",
            "title": c.title.strip(),
            "goal": c.goal.strip(),
            "connection_hook": c.connection_hook.strip(),
            "steps": [{"id": f"s{i}", "task": s.task.strip(), "check": s.check.strip()} for i, s in enumerate(c.steps, 1)],
            "keys": {p.agent_id: p.part.strip() for p in c.parts},  # each player's own part
            "hints": {p.agent_id: p.hint.strip() for p in c.parts if p.hint.strip()},  # private, like the parts
            "reference_photo": c.reference_photo or None,
            "answer": c.title.strip(),  # the game board reveals it at the end ("challenge complete! answer: …")
            "unlock": "every step's photo passes its check",
            "timer_min": max(5, min(30, c.timer_min)),
            "sources": sources,
        },
    }


def fallback(players: list[dict], match_id: str = "") -> dict:
    """A color hunt that works for any group of 2+: each person finds one thing for the next."""
    color = COLORS[sum(map(ord, match_id or "x")) % len(COLORS)]
    ids = [p["agent_id"] for p in players]
    names = {p["agent_id"]: p["name"] for p in players}
    pairs = list(zip(ids, ids[1:] + ids[:1]))
    c = Invented(
        title=f"{color.title()} Hunt",
        goal=f"Find {color} things around you, one for each teammate, then take a group photo with them.",
        connection_hook="Each person picks something for a teammate and tells them why.",
        steps=[Step(task=f"{names[a]} finds something {color} for {names[b]}.", check=f"A clearly {color} object is the main subject.") for a, b in pairs]
        + [Step(task=f"Group photo with all your {color} finds.", check=f"Several people together, holding or next to {color} objects.")],
        parts=[
            Part(
                agent_id=a,
                part=f"Find something {color} that {names[b]} would like, and tell them why you picked it.",
                hint=f"Ask {names[b]} what they're into first. Then look at signs, flowers, bikes, bags and doors.",
            )
            for a, b in pairs
        ],
        timer_min=15,
    )
    return template(ids, c, [])


async def design(
    claude: anthropic.AsyncAnthropic | None,
    model: str,
    budget_s: float,
    players: list[dict],
    place: Place,
    when: str,
    match_id: str = "",
    booths: list[dict] | None = None,
) -> tuple[dict, str]:
    """players: [{agent_id, name, interests, skills}]; booths: [{sponsor, what_it_does, location}].
    Returns (challenge, how) where how says who wrote it."""
    ids = [p["agent_id"] for p in players]
    if claude is None or not ids:
        return fallback(players, match_id), "built-in color hunt (Claude off)"

    system = SYSTEM.format(n=len(ids), event=place.event, location=place.location, city=place.city, time=when)
    content = json.dumps(
        {
            "players": players,
            "facts_about_the_place": [f.text for f in place.facts],
            "reference_photos": [{"url": i.url, "about": i.about} for i in place.images],
            "sponsor_booths": booths or [],
        },
        indent=1,
    )
    try:
        c = await asyncio.wait_for(
            llm.parse(claude, model, system=system, content=content, output=Invented, effort="medium"), budget_s
        )
    except Exception as e:
        log.warning("designer fell back: %r", e)
        return fallback(players, match_id), "built-in color hunt (designer error)"
    if c is None:
        return fallback(players, match_id), "built-in color hunt (declined)"
    if problem := validate(c, ids, {i.url for i in place.images}):
        log.warning("designer challenge rejected: %s", problem)
        return fallback(players, match_id), f"built-in color hunt ({problem})"

    sources = sorted({f.source for f in place.facts if f.source} | ({i.source for i in place.images if i.url == c.reference_photo}))
    how = "Claude + scraped place info" if place.scraped else "Claude + built-in place info"
    return template(ids, c, sources), how
