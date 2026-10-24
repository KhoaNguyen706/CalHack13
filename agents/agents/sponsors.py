"""Venue booths (hand-written) and what each sponsor does (scraped by the Apify Actor, cached in data/)."""

import json
from pathlib import Path

from pydantic import BaseModel

DATA_DIR = Path(__file__).parent.parent / "data"


class Booth(BaseModel):
    id: str
    sponsor: str
    # Words that give the sponsor away. No single key may contain them.
    aliases: list[str] = []
    area: str
    location: str
    verified: bool = False
    # Hand-written fallback keys.
    hint: str
    question: str

    @property
    def giveaways(self) -> set[str]:
        return {self.sponsor.lower(), self.id.lower(), *(a.lower() for a in self.aliases)}


class SponsorInfo(BaseModel):
    sponsor: str
    what_it_does: str
    notable_projects: list[str] = []
    source_url: str = ""


def load_booths(directory: Path = DATA_DIR) -> list[Booth]:
    return [Booth.model_validate(b) for b in json.loads((directory / "booths.json").read_text(encoding="utf-8"))]


def load_sponsors(directory: Path = DATA_DIR) -> dict[str, SponsorInfo]:
    items = json.loads((directory / "sponsors.json").read_text(encoding="utf-8"))
    return {s.sponsor: s for s in (SponsorInfo.model_validate(i) for i in items)}
