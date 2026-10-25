"""Pose ideas found on the web with Apify, for Spot & Pose: simple, popular, funny poses for 2-5 people.

Apify's RAG Web Browser runs simple searches ("popular funny poses for 3 people", "famous meme pose with 3 people")
and returns the pages with their images. Claude looks at the candidates in one go and keeps only single photos with
exactly that many people side by side, doing a pose anyone can copy in a few seconds, and says where to cut between
people so each player gets one. Kept images are copied into the store (served at /photos/<id>), so a link that
expires can't break a round.

Searching takes 30-50 s, so a group never waits on it: they get a pose already found, or a hand-picked meme
(data/poses.json), which fill in until a size has TARGET poses. What's found is kept in the store (copied to SpacetimeDB), so a restart
or a deploy doesn't search again. A size searches only until it has TARGET poses, then once a day for variety; every
search uses phrasings not tried that day, and images already judged (kept or not) never go to Claude again.
"""

import asyncio
import base64
import hashlib
import logging
import re
import time

import anthropic
import httpx
from pydantic import BaseModel

from . import llm, vision
from .config import Settings
from .designer import Pose
from .place import images_of, rag_search
from .store import Store

log = logging.getLogger(__name__)

SIZES = (2, 3, 4, 5)
TARGET = 6  # poses per group size; below this, keep searching
MAX_KEPT = 10  # poses kept per group size; newest first
MAX_CANDIDATES = 12  # new images Claude judges per group size, per search
MAX_SEEN = 400  # images remembered per group size, so they're never judged twice
PER_PAGE = 3  # images taken from each page, after its og:image
QUERIES_PER_SEARCH = 2
MIN_BYTES = 8_000  # smaller is an icon or an avatar
MIN_PART = 0.08  # each player's part is at least this share of the width
FULL_S = 24 * 3600  # a size with TARGET poses searches again after this, for variety
RETRY_S = 15 * 60  # a size below TARGET searches again after this
QUERY_COOLDOWN_S = 24 * 3600  # a phrasing isn't searched again within this

QUERIES = (  # best first: meme searches find the most simple, iconic poses
    "famous {n} person meme pose",
    "popular funny poses for {n} people",
    "{n} friends side by side funny pose",
    "iconic {n} person pose to recreate",
    "easy funny group photo poses for {n} friends",
    "silly {n} person photo pose ideas",
)

JUDGE = """You pick pose ideas for a game: {n} people who just met copy a pose from a photo at a public spot, then \
take a group photo doing it. Each player gets one vertical strip of the photo, so it must split cleanly into {n} \
parts, one person each. Judge every numbered image.

An image is usable only if all of this is true:
- It's one photo or one drawing: not a collage, grid or screenshot, and no big text or captions over it.
- It shows exactly {n} people (or characters), side by side enough that a vertical cut between neighbours gives \
each their own part.
- The pose is simple: anyone gets it at a glance and can do it in a few seconds, standing or crouching, with arms, \
hands and faces (pointing, covering eyes, heart hands, a superhero stance, a shocked face). No jumping, kicking, \
balancing on one leg, lifting or carrying anyone, and no moves that need practice.
- It's funny or iconic: a popular meme pose or a classic silly group pose. Ordinary portraits where people just \
stand and smile at the camera don't count.
- It's safe to do with strangers in public: nothing risky, romantic or suggestive, no props they wouldn't have.

For each usable image:
- `name`: 2-5 words for the pose, like "See no evil trio". It's revealed at the end.
- `pose`: one sentence on what each person does, for checking a photo later. Positions and gestures only, never \
anyone's looks.
- `cuts`: {parts} numbers from 0 to 1, left to right: 0, the x position between each pair of neighbours, then 1.
- `top`: how much empty space (sky, ceiling) to crop off the top, from 0 to 0.5.
List every image by its `index`; the rest are `usable: false`."""


class Judged(BaseModel):
    index: int
    usable: bool
    name: str = ""
    pose: str = ""
    cuts: list[float] = []
    top: float = 0


