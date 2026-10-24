"""Personal agents answer the coordinator's invitations ("want to join?") for their person.

A real personal agent (Muse, ...) would know its person; ours decide from a personal context (fake
personal info: profile, style, longest block, open to invites) and memories. Claude decides; code checks
the decision. A decision that breaks a rule, a refusal, an error or a timeout falls back to the rules:

1. Not open to invites → no.
2. Less than an hour of the plan fits their free time → no.
3. Fits, but longer than `max_minutes` → counter with the first `max_minutes`.
4. Only part of the plan fits → counter with that part.
5. Otherwise → yes.
"""

import asyncio
import json
import logging

import anthropic

from . import llm, timeutil
from .models import Answer, Profile, Question
from .timeutil import Window

log = logging.getLogger(__name__)

MIN_OVERLAP_MIN = 60

SYSTEM = """You are a personal agent for one person at a hackathon. A coordinator invites them to short, \
creative outdoor challenges with a few people nearby (photo hunts, checkpoint runs and the like). The plan says \
who started it, how many people, where and when.

Decide for your person, using only what you know about them. Their `style` says how they decide; \
`max_minutes` is the longest block they'll commit to; if `open_to_invites` is false, the answer is no.
- "yes" when the time fits their free time and they would enjoy it.
- "counter" when they would enjoy it but the time does not fit, or it's longer than they'll commit to. Put a \
window in `free`, formatted like "Sat 14:00-15:30": the same day, inside their free time, overlapping the plan \
by at least an hour.
- "no" when they would not want it, for example a memory says they disliked that kind of challenge, or the \
group is bigger than they like.
Lean towards yes: meeting people is the point, and they approve every group themselves afterwards.

`note` is shown on a public screen: a phrase of at most 8 words saying why, like \
"free then and loves puzzles" or "can do 90 minutes". Leave the person out of it or say \
"they"; never guess anyone's gender from a name. No private details."""


async def decide(
    claude: anthropic.AsyncAnthropic | None,
    model: str,
    budget_s: float,
    name: str,
    profile: Profile,
    memories: list[str],
    q: Question,
) -> Answer:
    """Never raises: the broker always gets an answer."""
    if claude is not None:
        try:
            content = json.dumps(
                {
                    "person": {"name": name, **profile.model_dump()},
                    "memories": memories,
                    "question": q.question,
                    "plan": q.plan,
                    "their_role": role_of(q),
                },
                indent=1,
            )
            answer = await asyncio.wait_for(
                llm.parse(claude, model, system=SYSTEM, content=content, output=Answer, effort="low"), budget_s
            )
            if answer is not None and (ok := checked(answer, profile, q)):
                return ok
        except Exception as e:  # timeout, API error, bad output: the rules answer
            log.warning("agent %s fell back to rules: %r", q.agent_id, e)
    return rule_answer(profile, q)


def role_of(q: Question) -> str | None:
    return ((q.plan or {}).get("roles") or {}).get(q.agent_id)


def memory_query(q: Question) -> str:
    """What to look up in memory before answering: past challenges, the group size, the offered role if any."""
    plan = q.plan or {}
    text = f"how they felt about past challenges like {plan.get('activity', 'an outdoor group challenge')}"
    if plan.get("size"):
        text += f" in groups of {plan['size']}"
    if role := role_of(q):
        text += f" and the {role} role"
    return text


def plan_window(q: Question) -> Window | None:
    window = (q.plan or {}).get("window")
    return timeutil.parse(window) if timeutil.valid(window) else None


def free_window(profile: Profile, plan: Window | None) -> Window | None:
    """Their free time; with none saved, they're free for the plan (real people check in from "now")."""
    return timeutil.parse(profile.free) if profile.free else plan


def valid_counter(profile: Profile, plan: Window | None, free: str | None) -> bool:
    """A counter must sit inside the person's free time and still overlap the plan by an hour."""
    if not timeutil.valid(free):
        return False
    w = timeutil.parse(free)
    mine = free_window(profile, plan)
    inside = mine is None or timeutil.shared(w, mine) == w
    both = timeutil.shared(w, plan) if plan else w
    return inside and both is not None and both.minutes >= MIN_OVERLAP_MIN


def checked(answer: Answer, profile: Profile, q: Question) -> Answer | None:
    """Claude's answer if it keeps the rules, else None (and the rules decide)."""
    if answer.answer != "no" and not profile.open_to_invites:
        return None
    if answer.answer == "counter":
        if not valid_counter(profile, plan_window(q), answer.free):
            return None
        return answer.model_copy(update={"free": str(timeutil.parse(answer.free))})
    return answer.model_copy(update={"free": None})


def rule_answer(profile: Profile, q: Question) -> Answer:
    if not profile.open_to_invites:
        return Answer(answer="no", note="not taking new plans right now")
    plan = plan_window(q)
    if plan is None:
        return Answer(answer="yes", note="open to it")
    fits = timeutil.shared(plan, free_window(profile, plan))
    if fits is None or fits.minutes < MIN_OVERLAP_MIN:
        return Answer(answer="no", note="doesn't fit their free time")
    if profile.max_minutes and fits.minutes > profile.max_minutes:
        shorter = Window(fits.day, fits.start, fits.start + profile.max_minutes)
        return Answer(answer="counter", free=str(shorter), note=f"can do {profile.max_minutes} minutes")
    if fits != plan:
        return Answer(answer="counter", free=str(fits), note="only part of that window works")
    return Answer(answer="yes", note="fits their free time")
