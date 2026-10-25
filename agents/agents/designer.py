"""Challenge designer: Spot & Pose. The coordinator POSTs {"match", "members"} here on confirm and expects the
challenge back.

The group meets first. Then every player gets one hint for the spot, a short plain sentence: only all the hints
together say where to go. When they're all there and have tapped "We're here", every phone shows its part of a
famous meme picture; the parts together show the pose. The group takes one photo there doing the pose, which Claude
checks (vision.py). The spot and the meme come from hand-picked lists (data/spots.json, verified sponsor booths,
data/poses.json), picked at random per match so groups spread out; Claude writes the hints. Code checks them (one
per member, none naming the spot); anything wrong → the spot's hand-written hints.
`round.keys` carries the hints, one per member, which is what the coordinator validates. `round.meet` is where the
group meets first and the sign every phone shows, so strangers find each other in a crowd.
Each player's past ratings (from memory) come along as `past_challenges`, so a "too long" last time makes this one
shorter; `round.tuned` tells that player, and only them, what changed.
"""

import asyncio
import json
import logging
import random
import re
from pathlib import Path

import anthropic
from pydantic import BaseModel

from . import llm
from .place import Place

log = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
MAX_HINT_CHARS = 90
MAX_SPOT_CHARS = 140
DEFAULT_SPOT = "the main entrance"
TIMER_MIN = 15
SHORT_TIMER_MIN = 10
# What in a past rating means "make the next one shorter", for the fallback (Claude reads the words itself).
TOO_LONG = re.compile(r"too long|took forever|dragged|shorter|less time|too many steps", re.I)
TITLE = "Spot & Pose"
GOAL = (
    "Meet your group, then put your hints together to find the spot. When you're all there, tap We're here to "
    "unlock the pose, then take a group photo doing it."
)

# The group's sign: every phone shows the same color and name full screen, so strangers find each other in a crowd.
SIGN_COLORS = (
    ("Teal", "#2DD4BF"), ("Coral", "#FB7185"), ("Sunny", "#FACC15"), ("Violet", "#A78BFA"),
    ("Lime", "#A3E635"), ("Sky", "#38BDF8"), ("Tangerine", "#FB923C"), ("Pink", "#F472B6"),
)
SIGN_ANIMALS = (("Otter", "🦦"), ("Fox", "🦊"), ("Panda", "🐼"), ("Owl", "🦉"), ("Penguin", "🐧"), ("Koala", "🐨"), ("Frog", "🐸"), ("Whale", "🐳"))

SYSTEM = """You write the clues for a short game for {n} people who just met at {event} ({location}, {city}). \
It is {time}.

Every player gets one hint about one spot nearby (`spot`). They share their hints to work out where to go.

Write `hints`: exactly one per player (by agent_id). Each is one short, plain sentence anyone gets at a glance, under \
12 words, like "It has a huge dome on top." or "Ducks swim there." Every hint gives a different clue, and together \
they make it easy to tell where to go. Never use the spot's name or its `giveaways`. Use what's true about it \
(`about`, `facts_about_the_place`).

Also pick `meeting_point`: where they meet first, before the hints. One exact, easy-to-find public spot within a \
2-minute walk (a named entrance, landmark or statue), never a vague area ("near the lawn"), and never the spot \
itself.

`past_challenges` on a player: how they rated earlier challenges, in their words. Learn from it: `timer_min` is 15, \
but lower it (to 10) if one ran too long; if one was too hard, make the hints even more obvious. Ratings are \
private: never hint at them in the hints or the meeting point. `adjustments`: for each player whose feedback you \
acted on, one short line to that player only, saying what you changed for them, e.g. "Kept it to 10 minutes, since \
your last one ran long." Leave it empty if you changed nothing."""


class Spot(BaseModel):
    id: str
    name: str  # "the Rotunda": revealed at the end
    about: str  # what's true about it, for writing the hints
    check: str  # what the group photo's background shows there
    area: str
    giveaways: list[str] = []  # no hint may contain these, nor the words of the name
    hints: list[str] = []  # hand-written hints for the fallback, one per player


class Pose(BaseModel):
    id: str
    name: str
    people: int  # one character per player
    image: str
    cuts: list[float]  # where the picture is cut into parts, left to right: people + 1 values from 0 to 1
    top: float = 0  # crop this much off the top of the picture (caption bars, sky)
    pose: str  # what the photo check looks for


