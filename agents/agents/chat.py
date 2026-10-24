"""Sample chatbot standing in for a person's personal agent (like Muse). It knows its person through a
personal context (fake personal info), starts challenges and answers invitations for them, and talks them
through the game. Coordinator news reaches it as [Update: ...] lines in the next turn."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from pydantic import ValidationError

from . import llm, timeutil
from .coordinator_client import CoordinatorError
from .models import MIN_GROUP_SIZE, User

if TYPE_CHECKING:
    from .service import AgentsService

log = logging.getLogger(__name__)

MAX_STEPS = 5
OFFLINE = "My brain is offline right now (Claude isn't configured), but tap Start and I'll still set up a challenge."

SYSTEM = """You are {name}'s personal agent at {event} ({location}, {city}). The saved profile in each \
[Now: ...] line is what you know about {name}. If it's missing their interests, skills or free time today, ask \
for them, one short question at a time, and save the answers with save_profile; never re-ask what's saved. \
Before anything about them is shared, they tap Share on the card that lists exactly what goes out; if \
start_challenge says to tap Share first, point them to that card. You help them meet \
people through short, creative outdoor challenges (photo hunts, checkpoint runs and the like) with a few people \
nearby. A coordinator invites other people's agents; an AI invents the challenge for whoever joins.

How to help:
- Keep replies short and warm: 1-3 sentences, it's a phone chat. No markdown.
- When they want to play, call start_challenge with the group size they want (2 to 5; ask if they haven't said, \
suggest 3). You can't promise who joins; news arrives later as updates.
- When they've been invited, tell them who invited them and when. Call respond_to_invite only on their clear yes \
or no.
- During a challenge, help with their part and the steps (my_challenge has them). Photos are checked in the game.
- If they tell you something new about themselves, save it with save_profile.
- Refer to other players by name or as "they"; never guess anyone's gender from a name.
- Never ask for their full name, contact details or exact location. Challenges happen in public spots, and \
nothing happens without their OK.
Lines in [brackets] come from the app, not from {name}."""

TOOLS = [
    {
        "name": "save_profile",
        "description": "Save what you learned about your person. Send only fields you learned; lists replace the old ones.",
        "input_schema": {
            "type": "object",
            "properties": {
                "interests": {"type": "array", "items": {"type": "string"}, "description": "Short tags, e.g. puzzles"},
                "skills": {"type": "array", "items": {"type": "string"}, "description": "Short tags, e.g. photography"},
                "free": {"type": "string", "description": 'One window, e.g. "Sat 14:00-16:00"'},
                "group_pref": {"type": "string", "enum": ["small", "any", "large"]},
                "about": {"type": "string", "description": "Anything else worth remembering, one sentence"},
            },
        },
    },
    {
        "name": "start_challenge",
        "description": "Start a challenge for your person. The coordinator invites people nearby until the group has `size` people.",
        "input_schema": {
            "type": "object",
            "properties": {"size": {"type": "integer", "minimum": MIN_GROUP_SIZE, "maximum": 5, "description": "Group size, including your person"}},
            "required": ["size"],
        },
    },
    {
        "name": "respond_to_invite",
        "description": "Accept or decline the challenge your person was invited to, on their clear say-so.",
        "input_schema": {
            "type": "object",
            "properties": {"accept": {"type": "boolean"}},
            "required": ["accept"],
        },
    },
    {
        "name": "my_challenge",
        "description": "Your person's current challenge: state, who's in, the steps, their part and which steps are done.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


async def reply(svc: AgentsService, user: User, message: str) -> str:
    """One chat turn. The conversation is append-only (thinking blocks must come back unchanged)."""
    if svc.claude is None:
        return OFFLINE
    now = timeutil.now(svc.s.tz)
    header = f"[Now: {timeutil.DAYS[now.weekday()]} {now:%H:%M} · saved profile: {user.profile.model_dump_json()}]"
    news = "".join(f"\n[Update: {n}]" for n in user.inbox)
    user.inbox.clear()
    user.convo.append({"role": "user", "content": f"{header}{news}\n{message}"})
    system = SYSTEM.format(name=user.name, event=svc.s.event, location=svc.s.location, city=svc.s.city)

    response = None
    for _ in range(MAX_STEPS):
        response = await svc.claude.messages.create(
            model=svc.s.model,
            max_tokens=8000,
            system=system,
            tools=TOOLS,
            messages=user.convo,
            output_config={"effort": "low"},
            **llm.FALLBACK,
        )
        user.convo.append(
            {"role": "assistant", "content": [b.model_dump(mode="json", exclude_none=True) for b in response.content]}
        )
        if response.stop_reason != "tool_use":
            break
        results = [await run_tool(svc, user, b) for b in response.content if b.type == "tool_use"]
        user.convo.append({"role": "user", "content": results})

    if response is not None and response.stop_reason == "refusal":
        return "Sorry, I can't help with that one. Want me to find you a group?"
    text = "".join(b.text for b in response.content if b.type == "text").strip() if response else ""
    return text or "On it!"


async def run_tool(svc: AgentsService, user: User, block) -> dict:
    try:
        content = await TOOL_FNS[block.name](svc, user, block.input or {})
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}
    except (ValidationError, ValueError, CoordinatorError, KeyError) as e:
        detail = getattr(e, "detail", None) or str(e)
        if isinstance(e, ValidationError):
            detail = 'free must look like "Sat 14:00-16:00"'
        return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {detail}", "is_error": True}


async def _save_profile(svc: AgentsService, user: User, args: dict) -> str:
    return "Saved: " + svc.update_profile(user, args).model_dump_json(exclude={"about"})


async def _start_challenge(svc: AgentsService, user: User, args: dict) -> str:
    size = int(args.get("size") or 3)
    if not MIN_GROUP_SIZE <= size <= 5:
        raise ValueError(f"size must be {MIN_GROUP_SIZE} to 5")
    free = await svc.start(user, size)
    return f"Started a {size}-person challenge for {free}. The coordinator is inviting people; news arrives as updates."


async def _respond(svc: AgentsService, user: User, args: dict) -> str:
    match = await svc.respond(user, bool(args.get("accept")))
    return f"Done. The challenge is now {match['state']}."


async def _my_challenge(svc: AgentsService, user: User, args: dict) -> str:
    s = await svc.status(user)
    m = s["match"]
    if not m:
        return "No challenge yet." + (" Checked in." if s["checked_in"] else " Not checked in.")
    return json.dumps(
        {
            "state": m["state"],
            "people": [svc.display(a) for a in m["members"]],
            "window": m["window"],
            "challenge": s["challenge"],
            "my_part": s["my_part"],
            "progress": s["progress"],
        }
    )


TOOL_FNS = {
    "save_profile": _save_profile,
    "start_challenge": _start_challenge,
    "respond_to_invite": _respond,
    "my_challenge": _my_challenge,
}
