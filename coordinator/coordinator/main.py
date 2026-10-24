"""HTTP + WebSocket surface of the coordinator. Run: uvicorn coordinator.main:app --reload"""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .broker import Broker
from .coordinator import Conflict, Coordinator, NotFound
from .events import EventLog
from .models import Accept, Agent, Ask, CheckIn, Match, MatchState, Question, Reply, StateChange
from .store import Store

SEEDS = Path(__file__).parent.parent / "seeds" / "personas.json"


def make_claude() -> anthropic.AsyncAnthropic | None:
    if os.environ.get("MATCH_WITH_CLAUDE", "1") == "0":
        return None
    try:
        return anthropic.AsyncAnthropic(timeout=30.0, max_retries=1)
    except anthropic.AnthropicError:  # no credentials: matching falls back to the rule score
        return None


def create_app(
    db_path: str = os.environ.get("COORDINATOR_DB", "coordinator.db"),
    claude: anthropic.AsyncAnthropic | None = None,
    use_claude: bool = True,
    reply_timeout: float = float(os.environ.get("REPLY_TIMEOUT_S", "20")),
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient() as http:
            app.state.coord = Coordinator(
                store=Store(db_path),
                events=EventLog(),
                broker=Broker(http, timeout=reply_timeout),
                http=http,
                claude=claude or (make_claude() if use_claude else None),
                challenge_url=os.environ.get("CHALLENGE_DESIGNER_URL"),
            )
            yield

    app = FastAPI(title="AI Coordination Layer", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def coord() -> Coordinator:
        return app.state.coord

    def agent_or_404(agent_id: str) -> Agent:
        try:
            return coord().agent(agent_id)
        except NotFound as e:
            raise HTTPException(404, str(e))

    def guarded(fn, *args):
        try:
            return fn(*args)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except ValueError as e:
            raise HTTPException(422, str(e))

    @app.get("/health")
    async def health() -> dict:
        c = coord()
        return {"ok": True, "agents": len(c.store.agents), "matches": len(c.store.matches), "claude": c.claude is not None}

    # Routes are async so state changes and log events stay on the event loop (no threadpool).

    # --- agent interface (frozen contract) ---------------------------------------

    @app.post("/agents/checkin")
    async def checkin(body: CheckIn) -> Agent:
        return coord().checkin(body)

    @app.get("/agents")
    async def agents() -> list[Agent]:
        return list(coord().store.agents.values())

    @app.get("/agents/{agent_id}")
    async def get_agent(agent_id: str) -> Agent:
        return agent_or_404(agent_id)

    @app.post("/agents/{agent_id}/ask")
    async def ask(agent_id: str, body: Ask) -> Reply:
        """Coordinator asks an agent a question and waits for its reply (or the timeout)."""
        return await coord().broker.ask(agent_or_404(agent_id), body.question, body.plan)

    @app.get("/agents/{agent_id}/questions")
    async def questions(agent_id: str) -> list[Question]:
        """Open questions for agents without a callback_url (e.g. the web chat agent polls this)."""
        agent_or_404(agent_id)
        return coord().broker.questions_for(agent_id)

    @app.post("/agents/{agent_id}/reply")
    async def reply(agent_id: str, body: Reply) -> dict:
        agent_or_404(agent_id)
        if not coord().broker.reply(agent_id, body):
            raise HTTPException(404, "no open question to answer")
        return {"ok": True}

    # --- matching, approval, game ------------------------------------------------

    @app.post("/matching/run")
    async def run_matching() -> list[Match]:
        return await coord().run_matching()

    @app.get("/matches")
    async def matches() -> list[Match]:
        return list(coord().store.matches.values())

    @app.get("/matches/{match_id}")
    async def get_match(match_id: str) -> Match:
        return guarded(coord().match, match_id)

    @app.post("/matches/{match_id}/accept")
    async def accept(match_id: str, body: Accept) -> Match:
        return guarded(coord().accept, match_id, body)

    @app.put("/matches/{match_id}/challenge")
    async def put_challenge(match_id: str, body: dict) -> Match:
        """Challenge designer (agents team) can push a round directly instead of being called."""
        return guarded(coord().set_challenge, match_id, body)

    @app.post("/matches/{match_id}/state")
    async def set_state(match_id: str, body: StateChange) -> Match:
        return guarded(coord().set_state, match_id, MatchState(body.state))

    # --- demo helpers ------------------------------------------------------------

    @app.post("/demo/seed")
    async def seed() -> dict:
        """Check in the seeded personas. Ones without a callback_url auto-answer yes."""
        personas = json.loads(SEEDS.read_text(encoding="utf-8"))
        for p in personas:
            c = CheckIn.model_validate(p)
            coord().checkin(c, auto_reply=c.callback_url is None)
        return {"seeded": len(personas)}

    @app.post("/demo/reset")
    async def reset() -> dict:
        coord().store.reset()
        coord().events.history.clear()
        coord().events.emit("coordinator", "demo reset")
        return {"ok": True}

    # --- negotiation log ---------------------------------------------------------

    @app.websocket("/ws/log")
    async def log_socket(ws: WebSocket):
        """Replays history, then streams every event live. Each message: {seq, ts, kind, text, data}."""
        await ws.accept()
        events = coord().events
        queue = events.subscribe()
        try:
            for event in list(events.history):
                await ws.send_json(event)
            while True:
                await ws.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            events.unsubscribe(queue)

    @app.get("/log")
    async def log_history() -> list[dict]:
        return coord().events.history

    return app


app = create_app()
