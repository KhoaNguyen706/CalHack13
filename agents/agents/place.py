"""What's true about the event, the place and the hour, scraped with Apify, for inventing challenges.

Pages come from Apify's RAG Web Browser (Google search + page markdown). Claude boils them down to short
facts a challenge can use, and page photos (og:image) become reference photos ("find this spot").
Results are cached per event, place and hour in agents/cache/, so the challenge designer never waits on a
scrape: it takes the freshest cache and a refresh runs behind it.
"""

import asyncio
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path

import anthropic
import httpx
from pydantic import BaseModel

from . import llm
from .config import Settings

log = logging.getLogger(__name__)

RAG_BROWSER = "https://rag-web-browser.apify.actor/search"
PAGE_CHARS = 4000  # per scraped page sent to Claude
MAX_IMAGES = 8


class Fact(BaseModel):
    text: str
    source: str = ""  # URL it came from


class Facts(BaseModel):
    facts: list[Fact]


class Image(BaseModel):
    url: str
    about: str  # title of the page it came from
    source: str


class Place(BaseModel):
    event: str
    location: str
    city: str
    hour: str  # "2026-10-24T14", local time
    facts: list[Fact]
    images: list[Image] = []
    scraped: bool  # False = built-in facts, nothing scraped yet
    fetched_at: float


CONDENSE = """You turn scraped web pages into facts for outdoor group challenges at {event}, \
held at the {location} in {city}. Pull 8 to 12 short facts (under 25 words each) that a challenge could use: \
things people can see or find at or near the venue (architecture, landmarks, nature, objects, views), \
a little history of the place, and fun facts about the event or hackathons. Only facts the pages support, \
each with the URL it came from. Nothing about private individuals."""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


