"""Hybrid matching: code filters and scores, Claude picks from a shortlist, code validates the pick."""

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
SHORTLIST = 5
MODEL = os.environ.get("MATCH_MODEL", "claude-opus-5-5")
RULES = (Path(__file__).parent.parent / "matching_rules.md").read_text(encoding="utf-8")


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
    best = max(permutations(ROLES), key=lambda perm: sum(r in f for r, f in zip(perm, fits)))
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
    """Brute force over all groups of 3 (1,140 at 20 agents). Best first; ties go to the longer overlap."""
    found = (score_group(list(group)) for group in combinations(agents, 3))
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
    if len(pick.roles) != 3 or set(roles) != set(chosen.members) or sorted(roles.values()) != sorted(ROLES):
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
        system=RULES,
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
