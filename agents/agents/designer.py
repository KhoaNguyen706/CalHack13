"""Challenge designer: Claude invents an outdoor challenge for a confirmed group, from who's in it and the place.

The coordinator POSTs {"match", "members"} here on confirm and expects the challenge back. Every step is
proven by one photo (checked later by vision.py), and every player gets a personal part that ties them to a
teammate. `round.keys` carries those parts, one per member, which is what the coordinator validates.
Code checks Claude's challenge (one part per member, 1-6 steps, only real reference photos); anything wrong →
a simple built-in color hunt.
Each player's past ratings (from memory) come along as `past_challenges`, so a "too long" last time makes this
one shorter; `round.tuned` tells that player, and only them, what changed.
"""

import asyncio
import json
import logging
import re

import anthropic
from pydantic import BaseModel

from . import llm
from .place import Place

log = logging.getLogger(__name__)

MAX_PART_CHARS = 280
MAX_SPOT_CHARS = 140
DEFAULT_SPOT = "the main entrance"
COLORS = ("red", "yellow", "blue", "green", "orange", "purple", "pink", "white")

# The group's sign: every phone shows the same color and name full screen, so strangers find each other in a crowd.
SIGN_COLORS = (
    ("Teal", "#2DD4BF"), ("Coral", "#FB7185"), ("Sunny", "#FACC15"), ("Violet", "#A78BFA"),
    ("Lime", "#A3E635"), ("Sky", "#38BDF8"), ("Tangerine", "#FB923C"), ("Pink", "#F472B6"),
)
SIGN_ANIMALS = (("Otter", "🦦"), ("Fox", "🦊"), ("Panda", "🐼"), ("Owl", "🦉"), ("Penguin", "🐧"), ("Koala", "🐨"), ("Frog", "🐸"), ("Whale", "🐳"))

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
- `meeting_point`: where they meet first, before step 1, so strangers can find each other in a crowd. One exact, \
easy-to-find public spot within a 2-minute walk: a named entrance, landmark, statue or a booth whose `location` \
is given. Never a vague area ("near the lawn"). If `suggested_meeting_point` is given, use it as it is.
- `past_challenges` on a player: how they rated earlier challenges, in their words. Learn from it: if one ran too \
long, make this one shorter (a lower `timer_min`, fewer steps); too hard, make it lighter; if they loved a kind of \
challenge, lean that way. Ratings are private: never mention them or who said what in the title, goal, steps or \
anyone else's part.
- `adjustments`: for each player whose feedback you acted on, one short line to that player only, saying what you \
changed for them, e.g. "Kept it to 10 minutes, since your last one ran long." Leave it empty if you changed nothing.
- Plain, warm, short sentences. Title: 2-5 words."""


class Step(BaseModel):
    task: str  # what the group does and photographs
    check: str  # what the photo must show to pass


class Part(BaseModel):
    agent_id: str
    part: str
    hint: str = ""  # a nudge for that player only, shown when they tap "Need a hint?"


class Adjustment(BaseModel):
    agent_id: str
    note: str  # to that player only: what changed because of their past feedback


class Invented(BaseModel):
    title: str
    goal: str  # one sentence for everyone
    connection_hook: str
    steps: list[Step]
    parts: list[Part]
    timer_min: int
    reference_photo: str | None = None
    meeting_point: str = ""  # where the group meets first; empty or too long → the default spot
    adjustments: list[Adjustment] = []


# What in a past rating means "make the next one shorter", for the built-in hunt (Claude reads the words itself).
TOO_LONG = re.compile(r"too long|took forever|dragged|shorter|less time|too many steps", re.I)
SHORT_TIMER_MIN = 10


def comments(past: list[str]) -> str:
    """Just the players' own words from rating memories ('… They said: "too long". …')."""
    return " ".join(m.group(1) for text in past for m in re.finditer(r'They said: "(.*?)"', text))