class Hint(BaseModel):
    agent_id: str
    hint: str


class Adjustment(BaseModel):
    agent_id: str
    note: str  # to that player only: what changed because of their past feedback


class Hints(BaseModel):
    hints: list[Hint]
    meeting_point: str = ""  # where the group meets first; empty, too long or the spot itself → the default spot
    timer_min: int = TIMER_MIN
    adjustments: list[Adjustment] = []


def load_spots(directory: Path = DATA_DIR) -> list[Spot]:
    return [Spot.model_validate(s) for s in json.loads((directory / "spots.json").read_text(encoding="utf-8"))]


def load_poses(directory: Path = DATA_DIR) -> list[Pose]:
    return [Pose.model_validate(p) for p in json.loads((directory / "poses.json").read_text(encoding="utf-8"))]


def fitting(poses: list[Pose], n: int) -> list[Pose]:
    """The memes with one character per player; else the closest size, whose picture is cut into n equal parts."""
    return [p for p in poses if p.people == n] or [min(poses, key=lambda p: (abs(p.people - n), -p.people))]


def giveaways(spot: Spot) -> set[str]:
    name = set(re.findall(r"[a-z0-9']+", spot.name.casefold())) - {"the", "a", "an", "of", "booth"}
    return name | {g.casefold() for g in spot.giveaways}


def reveals(text: str, spot: Spot) -> bool:
    """Whether a hint or a meeting point names the spot."""
    return any(g in text.casefold() for g in giveaways(spot))


def comments(past: list[str]) -> str:
    """Just the players' own words from rating memories ('… They said: "too long". …')."""
    return " ".join(m.group(1) for text in past for m in re.finditer(r'They said: "(.*?)"', text))


