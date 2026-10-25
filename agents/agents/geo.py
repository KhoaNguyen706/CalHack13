"""Where the meeting point is on a map: a street address and coordinates for the phone's map and directions.

The organizers' pin (MEETING_POINT_COORDS, MEETING_POINT_ADDRESS) wins. Otherwise the venue's address is looked up
once on OpenStreetMap (Nominatim: free, no key, at most one request a second, results cached on disk). If the lookup
fails, the address text alone still opens in any map app, so a group always gets directions.
"""

import json
import logging
from pathlib import Path

import httpx

from .config import Settings

log = logging.getLogger(__name__)

NOMINATIM = "https://nominatim.openstreetmap.org/search"
# Nominatim's usage policy asks for an identifying User-Agent.
HEADERS = {"User-Agent": "Krew/1.0 (Cal Hacks 13 demo; meetup directions)"}


def parse_coords(text: str) -> tuple[float, float] | None:
    """ "37.8029, -122.4484" -> (37.8029, -122.4484); anything else -> None."""
    try:
        lat, lng = (float(x) for x in text.split(","))
    except ValueError:
        return None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None


def short_address(hit: dict) -> str:
    """A readable street address from a Nominatim hit, e.g. "3601 Lyon St, San Francisco, CA 94123"."""
    a = hit.get("address") or {}
    street = " ".join(x for x in (a.get("house_number"), a.get("road")) if x)
    city = a.get("city") or a.get("town") or a.get("village") or ""
    region = " ".join(x for x in (a.get("state"), a.get("postcode")) if x)
    parts = [x for x in (hit.get("name"), street, city, region) if x]
    return ", ".join(dict.fromkeys(parts)) or hit.get("display_name", "")


class GeoService:
    def __init__(self, settings: Settings, http: httpx.AsyncClient):
        self.s = settings
        self.http = http
        self.cache = Path(settings.cache_dir) / "geo.json"
        self._found: dict | None = None

    @property
    def query(self) -> str:
        return self.s.meeting_point_address or f"{self.s.location}, {self.s.city}"

    async def where(self) -> dict:
        """{"address", "lat", "lng"} for the meeting point; lat/lng are None when nothing could be looked up."""
        if coords := parse_coords(self.s.meeting_point_coords):
            return {"address": self.query, "lat": coords[0], "lng": coords[1]}
        if self._found is None:
            self._found = self._read() or await self._lookup() or {}
        return {"address": self._found.get("address") or self.query, "lat": self._found.get("lat"), "lng": self._found.get("lng")}

    def _read(self) -> dict | None:
        try:
            cached = json.loads(self.cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cached if cached.get("query") == self.query else None

    async def _lookup(self) -> dict | None:
        try:
            resp = await self.http.get(
                NOMINATIM, params={"q": self.query, "format": "jsonv2", "limit": 1, "addressdetails": 1}, headers=HEADERS, timeout=5
            )
            resp.raise_for_status()
            hits = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("meeting point lookup failed: %r", e)
            return None
        if not hits:
            log.warning("meeting point lookup found nothing for %r", self.query)
            return None
        hit = hits[0]
        found = {"query": self.query, "address": short_address(hit), "lat": float(hit["lat"]), "lng": float(hit["lon"])}
        try:
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps(found), encoding="utf-8")
        except OSError:
            pass  # still cached in memory for this run
        return found