def meetup(match_id: str, spot: str) -> dict:
    """Where to meet and the group's sign, the same for every player. The sign comes from the match id, not Claude."""
    n = sum(map(ord, match_id or "x"))
    (color, hex_), (animal, emoji) = SIGN_COLORS[n % len(SIGN_COLORS)], SIGN_ANIMALS[(n // len(SIGN_COLORS)) % len(SIGN_ANIMALS)]
    return {"spot": spot, "sign": f"{color} {animal}", "emoji": emoji, "color": hex_}


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


def template(members: list[str], c: Invented, sources: list[str], meet: dict) -> dict:
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
            "meet": meet,  # public: every player sees the same spot and sign
            # Private, like the parts: what changed for a player because of their past ratings.
            "tuned": {
                a.agent_id: a.note.strip()
                for a in c.adjustments
                if a.agent_id in members and 0 < len(a.note.strip()) <= MAX_SPOT_CHARS
            },
        },
    }


def fallback(players: list[dict], match_id: str = "", spot: str = "") -> dict:
    """A color hunt that works for any group of 2+: each person finds one thing for the next.
    Shorter when someone's past feedback said a challenge ran long."""
    color = COLORS[sum(map(ord, match_id or "x")) % len(COLORS)]
    ids = [p["agent_id"] for p in players]
    names = {p["agent_id"]: p["name"] for p in players}
    pairs = list(zip(ids, ids[1:] + ids[:1]))
    long_before = [p["agent_id"] for p in players if TOO_LONG.search(comments(p.get("past_challenges", [])))]
    timer = SHORT_TIMER_MIN if long_before else 15
    adjustments = [Adjustment(agent_id=a, note=f"Kept it to {timer} minutes, since your last one ran long.") for a in long_before]
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
        timer_min=timer,
        adjustments=adjustments,
    )
    return template(ids, c, [], meetup(match_id, spot or DEFAULT_SPOT))


async def design(
    claude: anthropic.AsyncAnthropic | None,
    model: str,
    budget_s: float,
    players: list[dict],
    place: Place,
    when: str,
    match_id: str = "",
    booths: list[dict] | None = None,
    meeting_point: str = "",
) -> tuple[dict, str]:
    """players: [{agent_id, name, interests, skills, past_challenges}]; booths: [{sponsor, what_it_does, location}].
    meeting_point: an organizer's pick (MEETING_POINT); it beats Claude's. Returns (challenge, how)."""
    ids = [p["agent_id"] for p in players]
    default = meeting_point or f"the main entrance of the {place.location}"
    if claude is None or not ids:
        return fallback(players, match_id, default), "built-in color hunt (Claude off)"

    system = SYSTEM.format(n=len(ids), event=place.event, location=place.location, city=place.city, time=when)
    content = json.dumps(
        {
            "players": players,
            "facts_about_the_place": [f.text for f in place.facts],
            "reference_photos": [{"url": i.url, "about": i.about} for i in place.images],
            "sponsor_booths": booths or [],
            **({"suggested_meeting_point": meeting_point} if meeting_point else {}),
        },
        indent=1,
    )
    try:
        c = await asyncio.wait_for(
            llm.parse(claude, model, system=system, content=content, output=Invented, effort="medium"), budget_s
        )
    except Exception as e:
        log.warning("designer fell back: %r", e)
        return fallback(players, match_id, default), "built-in color hunt (designer error)"
    if c is None:
        return fallback(players, match_id, default), "built-in color hunt (declined)"
    if problem := validate(c, ids, {i.url for i in place.images}):
        log.warning("designer challenge rejected: %s", problem)
        return fallback(players, match_id, default), f"built-in color hunt ({problem})"

    sources = sorted({f.source for f in place.facts if f.source} | ({i.source for i in place.images if i.url == c.reference_photo}))
    how = "Claude + scraped place info" if place.scraped else "Claude + built-in place info"
    spot = c.meeting_point.strip()
    if meeting_point or not spot or len(spot) > MAX_SPOT_CHARS:
        spot = default
    return template(ids, c, sources, meetup(match_id, spot)), how