async def rag_search(http: httpx.AsyncClient, token: str, query: str, max_results: int = 3) -> list[dict]:
    """One search with Apify's RAG Web Browser (standby mode): the top pages, each with its markdown and metadata
    (incl. Open Graph). [] when the search fails."""
    try:
        resp = await http.get(
            RAG_BROWSER,
            params={"query": query, "maxResults": max_results, "requestTimeoutSecs": 40},
            headers={"Authorization": f"Bearer {token}"},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("Apify query %r failed: %s", query, e)
        return []


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and (e := task.exception()):
        log.warning("place refresh failed: %r", e)


class PlaceService:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, claude: anthropic.AsyncAnthropic | None):
        self.s = settings
        self.http = http
        self.claude = claude
        self.dir = Path(settings.cache_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._refreshing: asyncio.Task | None = None

    # --- reading -----------------------------------------------------------------

    async def get(self, at: datetime, wait_s: float = 0) -> Place:
        """This hour's place info. On a miss: start a refresh, wait up to wait_s, else the freshest older cache."""
        if hit := self._read(self._path(at)):
            return hit
        if not self.s.apify_token:
            return self.latest() or self.builtin(at)
        task = self.refresh(at)
        if wait_s > 0:
            try:
                return await asyncio.wait_for(asyncio.shield(task), wait_s)
            except Exception as e:  # slow or failed scrape: older cache or built-in facts
                log.warning("place refresh not ready: %r", e)
        return self.latest() or self.builtin(at)

    def latest(self) -> Place | None:
        files = sorted(self.dir.glob(f"{self._prefix()}__*.json"))
        return self._read(files[-1]) if files else None

    def builtin(self, at: datetime) -> Place:
        s = self.s
        facts = [
            f"{s.event} is a hackathon at the {s.location} in {s.city}.",
            "Hackers build a project from scratch in about 36 hours, then demo it to judges.",
            "Sponsors run booths and workshops, and hand out swag.",
            "The snack and coffee tables are the busiest spots after midnight.",
        ]
        return Place(
            event=s.event, location=s.location, city=s.city, hour=self._hour(at),
            facts=[Fact(text=f) for f in facts], scraped=False, fetched_at=time.time(),
        )

    # --- refreshing --------------------------------------------------------------

    def refresh(self, at: datetime) -> asyncio.Task:
        """One refresh at a time; callers share the running one."""
        if self._refreshing is None or self._refreshing.done():
            self._refreshing = asyncio.create_task(self._refresh(at))
            self._refreshing.add_done_callback(_log_failure)
        return self._refreshing

    async def _refresh(self, at: datetime) -> Place:
        if not self.s.apify_token:
            raise RuntimeError("APIFY_TOKEN is not set")
        pages = await self.scrape(self.queries(at))
        if not pages:
            raise RuntimeError("Apify returned no pages")
        facts = await self._condense(pages)
        place = Place(
            event=self.s.event, location=self.s.location, city=self.s.city, hour=self._hour(at),
            facts=facts, images=images_of(pages), scraped=True, fetched_at=time.time(),
        )
        self._path(at).write_text(place.model_dump_json(indent=1), encoding="utf-8")
        log.info("place cached: %d facts, %d photos from %d pages", len(facts), len(place.images), len(pages))
        return place

    def queries(self, at: datetime) -> list[str]:
        s = self.s
        return [
            f"{s.location} {s.city} history architecture landmarks",
            f"{s.event} hackathon {s.city}",
            f"{s.location} {s.city} events {at:%B %Y}",
        ]

    async def scrape(self, queries: list[str]) -> list[dict]:
        """Run the RAG Web Browser Actor for each query in parallel; failed queries are skipped."""
        batches = await asyncio.gather(*(rag_search(self.http, self.s.apify_token, q) for q in queries))
        pages = [p for batch in batches for p in batch]
        return [
            {
                "url": (p.get("metadata") or {}).get("url") or (p.get("searchResult") or {}).get("url", ""),
                "title": (p.get("metadata") or {}).get("title", ""),
                "text": (p.get("markdown") or (p.get("searchResult") or {}).get("description") or "")[:PAGE_CHARS],
                "og": (p.get("metadata") or {}).get("openGraph") or [],
            }
            for p in pages
            if p.get("markdown") or (p.get("searchResult") or {}).get("description")
        ]

    async def _condense(self, pages: list[dict]) -> list[Fact]:
        if self.claude is not None:
            try:
                system = CONDENSE.format(event=self.s.event, location=self.s.location, city=self.s.city)
                content = json.dumps([{k: p[k] for k in ("url", "title", "text")} for p in pages], indent=1)
                out = await llm.parse(self.claude, self.s.model, system=system, content=content, output=Facts, effort="low")
                if out and out.facts:
                    return out.facts[:12]
            except Exception as e:
                log.warning("condensing facts failed: %r", e)
        # Without Claude: each page's first sentence.
        return [Fact(text=re.split(r"(?<=[.!?])\s", p["text"].strip(), maxsplit=1)[0][:200], source=p["url"]) for p in pages][:12]

    # --- cache files -------------------------------------------------------------

    def _prefix(self) -> str:
        return f"{slug(self.s.event)}__{slug(self.s.location)}"

    def _hour(self, at: datetime) -> str:
        return at.strftime("%Y-%m-%dT%H")

    def _path(self, at: datetime) -> Path:
        return self.dir / f"{self._prefix()}__{at:%Y%m%d%H}.json"

    @staticmethod
    def _read(path: Path) -> Place | None:
        try:
            return Place.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None


def images_of(pages: list[dict]) -> list[Image]:
    """Page photos (og:image), one per page, for challenges that show a reference photo."""
    found: dict[str, Image] = {}
    for p in pages:
        og = p.get("og") or []
        tags = og if isinstance(og, list) else [{"property": k, "content": v} for k, v in og.items()]
        url = next((t.get("content") for t in tags if t.get("property") in ("og:image", "og:image:url")), None)
        if isinstance(url, str) and url.startswith("https://") and url not in found:
            found[url] = Image(url=url, about=p.get("title", ""), source=p.get("url", ""))
    return list(found.values())[:MAX_IMAGES]
