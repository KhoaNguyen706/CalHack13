"""Hybrid matching: code filters and scores, Claude picks from a shortlist, code validates the pick.

Two flows share it:
- Host-led challenges (the app): rank_invitees + pick_invitees choose who to invite into the host's group.
- /matching/run (testing): candidate_groups + pick split the pool into groups with fixed roles.
"""

import json
import logging
import os
from dataclasses import dataclass
from itertools import combinations, permutations
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from .models import Agent, Window

log = logging.getLogger(__name__)

ROLES = ("riddles", "map", "camera")
ROLE_TAGS = {
    "riddles": {"riddles", "puzzles", "logic", "trivia", "word games"},
    "map": {"map", "navigation", "campus", "directions", "exploring"},
    "camera": {"camera", "photography", "photos", "video", "design"},
}
MIN_OVERLAP_MIN = 60
# 3 in the real design; GROUP_SIZE=2 lets two browser windows rehearse a whole round.
GROUP_SIZE = int(os.environ.get("GROUP_SIZE", "3"))
SHORTLIST = 5
MODEL = os.environ.get("MATCH_MODEL", "claude-opus-5-5")
_HERE = Path(__file__).parent.parent
INVITE_RULES = (_HERE / "matching_rules.md").read_text(encoding="utf-8")
GROUP_RULES = (_HERE / "group_rules.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Candidate:
    members: tuple[str, ...]
    window: Window
    roles: dict[str, str]
    covered: int  # how many members got a role that fits their tags
    score: float

    @property
    def overlap_min(self) -> int:
        return self.window.end - self.window.start


def can_play(agent: Agent) -> set[str]:
    tags = set(agent.skills) | set(agent.interests)
    return {role for role, role_tags in ROLE_TAGS.items() if tags & role_tags}


def overlap(agents: list[Agent]) -> Window | None:
    """Shared window of all agents, if it is at least an hour on the same day."""
    windows = [a.window for a in agents]
    if len({w.day for w in windows}) != 1:
        return None
    start, end = max(w.start for w in windows), min(w.end for w in windows)
    return Window(windows[0].day, start, end) if end - start >= MIN_OVERLAP_MIN else None


def best_roles(agents: list[Agent]) -> tuple[dict[str, str], int]:
    fits = [can_play(a) for a in agents]
    best = max(permutations(ROLES, len(agents)), key=lambda perm: sum(r in f for r, f in zip(perm, fits)))
    return {a.agent_id: r for a, r in zip(agents, best)}, sum(r in f for r, f in zip(best, fits))


def score_group(agents: list[Agent]) -> Candidate | None:
    """Hard filters, then a score: role fit dominates, shared interests and overlap break ties."""
    if len({a.area.casefold() for a in agents}) != 1 or any(a.group_pref == "large" for a in agents):
        return None
    window = overlap(agents)
    if window is None:
        return None
    roles, covered = best_roles(agents)
    shared = len(set.intersection(*(set(a.interests) for a in agents)))
    score = covered * 10 + min(shared, 2) * 2 + (window.end - window.start) / 60
    return Candidate(tuple(a.agent_id for a in agents), window, roles, covered, score)


def candidate_groups(agents: list[Agent]) -> list[Candidate]:
    """Brute force over all groups of GROUP_SIZE (1,140 groups of 3 at 20 agents). Best first; ties go to the longer overlap."""
    found = (score_group(list(group)) for group in combinations(agents, GROUP_SIZE))
    return sorted((c for c in found if c), key=lambda c: (c.score, c.overlap_min), reverse=True)


class RoleAssignment(BaseModel):
    agent_id: str
    role: Literal["riddles", "map", "camera"]


class ClaudePick(BaseModel):
    candidate: int
    roles: list[RoleAssignment]
    reason: str


def validate(pick: ClaudePick, shortlist: list[Candidate]) -> Candidate | None:
    """Claude's pick must name a shortlisted group and give its members one distinct role each."""
    if not 0 <= pick.candidate < len(shortlist):
        return None
    chosen = shortlist[pick.candidate]
    roles = {r.agent_id: r.role for r in pick.roles}
    n = len(chosen.members)
    if len(pick.roles) != n or set(roles) != set(chosen.members) or len(set(roles.values())) != n:
        return None
    return Candidate(chosen.members, chosen.window, roles, chosen.covered, chosen.score)


async def claude_pick(client: anthropic.AsyncAnthropic, shortlist: list[Candidate], agents: dict[str, Agent]) -> ClaudePick | None:
    payload = [
        {
            "candidate": i,
            "overlap": str(c.window),
            "rule_score": round(c.score, 2),
            "suggested_roles": c.roles,
            "members": [
                {"agent_id": m, "skills": agents[m].skills, "interests": agents[m].interests} for m in c.members
            ],
        }
        for i, c in enumerate(shortlist)
    ]
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=GROUP_RULES,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        output_format=ClaudePick,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


async def pick(
    candidates: list[Candidate], agents: dict[str, Agent], client: anthropic.AsyncAnthropic | None
) -> tuple[Candidate, str, Literal["claude", "rules"]]:
    """Returns (group, reason, picked_by). Never raises: any Claude failure falls back to the top score."""
    shortlist = candidates[:SHORTLIST]
    fallback_reason = "highest rule score"
    if client is not None:
        try:
            choice = await claude_pick(client, shortlist, agents)
        except Exception as e:  # API errors, missing credentials, unparseable output: the demo must not break
            log.warning("Claude pick failed: %s", e)
            choice = None
            fallback_reason = "Claude unavailable → highest rule score"
        if choice is not None:
            valid = validate(choice, shortlist)
            if valid:
                return valid, choice.reason, "claude"
            fallback_reason = "Claude pick failed validation → highest rule score"
    return shortlist[0], fallback_reason, "rules"


# --- host-led challenges -------------------------------------------------------


@dataclass(frozen=True)
class Invitee:
    agent_id: str
    window: Window  # the group's shared window if they join
    score: float


def rank_invitees(group: list[Agent], pool: list[Agent], size: int) -> list[Invitee]:
    """Who fits the host's group best. Hard filters: same area, an hour shared with everyone, and nobody who wants
    a large group in a group of 3 or fewer. Score: shared interests (something to talk about), skills the group lacks
    (complementary), then overlap hours."""
    area = group[0].area.casefold()
    interests = set().union(*(a.interests for a in group))
    skills = set().union(*(a.skills for a in group))
    found = []
    for a in pool:
        if a.area.casefold() != area or (size <= 3 and a.group_pref == "large"):
            continue
        window = overlap([*group, a])
        if window is None:
            continue
        shared, new = len(set(a.interests) & interests), len(set(a.skills) - skills)
        score = min(shared, 2) * 2 + min(new, 2) * 3 + (window.end - window.start) / 60
        found.append(Invitee(a.agent_id, window, score))
    return sorted(found, key=lambda c: (c.score, c.window.end - c.window.start), reverse=True)


class InvitePick(BaseModel):
    invite: list[str]
    reason: str


def validate_invites(choice: InvitePick, shortlist: list[Invitee], n: int) -> list[str] | None:
    """Claude must name exactly n different people, all from the shortlist."""
    ids = {c.agent_id for c in shortlist}
    if len(choice.invite) != n or len(set(choice.invite)) != n or not set(choice.invite) <= ids:
        return None
    return choice.invite


async def claude_invites(
    client: anthropic.AsyncAnthropic, group: list[Agent], shortlist: list[Invitee], n: int, agents: dict[str, Agent]
) -> InvitePick | None:
    def person(a: Agent) -> dict:
        return {"agent_id": a.agent_id, "skills": a.skills, "interests": a.interests}

    payload = {
        "invite_count": n,
        "group": [person(a) for a in group],
        "candidates": [
            {**person(agents[c.agent_id]), "overlap_if_joined": str(c.window), "rule_score": round(c.score, 2)}
            for c in shortlist
        ],
    }
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=INVITE_RULES,
        messages=[{"role": "user", "content": json.dumps(payload, indent=1)}],
        output_format=InvitePick,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


async def pick_invitees(
    ranked: list[Invitee], group: list[Agent], n: int, agents: dict[str, Agent], client: anthropic.AsyncAnthropic | None
) -> tuple[list[str], str, Literal["claude", "rules"]]:
    """Returns (who to invite, reason, picked_by). Never raises: any Claude failure falls back to the top scores."""
    n = min(n, len(ranked))
    shortlist = ranked[: max(SHORTLIST, 2 * n)]
    fallback_reason = "highest rule scores"
    if client is not None and len(shortlist) > n:  # nothing to choose when everyone left is invited anyway
        try:
            choice = await claude_invites(client, group, shortlist, n, agents)
        except Exception as e:  # API errors, missing credentials, unparseable output: the demo must not break
            log.warning("Claude invite pick failed: %s", e)
            choice = None
            fallback_reason = "Claude unavailable → highest rule scores"
        if choice is not None:
            valid = validate_invites(choice, shortlist, n)
            if valid:
                return valid, choice.reason, "claude"
            fallback_reason = "Claude pick failed validation → highest rule scores"
    return [c.agent_id for c in shortlist[:n]], fallback_reason, "rules"
