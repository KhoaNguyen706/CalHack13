"""Agents service: the personal agents behind the coordinator. Run: uvicorn agents.main:app --port 8100"""

import os
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import personas as persona_store
from . import sponsors
from .designer import ChallengeRequest, design
from .models import Persona, Question, Reply
from .persona_agent import decide


def make_claude() -> anthropic.AsyncAnthropic | None:
    if os.environ.get("AGENT_WITH_CLAUDE", "1") == "0":
        return None
    try:
        # Each caller caps its own wait (persona answers 12 s, challenge design 40 s).
        return anthropic.AsyncAnthropic(timeout=45.0, max_retries=0)
    except anthropic.AnthropicError:  # no credentials: agents decide with rules
        return None


def create_app(
    personas_dir: Path = persona_store.PERSONAS_DIR,
    data_dir: Path = sponsors.DATA_DIR,
    coordinator_url: str = os.environ.get("COORDINATOR_URL", "http://localhost:8000"),
    # How the coordinator reaches this service (http://agents:8100 inside Docker Compose).
    public_url: str = os.environ.get("AGENTS_PUBLIC_URL", "http://localhost:8100"),
    claude: anthropic.AsyncAnthropic | None = None,
    use_claude: bool = True,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(base_url=coordinator_url, transport=transport, timeout=10) as http:
            app.state.http = http
            app.state.claude = claude or (make_claude() if use_claude else None)
            app.state.personas = persona_store.load(personas_dir)
            app.state.booths = sponsors.load_booths(data_dir)
            app.state.sponsors = sponsors.load_sponsors(data_dir)
            app.state.booth_uses = Counter()  # spreads groups across booths
            yield

    app = FastAPI(title="Agents", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def persona_or_404(agent_id: str) -> Persona:
        persona = app.state.personas.get(agent_id)
        if persona is None:
            raise HTTPException(404, f"unknown persona {agent_id}")
        return persona

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True, "personas": len(app.state.personas), "claude": app.state.claude is not None}

    # --- persona agents ----------------------------------------------------------

    @app.get("/personas")
    async def list_personas() -> list[Persona]:
        return list(app.state.personas.values())

    @app.post("/personas/checkin-all")
    async def checkin_all() -> dict:
        """Check every persona in with the coordinator. Their agents then answer its asks through /ask."""
        for p in app.state.personas.values():
            resp = await app.state.http.post("/agents/checkin", json=p.checkin(f"{public_url}/personas/{p.agent_id}/ask"))
            if resp.is_error:
                raise HTTPException(502, f"coordinator rejected {p.agent_id}: {resp.text}")
        return {"checked_in": len(app.state.personas)}

    @app.post("/personas/{agent_id}/ask", response_model_exclude_none=True)
    async def ask(agent_id: str, body: Question) -> Reply:
        """The coordinator's callback: answer yes / no / counter in the response body."""
        return await decide(persona_or_404(agent_id), body, app.state.claude)

    # --- challenge designer ------------------------------------------------------

    @app.post("/challenge")
    async def challenge(body: ChallengeRequest) -> dict:
        """CHALLENGE_DESIGNER_URL: the split_riddle round for a confirmed group."""
        s = app.state
        return await design(body, s.booths, s.sponsors, s.claude, s.booth_uses)

    return app


app = create_app()
