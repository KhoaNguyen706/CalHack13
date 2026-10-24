"""Personal agents answer the coordinator's invitations ("want to join?") for their person.

A real personal agent (Muse, ...) would know its person; ours decide from a personal context (fake
personal info) and memories. If Claude is off, slow or refuses, a plain rule answers instead: yes when
the plan sits inside their free time.
"""

import asyncio
import json
import logging

import anthropic

from . import llm, timeutil
from .models import Answer, Profile, Question

log = logging.getLogger(__name__)

SYSTEM = """You are a personal agent for one person at a hackathon. A coordinator invites them to short, \
creative outdoor challenges with a few people nearby (photo hunts, checkpoint runs and the like). The plan says \
who started it, how many people, where and when.

Decide for your person, using only what you know about them:
- "yes" when the time fits their free time and they would enjoy it.
- "counter" when they would enjoy it but the time does not fit. Put a window inside their free time, at least \
1 hour long, in `free`, formatted like "Sat 14:00-16:00".
- "no" when they would not want it, for example a memory says they disliked that kind of challenge, or the \
group is bigger than they like.
Lean towards yes: meeting people is the point, and they approve every group themselves afterwards.

`note` is shown on a public screen: a phrase of at most 8 words saying why, like \
"free then and loves puzzles" or "remembers enjoying the map role". Leave the person out of it or say \
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
            if answer is not None:
                return checked(answer)
        except Exception as e:  # timeout, API error, bad output: the rule answers
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


def checked(answer: Answer) -> Answer:
    if answer.answer != "counter":
        return answer.model_copy(update={"free": None})
    if timeutil.valid(answer.free):
        return answer.model_copy(update={"free": str(timeutil.parse(answer.free))})
    return Answer(answer="no", note=answer.note or "the time doesn't work")


def rule_answer(profile: Profile, q: Question) -> Answer:
    window = (q.plan or {}).get("window")
    if not profile.free or not timeutil.valid(window) or timeutil.fits(window, profile.free):
        return Answer(answer="yes", note="free then")
    return Answer(answer="no", note="busy then")
