"""Challenge designer: Claude writes the split_riddle round, code validates it.

Claude picks a sponsor booth and writes one key per role, each useless alone. Code checks the round; on failure
Claude gets one retry with the problems listed, then a hand-written round from booths.json takes over. Never raises:
the coordinator always gets a round inside its 60 s timeout.
"""

import asyncio
import json
import logging
import os
import time
from collections import Counter

import anthropic
from pydantic import BaseModel

from .sponsors import Booth, SponsorInfo

log = logging.getLogger(__name__)

MODEL = os.environ.get("DESIGNER_MODEL", "claude-opus-5-5")
# The coordinator gives up after 60 s.
BUDGET_S = 40.0
TIMER_MIN, TIMER_RANGE = 15, range(5, 21)
MAX_KEY_CHARS = 160
UNLOCK = "all_checked_in + photo"

SYSTEM = """You design one round of a cooperative scavenger hunt for 3 strangers at a hackathon. Together they must
find one sponsor booth. Each person's phone shows one key, and no single key is enough to find it.

Write exactly one key per member, by role:
- riddles: what the sponsor's tool does, as a clue. Use the scraped sponsor info.
- map: where the booth is, from the booth's location.
- camera: starts with "Ask them:", then a friendly question to ask the sponsor about their tool. This person asks it
  at the booth before the group photo.

Rules:
- No key may contain the sponsor's name, product names or the booth id.
- Plain, warm words; under 140 characters each; solvable in a few minutes by talking and walking.
- Targets are booths, never people. Never mention anyone's appearance.
- Pick the booth from the list. Prefer booths with fewer `uses`, so groups don't crowd one booth, and a sponsor that
  fits the group's interests.
- timer_min between 5 and 20, normally 15. Go shorter if the group's notes ask for shorter challenges.
- note: one sentence on why this booth suits the group. It is shown on a public log: no names."""


class Member(BaseModel):
    agent_id: str
    role: str
    skills: list[str] = []
    interests: list[str] = []


class ChallengeRequest(BaseModel):
    """What the coordinator POSTs to CHALLENGE_DESIGNER_URL."""

    match: dict
    members: list[Member]


class KeyCard(BaseModel):
    agent_id: str
    key: str


class DesignedRound(BaseModel):
    """Claude's output. Keys are a list so the schema stays simple; to_challenge turns them into a map."""

    booth_id: str
    keys: list[KeyCard]
    timer_min: int
    note: str


def problems(designed: DesignedRound, members: list[Member], booths: list[Booth]) -> list[str]:
    """Every rule the round breaks. Empty means valid."""
    found = []
    booth = next((b for b in booths if b.id == designed.booth_id), None)
    if booth is None:
        found.append(f"booth_id must be one of {[b.id for b in booths]}")
    ids = [k.agent_id for k in designed.keys]
    if sorted(ids) != sorted(m.agent_id for m in members):
        found.append(f"keys must have exactly one entry for each of {[m.agent_id for m in members]}")
    if designed.timer_min not in TIMER_RANGE:
        found.append("timer_min must be between 5 and 20")
    roles = {m.agent_id: m.role for m in members}
    for k in designed.keys:
        text = k.key.strip()
        if not text or len(text) > MAX_KEY_CHARS:
            found.append(f"{k.agent_id}'s key must be 1-{MAX_KEY_CHARS} characters")
        if booth and any(word in text.lower() for word in booth.giveaways):
            found.append(f"{k.agent_id}'s key gives the sponsor away; drop its name, products and booth id")
        if roles.get(k.agent_id) == "camera" and not text.startswith("Ask them"):
            found.append(f'{k.agent_id} holds the camera role, so their key must start with "Ask them:"')
    if len({k.key.strip().lower() for k in designed.keys}) != len(designed.keys):
        found.append("every key must be different")
    return found


def to_challenge(members: list[Member], booth_id: str, keys: dict[str, str], timer_min: int, by: str, note: str) -> dict:
    """The game template the coordinator expects, plus who designed it."""
    return {
        "group": [m.agent_id for m in members],
        "round": {"type": "split_riddle", "keys": keys, "answer": booth_id, "unlock": UNLOCK, "timer_min": timer_min},
        "designer": {"by": by, "note": note},
    }


def fallback(members: list[Member], booths: list[Booth], uses: Counter) -> dict:
    """Hand-written round at the least-used booth (ties go to the first listed)."""
    booth = min(booths, key=lambda b: uses[b.id])
    by_role = {"riddles": booth.hint, "map": f"Their booth is {booth.location}.", "camera": booth.question}
    keys = {m.agent_id: by_role.get(m.role, booth.hint) for m in members}
    return to_challenge(members, booth.id, keys, TIMER_MIN, "fallback", f"hand-written round at the {booth.sponsor} booth")


def brief(members: list[Member], booths: list[Booth], sponsors: dict[str, SponsorInfo], uses: Counter, notes: list[str]) -> str:
    return json.dumps(
        {
            "members": [m.model_dump() for m in members],
            "booths": [
                {
                    "id": b.id,
                    "sponsor": b.sponsor,
                    "location": b.location,
                    "uses": uses[b.id],
                    "what_it_does": sponsors[b.sponsor].what_it_does if b.sponsor in sponsors else b.hint,
                    "notable_projects": sponsors[b.sponsor].notable_projects if b.sponsor in sponsors else [],
                }
                for b in booths
            ],
            "notes": notes,
        },
        indent=1,
    )


async def claude_design(client: anthropic.AsyncAnthropic, content: str) -> DesignedRound | None:
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=8000,
        output_config={"effort": "medium"},
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=DesignedRound,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


async def design(
    request: ChallengeRequest,
    booths: list[Booth],
    sponsors: dict[str, SponsorInfo],
    client: anthropic.AsyncAnthropic | None,
    uses: Counter,
    notes: list[str] = (),
) -> dict:
    """The round for this group. Counts the chosen booth in `uses` so the next group goes elsewhere."""
    members = request.members
    area = request.match.get("area")
    local = [b for b in booths if b.area == area]
    if not local:
        log.warning("no booths listed for area %r; using all booths", area)
    booths = local or booths
    challenge = None
    if client is not None:
        content = brief(members, booths, sponsors, uses, list(notes))
        deadline = time.monotonic() + BUDGET_S
        for attempt in range(2):
            try:
                designed = await asyncio.wait_for(claude_design(client, content), deadline - time.monotonic())
            except Exception as e:  # API errors, timeouts, missing credentials: go to the hand-written round
                log.warning("Claude design failed: %s", e)
                break
            if designed is None:
                break
            found = problems(designed, members, booths)
            if not found:
                keys = {k.agent_id: k.key.strip() for k in designed.keys}
                challenge = to_challenge(members, designed.booth_id, keys, designed.timer_min, "claude", designed.note)
                break
            log.warning("designed round invalid (attempt %d): %s", attempt + 1, found)
            content += "\n\nYour last round broke these rules. Write a new round that fixes them:\n- " + "\n- ".join(found)
    challenge = challenge or fallback(members, booths, uses)
    uses[challenge["round"]["answer"]] += 1
    return challenge
