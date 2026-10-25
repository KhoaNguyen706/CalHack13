"""Settings, read from the environment and agents/.env (copy .env.example)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _flag(name: str, default: str) -> bool:
    return _env(name, default).lower() not in ("0", "false", "no", "")


@dataclass(frozen=True)
class Settings:
    coordinator_url: str = _env("COORDINATOR_URL", "http://localhost:8000")
    # Where the coordinator reaches this service; every agent's callback_url is built from it.
    public_url: str = _env("AGENTS_PUBLIC_URL", "http://localhost:8100")
    db_path: str = _env("AGENTS_DB", "agents.db")
    # Save everything to SpacetimeDB too (the game database), so restarts keep people signed in. Needs the
    # publisher token (the same SPACETIME_TOKEN as the coordinator), or a file holding it under docker compose.
    spacetime_url: str = _env("SPACETIME_URL")
    spacetime_db: str = _env("SPACETIME_DB", "threekey")
    spacetime_token: str = _env("SPACETIME_TOKEN")
    spacetime_token_file: str = _env("SPACETIME_TOKEN_FILE")
    cache_dir: Path = Path(_env("AGENTS_CACHE", str(ROOT / "cache")))

    use_claude: bool = _flag("AGENTS_WITH_CLAUDE", _env("AGENT_WITH_CLAUDE", "1"))  # either name works
    model: str = _env("AGENT_MODEL", "claude-opus-5-5")
    designer_model: str = _env("DESIGNER_MODEL", _env("AGENT_MODEL", "claude-opus-5-5"))
    # Must stay under the coordinator's REPLY_TIMEOUT_S (20) and its 60 s challenge wait.
    reply_budget_s: float = float(_env("REPLY_BUDGET_S", "12"))
    design_budget_s: float = float(_env("DESIGN_BUDGET_S", "45"))
    vision_budget_s: float = float(_env("VISION_BUDGET_S", "20"))

    event: str = _env("EVENT_NAME", "Cal Hacks 13")
    location: str = _env("EVENT_LOCATION", "Palace of Fine Arts")
    city: str = _env("EVENT_CITY", "San Francisco")
    # Coarse area every agent checks in with. Matching only groups agents in the same area.
    area: str = _env("EVENT_AREA", "Palace of Fine Arts")
    tz: str = _env("EVENT_TZ", "America/Los_Angeles")
    # Where every new group meets first, e.g. "the rotunda steps, facing the lagoon". Unset: Claude picks a spot
    # from the place facts and booths, else the venue's main entrance.
    meeting_point: str = _env("MEETING_POINT")
    # For a live demo: every new challenge sends the group to this one spot (an id from data/spots.json, e.g.
    # "spacetime_booth"). Unset: a spot is picked at random per match.
    demo_spot: str = _env("DEMO_SPOT")
    # Its place on the map, for the in-app map and "Directions". Unset: the venue (EVENT_LOCATION, EVENT_CITY) looked up
    # on OpenStreetMap. COORDS is "lat,lng", e.g. "37.8029,-122.4484"; ADDRESS is a street address.
    meeting_point_address: str = _env("MEETING_POINT_ADDRESS")
    meeting_point_coords: str = _env("MEETING_POINT_COORDS")

    # Sign in with SpacetimeAuth: the client id from the database's SpacetimeAuth dashboard (public), plus its
    # secret if the client is confidential. Unset: only the name sign-in works.
    spacetimeauth_client_id: str = _env("SPACETIMEAUTH_CLIENT_ID")
    spacetimeauth_client_secret: str = _env("SPACETIMEAUTH_CLIENT_SECRET")
    apify_token: str = _env("APIFY_TOKEN")
    moss_project_id: str = _env("MOSS_PROJECT_ID")
    moss_project_key: str = _env("MOSS_PROJECT_KEY")
    moss_index: str = _env("MOSS_INDEX", "agent-memory")

    # Stand-in agents (a personal context with no real person behind it) accept their groups on their own.
    auto_accept: bool = _flag("STAND_IN_AUTO_ACCEPT", "1")
    watch_every_s: float = float(_env("WATCH_EVERY_S", "2"))
