"""HTTP surface of the agents service. Run: uvicorn agents.main:app --port 8100 --reload"""

import asyncio
import base64
import binascii
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic
import httpx
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from . import chat, llm, spacetimeauth, sponsors, timeutil
from .config import Settings
from .coordinator_client import CoordinatorClient, CoordinatorError
from .memory import Memory
from .mirror import Mirror
from .models import Answer, ChatIn, ChatLine, Consent, Decision, Feedback, Login, PersonalContext, PhotoCheck, PhotoUpload, Question, SpacetimeLogin, Start
from .place import Place, PlaceService
from .service import AgentsService, Conflict, NotFound
from .store import Store

# The group photo's first bytes say what it is; anything else is refused.
PHOTO_TYPES = ((b"\xff\xd8\xff", "image/jpeg"), (b"\x89PNG", "image/png"), (b"RIFF", "image/webp"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
SEEDS = Path(__file__).parent.parent / "seeds" / "personal_contexts.json"


def load_contexts(path: Path = SEEDS) -> list[PersonalContext]:
    return [PersonalContext.model_validate(c) for c in json.loads(path.read_text(encoding="utf-8"))]


def create_app(
    settings: Settings | None = None,
    claude: anthropic.AsyncAnthropic | None = None,
    coordinator_transport: httpx.AsyncBaseTransport | None = None,
    web_transport: httpx.AsyncBaseTransport | None = None,
    watch: bool = True,
) -> FastAPI:
    s = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with (
            httpx.AsyncClient(base_url=s.coordinator_url, transport=coordinator_transport) as coord_http,
            httpx.AsyncClient(transport=web_transport) as web,
        ):
            mirror = None
            if s.spacetime_url and (s.spacetime_token or s.spacetime_token_file):
                mirror = Mirror(s.spacetime_url, s.spacetime_db, s.spacetime_token, s.spacetime_token_file)
            store = Store(s.db_path, mirror)
            memory = Memory(store, s.moss_project_id, s.moss_project_key, s.moss_index)
            await memory.start()
            claude_client = claude or llm.make_claude(s.use_claude)
            place = PlaceService(s, web, claude_client)
            svc = AgentsService(
                s, store, memory, CoordinatorClient(coord_http), claude_client, place, load_contexts(),
                sponsors.load_booths(), sponsors.load_sponsors(),
            )
            app.state.svc = svc
            app.state.web = web
            tasks = [asyncio.create_task(svc.watch())] if watch else []
            if s.apify_token:
                place.refresh(timeutil.now(s.tz))  # warm the cache so the first group gets scraped facts
            yield
            for t in tasks:
                t.cancel()
            await memory.close()

    app = FastAPI(title="Agents and AI", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def svc() -> AgentsService:
        return app.state.svc

    async def guarded(coro):
        try:
            return await coro
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        except CoordinatorError as e:
            raise HTTPException(e.status if e.status < 500 else 502, e.detail)
        except (ValueError, ValidationError) as e:
            raise HTTPException(422, str(e))

    def user_or_404(agent_id: str):
        try:
            return svc().user(agent_id)
        except NotFound as e:
            raise HTTPException(404, str(e))

    @app.get("/health")
    async def health() -> dict:
        v = svc()
        return {
            "ok": True,
            "users": len(v.store.users),
            "personal_contexts": len(v.contexts),
            "claude": v.claude is not None,
            "moss": v.memory.ready,
            "apify": bool(s.apify_token),
        }

    # --- the sample chatbot: real people -----------------------------------------

    @app.post("/users/login")
    async def login(body: Login) -> dict:
        """After SpacetimeDB sign-in: get this person's agent, loaded with a personal context (picked or the next free one)."""
        try:
            user = svc().login(body)
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        return await svc().status(user)

    @app.get("/auth/config")
    async def auth_config() -> dict:
        """What the sign-in page needs: SpacetimeAuth's address and our client id (public), or none for name-only."""
        client_id = s.spacetimeauth_client_id or None
        return {"spacetimeauth": {"authority": spacetimeauth.ISSUER, "client_id": client_id} if client_id else None}

    @app.post("/users/spacetime")
    async def login_spacetime(body: SpacetimeLogin) -> dict:
        """Sign in with SpacetimeAuth. Keeps the account id and first name only; email and photo are dropped here."""
        try:
            person = await spacetimeauth.sign_in(
                app.state.web, body.code, body.code_verifier, body.redirect_uri,
                s.spacetimeauth_client_id, s.spacetimeauth_client_secret,
            )
        except spacetimeauth.InvalidSignIn as e:
            raise HTTPException(401, str(e))
        return await svc().status(svc().login_person(person))

    @app.post("/users/{agent_id}/consent")
    async def consent(agent_id: str, body: Consent) -> dict:
        """Share = check in with the coordinator (coarse fields only); Not now = stay private."""
        user = user_or_404(agent_id)
        await guarded(svc().consent(user, body.share))
        return await svc().status(user)

    @app.get("/users/{agent_id}")
    async def status(agent_id: str) -> dict:
        """Poll this from the chat and game pages: transcript, match, needs_accept, challenge, my_part, progress."""
        return await guarded(svc().status(user_or_404(agent_id)))

    @app.post("/users/{agent_id}/start")
    async def start(agent_id: str, body: Start | None = None) -> dict:
        """The Start button: start a challenge for `size` people (MIN_GROUP_SIZE-5, default 3). The coordinator invites the rest."""
        user = user_or_404(agent_id)
        async with svc().lock(agent_id):
            await guarded(svc().start(user, (body or Start()).size))
        return await svc().status(user)

    @app.post("/users/{agent_id}/accept")
    async def accept(agent_id: str, body: Decision) -> dict:
        """Accept or decline an invitation."""
        user = user_or_404(agent_id)
        await guarded(svc().respond(user, body.accept))
        return await svc().status(user)

    @app.post("/chat/{agent_id}")
    async def chat_turn(agent_id: str, body: ChatIn) -> dict:
        user = user_or_404(agent_id)
        async with svc().lock(agent_id):
            user.transcript.append(ChatLine(role="user", text=body.message))
            try:
                text = await chat.reply(svc(), user, body.message)
            except anthropic.APIError as e:
                logging.getLogger(__name__).warning("chat failed for %s: %s", agent_id, e)
                text = "Sorry, I lost my train of thought. Try again, or tap Start to begin a challenge."
            user.transcript.append(ChatLine(role="agent", text=text))
            svc().store.save_user(user)
        return {"reply": text, **(await svc().status(user))}

    # --- photos: the game page uploads the group photo here, then puts its link on the board -------

    @app.post("/photos")
    async def upload_photo(body: PhotoUpload) -> dict:
        """Returns the photo's path on this service; the page turns it into a full link."""
        try:
            data = base64.b64decode(body.image_base64, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(422, "image_base64 is not valid base64")
        media_type = next((t for magic, t in PHOTO_TYPES if data.startswith(magic)), None)
        if media_type is None:
            raise HTTPException(422, "send a JPEG, PNG or WebP photo")
        photo_id = svc().store.save_photo(body.match_id, body.agent_id, media_type, data)
        return {"id": photo_id, "path": f"/photos/{photo_id}"}

    @app.get("/photos/{photo_id}")
    async def get_photo(photo_id: str) -> Response:
        found = svc().store.photo(photo_id)
        if found is None:
            raise HTTPException(404, "unknown photo")
        return Response(found[1], media_type=found[0], headers={"Cache-Control": "public, max-age=86400"})

    # --- challenges: photo checks --------------------------------------------------

    @app.post("/challenges/{match_id}/check")
    async def check_photo(match_id: str, body: PhotoCheck) -> dict:
        """One photo for one step. Returns passed + a friendly reason, and the whole progress."""
        return await guarded(svc().check_photo(match_id, body))

    @app.get("/challenges/{match_id}/progress")
    async def progress(match_id: str) -> dict:
        return await guarded(svc().progress_view(match_id))

    # --- the coordinator calls these ---------------------------------------------

    @app.post("/agents/{agent_id}/question")
    async def question(agent_id: str, body: Question) -> Answer:
        """callback_url for every agent we run. Answers the coordinator's invitation in the response body."""
        return await svc().answer(body.model_copy(update={"agent_id": agent_id}))

    @app.post("/challenge")
    async def challenge(body: dict) -> dict:
        """CHALLENGE_DESIGNER_URL. Body: {"match", "members"}; returns the invented challenge."""
        return await svc().design(body)

    # --- personal contexts, feedback, place --------------------------------------

    @app.get("/contexts")
    async def contexts() -> list[dict]:
        """Sample personal contexts, for a picker at sign-in. taken_by = who's using it."""
        return svc().contexts_view()

    @app.post("/contexts/seed")
    async def seed(shift_to_now: bool = False) -> dict:
        """Rehearsals: check unclaimed contexts in as stand-in agents. shift_to_now moves Saturday windows to today."""
        return await guarded(svc().seed_stand_ins(shift_to_now))

    # Paths from the first agents service, kept so main's README commands and old callbacks still work.

    @app.get("/personas")
    async def personas() -> list[dict]:
        return svc().contexts_view()

    @app.post("/personas/checkin-all")
    async def checkin_all(shift_to_now: bool = False) -> dict:
        return await guarded(svc().seed_stand_ins(shift_to_now))

    @app.post("/personas/{agent_id}/ask")
    async def ask(agent_id: str, body: Question) -> Answer:
        return await svc().answer(body.model_copy(update={"agent_id": agent_id}))

    @app.post("/feedback")
    async def feedback(body: Feedback) -> dict:
        return await svc().feedback(body)

    @app.get("/feedback/{match_id}")
    async def feedback_summary(match_id: str, agent_id: str | None = None) -> dict:
        return await svc().feedback_view(match_id, agent_id)

    @app.get("/stats")
    async def stats() -> dict:
        """ "X of Y groups wanted to meet again" for the video and the end screen."""
        return svc().stats()

    @app.get("/place")
    async def place() -> Place:
        return await svc().place.get(timeutil.now(s.tz))

    @app.post("/place/refresh")
    async def place_refresh() -> Place:
        """Scrape now (Apify) and wait for it, up to 90 s."""
        try:
            return await asyncio.wait_for(asyncio.shield(svc().place.refresh(timeutil.now(s.tz))), 90)
        except Exception as e:
            raise HTTPException(502, f"refresh failed: {e!r}")

    return app


app = create_app()
