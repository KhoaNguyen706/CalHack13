"""A persona's personal agent: decides yes / no / counter on the coordinator's plan.

Claude decides from the persona's profile and style; code checks the decision. Any Claude failure, or a counter
that doesn't fit the person's own free time, falls back to the rule decision, so the broker always gets an answer.
"""

import asyncio
import json
import logging
import os
from typing import Literal

import anthropic
from pydantic import BaseModel

from .models import Persona, Question, Reply, Window, parse_free, shared

log = logging.getLogger(__name__)

MODEL = os.environ.get("AGENT_MODEL", "claude-opus-5-5")
MIN_OVERLAP_MIN = 60
# The broker counts "no" after 20 s; leave room for the rule fallback.
CLAUDE_TIMEOUT_S = 12.0

SYSTEM = """You are one person's personal agent. A coordinator proposes a plan: meet 2 strangers for a short,
cooperative challenge at a public spot. Decide for your person using only their profile and style.

- "yes": the plan fits as proposed.
- "counter": it would fit with a shorter or shifted window. Give `free` as "Sat 14:00-15:30": the same day, inside
  your person's free time, overlapping the plan's window by at least an hour.
- "no": it doesn't fit, or your person isn't taking new plans.

`note` is one short sentence on why, written as the agent (no names, no private details)."""


class Decision(BaseModel):
    answer: Literal["yes", "no", "counter"]
    free: str | None = None
    note: str


def plan_window(persona: Persona, question: Question) -> Window:
    try:
        return parse_free((question.plan or {})["window"])
    except (KeyError, TypeError, ValueError):
        return persona.window


def valid_counter(persona: Persona, plan: Window, free: str | None) -> bool:
    """A counter must sit inside the person's free time and still overlap the plan by an hour."""
    try:
        w = parse_free(free or "")
    except ValueError:
        return False
    inside = shared(w, persona.window) == w
    both = shared(w, plan)
    return inside and both is not None and both.minutes >= MIN_OVERLAP_MIN


def rule_decide(persona: Persona, question: Question) -> Reply:
    plan = plan_window(persona, question)
    fits = shared(plan, persona.window)
    if not persona.open_to_invites:
        return Reply(answer="no", note="not taking new plans right now")
    if fits is None or fits.minutes < MIN_OVERLAP_MIN:
        return Reply(answer="no", note="doesn't fit my free time")
    if persona.max_minutes and fits.minutes > persona.max_minutes:
        shorter = Window(fits.day, fits.start, fits.start + persona.max_minutes)
        return Reply(answer="counter", free=str(shorter), note=f"can do {persona.max_minutes} minutes")
    if fits != plan:
        return Reply(answer="counter", free=str(fits), note="only part of that window works")
    return Reply(answer="yes", note="fits my free time")


async def claude_decide(client: anthropic.AsyncAnthropic, persona: Persona, question: Question) -> Decision | None:
    profile = persona.model_dump(exclude={"agent_id"})
    response = await client.messages.parse(
        model=MODEL,
        max_tokens=4000,
        output_config={"effort": "low"},
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": json.dumps({"profile": profile, "question": question.question, "plan": question.plan}, indent=1),
            }
        ],
        output_format=Decision,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output


def check(decision: Decision, persona: Persona, question: Question) -> Reply | None:
    """Claude's decision as a Reply, or None if it breaks a rule."""
    if decision.answer == "counter":
        if not valid_counter(persona, plan_window(persona, question), decision.free):
            return None
        return Reply(answer="counter", free=str(parse_free(decision.free)), note=decision.note)
    if decision.answer == "yes" and not persona.open_to_invites:
        return None
    return Reply(answer=decision.answer, note=decision.note)


async def decide(persona: Persona, question: Question, client: anthropic.AsyncAnthropic | None) -> Reply:
    """Never raises: the broker always gets an answer."""
    if client is not None:
        try:
            decision = await asyncio.wait_for(claude_decide(client, persona, question), CLAUDE_TIMEOUT_S)
        except Exception as e:  # API errors, timeouts, missing credentials: fall back to rules
            log.warning("Claude decision for %s failed: %s", persona.agent_id, e)
            decision = None
        reply = check(decision, persona, question) if decision else None
        if reply:
            return reply
    return rule_decide(persona, question)