def meetup(match_id: str, spot: str) -> dict:
    """Where to meet and the group's sign, the same for every player. The sign comes from the match id, not Claude."""
    n = sum(map(ord, match_id or "x"))
    (color, hex_), (animal, emoji) = SIGN_COLORS[n % len(SIGN_COLORS)], SIGN_ANIMALS[(n // len(SIGN_COLORS)) % len(SIGN_ANIMALS)]
    return {"spot": spot, "sign": f"{color} {animal}", "emoji": emoji, "color": hex_}


def validate(out: Hints, members: list[str], spot: Spot) -> str | None:
    """Why the hints can't be used, or None when they're fine."""
    hints = {h.agent_id: h.hint.strip() for h in out.hints}
    if set(hints) != set(members) or len(out.hints) != len(members):
        return "hints must have exactly one entry per member"
    if any(not h or len(h) > MAX_HINT_CHARS for h in hints.values()):
        return f"every hint must be 1-{MAX_HINT_CHARS} characters"
    if len({h.casefold() for h in hints.values()}) != len(hints):
        return "hints must all be different"
    if any(reveals(h, spot) for h in hints.values()):
        return "a hint names the spot"
    return None


def template(
    members: list[str], spot: Spot, pose: Pose, hints: dict[str, str], seed: str, meet: dict,
    timer_min: int = TIMER_MIN, adjustments: list[Adjustment] | None = None,
) -> dict:
    n = len(members)
    cuts = pose.cuts if pose.people == n or not n else [round(i / n, 4) for i in range(n + 1)]
    holders = random.Random(seed).sample(members, n)  # who holds which part, left to right
    check = (
        f"Taken at {spot.name}: the background shows {spot.check}. The people in it recreate the pose in the "
        f"reference picture: {pose.pose}. The reference shows only the pose, not the place. It's fine if one player "
        "is missing because they took the photo. The reason must never name or describe the spot or the pose: say "
        "only what's missing, like \"that doesn't look like the spot yet\"."
    )
    return {
        "group": members,
        "round": {
            "type": "spot_and_pose",
            "title": TITLE,
            "goal": GOAL,
            "steps": [{"id": "photo", "task": "Group photo at the spot, doing the pose", "check": check}],
            "keys": hints,  # each player's own hint for the spot
            "pose": {"image": pose.image, "cuts": cuts, "top": pose.top},
            "pieces": {m: i for i, m in enumerate(holders)},  # each player's part of the picture
            "reference_photo": pose.image,
            "spot": spot.id,
            "answer": f"{spot.name} · {pose.name}",  # the game board reveals it at the end
            "unlock": "everyone taps We're here at the spot, then the group photo passes its check",
            "timer_min": max(5, min(30, timer_min)),
            "meet": meet,  # public: every player sees the same meeting point and sign
            # Private, like the hints: what changed for a player because of their past ratings.
            "tuned": {
                a.agent_id: a.note.strip()
                for a in adjustments or []
                if a.agent_id in members and 0 < len(a.note.strip()) <= MAX_SPOT_CHARS
            },
        },
    }


def fallback(players: list[dict], spots: list[Spot], poses: list[Pose], match_id: str = "", meeting_point: str = "") -> dict:
    """A spot with enough hand-written hints that isn't the meeting point, and a meme that fits, both picked by the
    match id. Shorter when someone's past feedback said a challenge ran long."""
    ids = [p["agent_id"] for p in players]
    where = meeting_point or DEFAULT_SPOT
    rng = random.Random(match_id or "x")
    hinted = [s for s in spots if s.hints and not reveals(where, s)] or [s for s in spots if s.hints]
    spot = rng.choice([s for s in hinted if len(s.hints) >= len(ids)] or hinted)
    hints = rng.sample(spot.hints, len(spot.hints))
    pose = rng.choice(fitting(poses, len(ids)))
    long_before = [p["agent_id"] for p in players if TOO_LONG.search(comments(p.get("past_challenges", [])))]
    timer = SHORT_TIMER_MIN if long_before else TIMER_MIN
    adjustments = [Adjustment(agent_id=a, note=f"Kept it to {timer} minutes, since your last one ran long.") for a in long_before]
    hints = {m: hints[i % len(hints)] for i, m in enumerate(ids)}
    return template(ids, spot, pose, hints, match_id, meetup(match_id, where), timer, adjustments)


async def design(
    claude: anthropic.AsyncAnthropic | None,
    model: str,
    budget_s: float,
    players: list[dict],
    place: Place,
    when: str,
    spots: list[Spot],
    poses: list[Pose],
    match_id: str = "",
    meeting_point: str = "",
) -> tuple[dict, str]:
    """players: [{agent_id, name, interests, skills, past_challenges}]. meeting_point: an organizer's pick
    (MEETING_POINT); it beats Claude's. Returns (challenge, how) where how says who wrote it."""
    ids = [p["agent_id"] for p in players]
    default = meeting_point or f"the main entrance of the {place.location}"
    if claude is None or not ids:
        return fallback(players, spots, poses, match_id, default), "hand-written hints (Claude off)"

    rng = random.Random(match_id or "x")
    spot = rng.choice([s for s in spots if not reveals(default, s)] or spots)  # never meet at the answer
    pose = rng.choice(fitting(poses, len(ids)))
    system = SYSTEM.format(n=len(ids), event=place.event, location=place.location, city=place.city, time=when)
    content = json.dumps(
        {
            "players": players,
            "spot": spot.model_dump(include={"name", "about", "giveaways"}),
            "facts_about_the_place": [f.text for f in place.facts],
        },
        indent=1,
    )
    try:
        out = await asyncio.wait_for(
            llm.parse(claude, model, system=system, content=content, output=Hints, effort="medium"), budget_s
        )
    except Exception as e:
        log.warning("designer fell back: %r", e)
        return fallback(players, spots, poses, match_id, default), "hand-written hints (designer error)"
    if out is None:
        return fallback(players, spots, poses, match_id, default), "hand-written hints (declined)"
    if problem := validate(out, ids, spot):
        log.warning("designer hints rejected: %s", problem)
        return fallback(players, spots, poses, match_id, default), f"hand-written hints ({problem})"

    hints = {h.agent_id: h.hint.strip() for h in out.hints}
    where = out.meeting_point.strip()
    if meeting_point or not where or len(where) > MAX_SPOT_CHARS or reveals(where, spot):
        where = default
    elif where.startswith("The "):  # it follows "Meet at"
        where = "t" + where[1:]
    how = "Claude + scraped place info" if place.scraped else "Claude + built-in place info"
    return template(ids, spot, pose, hints, match_id, meetup(match_id, where), out.timer_min, out.adjustments), how
