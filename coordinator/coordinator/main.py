"""HTTP + WebSocket surface of the coordinator. Run: uvicorn coordinator.main:app --reload"""

import asyncio
import contextlib
import json
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from .broker import Broker
from .coordinator import Conflict, Coordinator, NotFound
from .events import EventLog
from .mcp_server import MUSE_SETUP, mcp, serve_inside
from .models import Accept, Agent, Ask, CheckIn, Leave, Match, MatchState, NewChallenge, Question, Reply, StateChange
from .mirror import Mirror
from .spacetime import Spacetime
from .store import Store

SEEDS = Path(__file__).parent.parent / "seeds" / "personas.json"


def make_claude() -> anthropic.AsyncAnthropic | None:
    if os.environ.get("MATCH_WITH_CLAUDE", "1") == "0":
        return None
    try:
        return anthropic.AsyncAnthropic(timeout=30.0, max_retries=1)
    except anthropic.AnthropicError:  # no credentials: matching falls back to the rule score
        return None


def make_spacetime(http: httpx.AsyncClient) -> Spacetime | None:
    """Live game board. The token must be the identity that published game/spacetimedb
    (`spacetime login show --token`), since only it may start rounds."""
    token = os.environ.get("SPACETIME_TOKEN")
    if not token:
        return None
    url = os.environ.get("SPACETIME_URL", "http://127.0.0.1:3000")
    return Spacetime(http, url, os.environ.get("SPACETIME_DB", "threekey"), token, os.environ.get("SPACETIME_TOKEN_FILE"))


def make_mirror() -> Mirror | None:
    """Copy agents and matches to SpacetimeDB (the same publisher token as the live board) to survive restarts."""
    token, token_file = os.environ.get("SPACETIME_TOKEN", ""), os.environ.get("SPACETIME_TOKEN_FILE", "")
    if not (token or token_file):
        return None
    url = os.environ.get("SPACETIME_URL", "http://127.0.0.1:3000")
    return Mirror(url, os.environ.get("SPACETIME_DB", "threekey"), token, token_file)


def create_app(
    db_path: str = os.environ.get("COORDINATOR_DB", "coordinator.db"),
    claude: anthropic.AsyncAnthropic | None = None,
    use_claude: bool = True,
    reply_timeout: float = float(os.environ.get("REPLY_TIMEOUT_S", "20")),
    spacetime: Callable[[httpx.AsyncClient], Spacetime | None] = make_spacetime,
    game_poll_s: float = 1.5,
    mirror: Callable[[], Mirror | None] = make_mirror,
) -> FastAPI:
    mcp_app = mcp.http_app(path="/mcp")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_app.lifespan(app), httpx.AsyncClient() as http:
            app.state.coord = Coordinator(
                store=Store(db_path, mirror()),
                events=EventLog(),
                broker=Broker(http, timeout=reply_timeout),
                http=http,
                claude=claude or (make_claude() if use_claude else None),
                challenge_url=os.environ.get("CHALLENGE_DESIGNER_URL"),
                spacetime=spacetime(http),
            )
            watcher = asyncio.create_task(app.state.coord.watch_games(game_poll_s))
            yield
            watcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher

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
        return {
            "ok": True,
            "agents": len(c.store.agents),
            "matches": len(c.store.matches),
            "claude": c.claude is not None,
            "spacetime": c.spacetime is not None,
        }

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

    # --- host-led challenges -----------------------------------------------------

    @app.post("/challenges")
    async def start_challenge(body: NewChallenge) -> Match:
        """The host picks the size (MIN_GROUP_SIZE-5); the coordinator invites other agents in the background until it's full."""
        return guarded(coord().start_challenge, body)

    @app.post("/challenges/{match_id}/start")
    async def start_now(match_id: str) -> Match:
        """Start with whoever accepted, if that's at least MIN_GROUP_SIZE. Unfilled seats are dropped."""
        return guarded(coord().start_now, match_id)

    @app.post("/challenges/{match_id}/cancel")
    async def cancel(match_id: str) -> Match:
        return guarded(coord().cancel, match_id)

    # --- matching, approval, game ------------------------------------------------

    @app.post("/matching/run")
    async def run_matching() -> list[Match]:
        """Testing only: split the whole pool into groups of GROUP_SIZE with fixed roles. The app uses /challenges."""
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

    @app.post("/matches/{match_id}/leave")
    async def leave(match_id: str, body: Leave) -> Match:
        """Leave the challenge at any point; see Coordinator.leave for what happens to the others."""
        try:
            return await coord().leave(match_id, body.agent_id)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))

    @app.put("/matches/{match_id}/challenge")
    async def put_challenge(match_id: str, body: dict) -> Match:
        """Challenge designer (agents team) can push a round directly instead of being called."""
        guarded(coord().set_challenge, match_id, body)
        return await coord().start_game(match_id)

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

    @app.get("/agents/{agent_id}/feed")
    async def feed(agent_id: str) -> dict:
        """One person's live screen: their agent, the coordinator, and the agents considered for their challenge."""
        return coord().feed(agent_id)

    @app.get("/log")
    async def log_history() -> list[dict]:
        return coord().events.history

    @app.get("/connect/muse", response_class=PlainTextResponse)
    async def connect_muse(request: Request) -> str:
        """Setup notes Meta Muse reads to build its Krew integration (Muse has no menu for MCP servers)."""
        scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        return MUSE_SETUP.format(mcp=f"{scheme}://{request.url.netloc}/mcp")

    # Any MCP agent (a judge's own Claude as a connector) joins the live event at /mcp. Mounted last, so every route
    # above still wins.
    serve_inside(app)
    app.mount("/", mcp_app)
    return app


app = create_app()