class Verdicts(BaseModel):
    images: list[Judged]


class Candidate(BaseModel):
    url: str
    page: str


def queries(n: int) -> list[str]:
    return [q.format(n=n) for q in QUERIES]


def image_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def bigger(url: str) -> str:
    """Pinterest lists small thumbnails (/236x/); the same pin comes larger at /736x/."""
    return re.sub(r"(//i\.pinimg\.com)/\d+x/", r"\1/736x/", url)


def candidates(pages: list[dict], limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """Images worth judging: each page's og:image, then a few from its text, round-robin across pages."""
    per_page: list[list[Candidate]] = []
    for p in pages:
        page = (p.get("metadata") or {}).get("url") or (p.get("searchResult") or {}).get("url", "")
        og = [i.url for i in images_of([{"og": (p.get("metadata") or {}).get("openGraph") or [], "url": page}])]
        inline = re.findall(r"!\[[^\]]*\]\((https://[^)\s]+)", p.get("markdown") or "")[:PER_PAGE]
        per_page.append([Candidate(url=bigger(u), page=page) for u in og + inline])
    out: list[Candidate] = []
    seen: set[str] = set()
    for i in range(max((len(c) for c in per_page), default=0)):
        for found in per_page:
            if i < len(found) and found[i].url not in seen:
                seen.add(found[i].url)
                out.append(found[i])
    return out[:limit]


def checked(j: Judged, n: int) -> Judged | None:
    """Claude's verdict if it's a pose we can use for n players, with the cuts tidied; else None."""
    cuts = [min(1.0, max(0.0, c)) for c in j.cuts]
    if not j.usable or len(cuts) != n + 1 or not j.name.strip() or not j.pose.strip():
        return None
    cuts[0], cuts[-1] = 0.0, 1.0
    if any(b - a < MIN_PART for a, b in zip(cuts, cuts[1:])):
        return None
    return j.model_copy(update={"cuts": [round(c, 4) for c in cuts], "top": min(0.5, max(0.0, j.top))})


def _log_failure(task: asyncio.Task) -> None:
    if not task.cancelled() and (e := task.exception()):
        log.warning("pose search failed: %r", e)


class PoseSearch:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, claude: anthropic.AsyncAnthropic | None, store: Store):
        self.s = settings
        self.http = http
        self.claude = claude
        self.store = store
        self._refreshing: asyncio.Task | None = None

    @property
    def enabled(self) -> bool:
        return bool(self.s.apify_token) and self.claude is not None

    # --- reading -----------------------------------------------------------------

    def found(self, n: int) -> list[Pose]:
        """Poses found for n players, newest first; [] before the first search."""
        return [Pose.model_validate(p) for p in self.store.pose_cache(n).get("poses", [])]

    def stale(self, n: int) -> bool:
        """Whether n players' poses are due a search: soon while there are fewer than TARGET, else once a day."""
        state = self.store.pose_cache(n)
        age = time.time() - state.get("fetched_at", 0)
        if len(state.get("poses", [])) >= TARGET:
            return age > FULL_S
        return age > RETRY_S and bool(self._next_queries(n, state))

    def poses_for(self, n: int, hand_picked: list[Pose]) -> list[Pose]:
        """What a group of n can get: the poses found for them, plus the hand-picked memes until there are TARGET, so
        groups get some variety while the search fills up. A size that's due gets searched in the background, for
        the next group."""
        if self.enabled and self.stale(n):
            self.refresh([n])
        found = self.found(n)
        return found if len(found) >= TARGET else found + hand_picked

    # --- searching ---------------------------------------------------------------

    def refresh(self, sizes: list[int] | tuple[int, ...] = SIZES) -> asyncio.Task:
        """One search at a time; callers share the running one."""
        if self._refreshing is None or self._refreshing.done():
            self._refreshing = asyncio.create_task(self._refresh(list(sizes)))
            self._refreshing.add_done_callback(_log_failure)
        return self._refreshing

    async def _refresh(self, sizes: list[int]) -> dict[int, int]:
        """Search each group size in parallel; returns how many poses each one has now."""
        if not self.enabled:
            raise RuntimeError("pose search needs APIFY_TOKEN and Claude")
        kept = await asyncio.gather(*(self._search(n) for n in sizes))
        return dict(zip(sizes, kept))

    @staticmethod
    def _next_queries(n: int, state: dict) -> list[str]:
        """Phrasings not searched in the last day, never-tried first."""
        used = state.get("queries", {})
        fresh = [q for q in queries(n) if time.time() - used.get(q, 0) > QUERY_COOLDOWN_S]
        return sorted(fresh, key=lambda q: used.get(q, 0))[:QUERIES_PER_SEARCH]

    async def _search(self, n: int) -> int:
        state = self.store.pose_cache(n)
        old = [Pose.model_validate(p) for p in state.get("poses", [])]
        todo = self._next_queries(n, state)
        if not todo:
            return len(old)
        batches = await asyncio.gather(*(rag_search(self.http, self.s.apify_token, q, max_results=4) for q in todo))
        seen = list(state.get("seen", []))
        known = set(seen)
        pages = [p for batch in batches for p in batch]
        fresh = [c for c in candidates(pages, limit=4 * MAX_CANDIDATES) if image_key(c.url) not in known][:MAX_CANDIDATES]
        images = await asyncio.gather(*(self._download(c.url) for c in fresh))
        ready = [(c, img) for c, img in zip(fresh, images) if img]
        new = await self._judge(n, ready) if ready else []  # nothing new: no call to Claude
        poses = (new + [p for p in old if p.id not in {q.id for q in new}])[:MAX_KEPT]
        now = time.time()
        self.store.save_pose_cache(n, {
            "poses": [p.model_dump() for p in poses],
            "seen": (seen + [image_key(c.url) for c in fresh])[-MAX_SEEN:],  # judged or unreachable: not again
            # A search that failed (Apify errors happen) isn't counted as tried, so it runs again next time.
            "queries": {**state.get("queries", {}), **{q: now for q, batch in zip(todo, batches) if batch}},
            "fetched_at": now,
        })
        log.info("poses for %d: %d new images (%d downloaded), %d kept now, %d total", n, len(fresh), len(ready), len(new), len(poses))
        return len(poses)

    async def _download(self, url: str) -> tuple[str, bytes] | None:
        try:
            resp = await self.http.get(url, headers={"User-Agent": vision.USER_AGENT}, timeout=15, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError:
            return None
        media_type = resp.headers.get("content-type", "").split(";")[0].strip()
        if media_type not in vision.MEDIA or not MIN_BYTES <= len(resp.content) <= vision.MAX_INLINE:
            return None
        return media_type, resp.content

    async def _judge(self, n: int, ready: list[tuple[Candidate, tuple[str, bytes]]]) -> list[Pose]:
        """Claude looks at all candidates in one request; the usable ones are copied into the store."""
        content: list[dict] = []
        for i, (_, (media_type, data)) in enumerate(ready):
            content += [{"type": "text", "text": f"Image {i}:"}, vision.image_block(data=base64.b64encode(data).decode(), media_type=media_type)]
        content.append({"type": "text", "text": f"Judge images 0 to {len(ready) - 1} for {n} players."})
        try:
            out = await llm.parse(self.claude, self.s.model, system=JUDGE.format(n=n, parts=n + 1), content=content, output=Verdicts, effort="low")
        except Exception as e:
            log.warning("judging poses for %d failed: %r", n, e)
            return []
        poses = []
        for j in out.images if out else []:
            if not 0 <= j.index < len(ready) or (ok := checked(j, n)) is None:
                continue
            cand, (media_type, data) = ready[j.index]
            photo_id = "pose-" + image_key(cand.url)
            self.store.save_photo("poses", None, media_type, data, photo_id=photo_id)
            poses.append(Pose(
                id=photo_id, name=ok.name.strip(), people=n, image=f"/photos/{photo_id}", cuts=ok.cuts, top=ok.top,
                pose=ok.pose.strip(), source=cand.page,
            ))
        return poses
