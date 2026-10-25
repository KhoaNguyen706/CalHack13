"""MCP adapter: lets any MCP-capable agent join the coordination layer.

The coordinator serves it itself at /mcp (main.py), so any agent (a judge's own Claude as a connector) can join the
live event. Thin wrapper over the HTTP contract, so it also runs next to (or far from) the coordinator:
    COORDINATOR_URL=http://localhost:8000 python -m coordinator.mcp_server            # stdio
    COORDINATOR_URL=... python -m coordinator.mcp_server --http 8765                   # streamable HTTP
"""

import os
import secrets
import sys
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI
from fastmcp import FastMCP

BASE = os.environ.get("COORDINATOR_URL", "http://localhost:8000")
# Matching only groups agents in the same area, so outside agents must check in with the event's.
AREA = os.environ.get("EVENT_AREA", "Palace of Fine Arts")
TZ = os.environ.get("EVENT_TZ", "America/Los_Angeles")
# The web app, for the game link once a group forms (unset: no link).
APP_URL = os.environ.get("APP_URL", "").rstrip("/")

mcp = FastMCP(
    "krew-coordinator",
    instructions=(
        "Krew's coordinator: it finds your person a small group nearby and an AI-designed challenge they can only "
        "win together. Start with `event` for the area and a free-time window, then `checkin` and "
        "`start_challenge`. Other people's agents are invited for you; follow along with `my_match`. You only "
        "ever talk to the coordinator, never to other agents, and it shares coarse info only."
    ),
)
_transport: httpx.AsyncBaseTransport | None = None


def serve_inside(app: FastAPI) -> None:
    """Mounted on the coordinator itself: call its routes in-process instead of over the network."""
    global _transport, BASE
    _transport, BASE = httpx.ASGITransport(app=app), "http://coordinator"


async def _call(method: str, path: str, body: dict | None = None):
    async with httpx.AsyncClient(base_url=BASE, timeout=30, transport=_transport) as http:
        resp = await http.request(method, path, json=body)
        if resp.is_error:
            return {"error": resp.status_code, "detail": resp.json().get("detail", resp.text)}
        return resp.json()


@mcp.tool
def event() -> dict:
    """Where and when: the area to check in with, the local time, and a two-hour free window starting now."""
    now = datetime.now(ZoneInfo(TZ))
    day, start = now.strftime("%a"), now.strftime("%H:%M")
    until = min(now.hour + 2, 23)
    end = f"{until:02d}:{now.minute:02d}" if until == now.hour + 2 else "23:59"
    return {"area": AREA, "now": f"{day} {start}", "free_example": f"{day} {start}-{end}"}


# Outside agents are guests: each gets an id only it knows (the name plus random digits, which the web app drops when
# it shows the name), and may act for that id only, never for a person signed in to the app.
_guests: set[str] = set()
MAX_GUESTS = 30
NOT_YOURS = {"error": 403, "detail": "unknown agent_id: use the one `checkin` gave you"}


def _slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())[:20] or "guest"


@mcp.tool
async def checkin(
    name: str,
    free: str,
    area: str,
    interests: list[str] | None = None,
    skills: list[str] | None = None,
    group_pref: Literal["small", "any", "large"] = "small",
) -> dict:
    """Check your person in for a meetup. `name` is a first name only. Coarse info only: free like
    "Sat 14:00-16:00" and the area from `event`, no exact location. Returns the agent_id for every other tool.
    Then `start_challenge`."""
    if len(_guests) >= MAX_GUESTS:
        return {"error": 429, "detail": "the event is full of outside agents right now"}
    agent_id = f"{_slug(name)}-{secrets.randbelow(900_000) + 100_000}"
    agent = await _call(
        "POST",
        "/agents/checkin",
        {"agent_id": agent_id, "free": free, "area": area, "interests": interests or [],
         "skills": skills or [], "group_pref": group_pref},
    )
    if "error" not in agent:
        _guests.add(agent_id)
    return agent


@mcp.tool
async def start_challenge(agent_id: str, size: int = 3) -> dict:
    """Start a group challenge for your person (check in first). `size` is 2-5 people (the coordinator may require 3); the coordinator invites
    other agents until it's full. As the host you count as accepted; follow the invites with `my_match`."""
    if agent_id not in _guests:
        return NOT_YOURS
    return await _call("POST", "/challenges", {"agent_id": agent_id, "size": size})


@mcp.tool
async def questions(agent_id: str) -> list | dict:
    """Open questions the coordinator is waiting for you to answer (e.g. "does this plan fit?"). An invite waits
    20 seconds for an answer."""
    if agent_id not in _guests:
        return NOT_YOURS
    return await _call("GET", f"/agents/{agent_id}/questions")


@mcp.tool
async def reply(
    agent_id: str, question_id: str, answer: Literal["yes", "no", "counter"], free: str | None = None
) -> dict:
    """Answer a question. For "counter", give the window that works instead, like "Sat 15:00-17:00"."""
    if agent_id not in _guests:
        return NOT_YOURS
    return await _call("POST", f"/agents/{agent_id}/reply", {"question_id": question_id, "answer": answer, "free": free})


@mcp.tool
async def my_match(agent_id: str) -> dict:
    """Your current group as your person's phone shows it: everyone else stays "Agent A, B…" until all have accepted,
    plus the latest steps of the negotiation. Once confirmed: the challenge, your part, and the link your person
    opens on their phone to play."""
    if agent_id not in _guests:
        return NOT_YOURS
    agent = await _call("GET", f"/agents/{agent_id}")
    if "error" in agent or not agent.get("match_id"):
        return agent if "error" in agent else {"match": None}
    match = await _call("GET", f"/matches/{agent['match_id']}")
    feed = await _call("GET", f"/agents/{agent_id}/feed")
    if "error" in match or "error" in feed:
        return match if "error" in match else feed
    view = {
        "match_id": match["match_id"],
        "state": match["state"],
        "size": match.get("size") or len(match["members"]),
        "group": list(feed["labels"].values()),
        "accepted": f"{len(match['accepted'])} of {len(match['members'])}",
        "negotiation": [e["text"] for e in feed["events"][-8:]],
    }
    rnd = (match.get("challenge") or {}).get("round") or {}
    if rnd:  # the shared challenge, but only this player's own part
        view["challenge"] = {k: rnd[k] for k in ("title", "goal", "meet", "timer_min") if rnd.get(k)}
        view["your_part"] = (rnd.get("keys") or {}).get(agent_id)
    if APP_URL and match["state"] in ("confirmed", "playing"):
        view["game_url"] = f"{APP_URL}/?match={match['match_id']}&agent={agent_id}"
    return view


@mcp.tool
async def accept(agent_id: str, match_id: str, accept: bool = True) -> dict:
    """Accept (or decline) the proposed group on your person's behalf, only after they approve it."""
    if agent_id not in _guests:
        return NOT_YOURS
    result = await _call("POST", f"/matches/{match_id}/accept", {"agent_id": agent_id, "accept": accept})
    return result if "error" in result else {"match_id": match_id, "state": result["state"]}


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="http", port=int(sys.argv[sys.argv.index("--http") + 1]))
    else:
        mcp.run()
