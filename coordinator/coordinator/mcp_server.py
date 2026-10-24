"""MCP adapter: lets any MCP-capable agent join the coordination layer.

Thin wrapper over the HTTP contract, so it runs next to (or far from) the coordinator:
    COORDINATOR_URL=http://localhost:8000 python -m coordinator.mcp_server            # stdio
    COORDINATOR_URL=... python -m coordinator.mcp_server --http 8765                   # streamable HTTP
"""

import os
import sys
from typing import Literal

import httpx
from fastmcp import FastMCP

BASE = os.environ.get("COORDINATOR_URL", "http://localhost:8000")
mcp = FastMCP("cal-hacks-coordinator")


async def _call(method: str, path: str, body: dict | None = None):
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as http:
        resp = await http.request(method, path, json=body)
        if resp.is_error:
            return {"error": resp.status_code, "detail": resp.json().get("detail", resp.text)}
        return resp.json()


@mcp.tool
async def checkin(
    agent_id: str,
    free: str,
    area: str,
    interests: list[str] | None = None,
    skills: list[str] | None = None,
    group_pref: Literal["small", "any", "large"] = "small",
) -> dict:
    """Check your person in for a meetup. Coarse info only: free like "Sat 14:00-16:00", area like
    "UC Berkeley", no names or exact location. Then poll `questions` and answer with `reply`."""
    return await _call(
        "POST",
        "/agents/checkin",
        {"agent_id": agent_id, "free": free, "area": area, "interests": interests or [],
         "skills": skills or [], "group_pref": group_pref},
    )


@mcp.tool
async def questions(agent_id: str) -> list | dict:
    """Open questions the coordinator is waiting for you to answer (e.g. "does this plan fit?")."""
    return await _call("GET", f"/agents/{agent_id}/questions")


@mcp.tool
async def reply(
    agent_id: str, question_id: str, answer: Literal["yes", "no", "counter"], free: str | None = None
) -> dict:
    """Answer a question. For "counter", give the window that works instead, like "Sat 15:00-17:00"."""
    return await _call("POST", f"/agents/{agent_id}/reply", {"question_id": question_id, "answer": answer, "free": free})


@mcp.tool
async def my_match(agent_id: str) -> dict:
    """Your current group: members, roles, state and, once confirmed, your challenge key."""
    agent = await _call("GET", f"/agents/{agent_id}")
    if "error" in agent or not agent.get("match_id"):
        return agent if "error" in agent else {"match": None}
    return await _call("GET", f"/matches/{agent['match_id']}")


@mcp.tool
async def accept(agent_id: str, match_id: str, accept: bool = True) -> dict:
    """Accept (or decline) the proposed group on your person's behalf, only after they approve it."""
    return await _call("POST", f"/matches/{match_id}/accept", {"agent_id": agent_id, "accept": accept})


if __name__ == "__main__":
    if "--http" in sys.argv:
        mcp.run(transport="http", port=int(sys.argv[sys.argv.index("--http") + 1]))
    else:
        mcp.run()
