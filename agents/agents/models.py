"""Shapes the agents service shares with the coordinator. Mirrors coordinator/coordinator/models.py (the frozen
interface) so the two services deploy separately."""

import re
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field, field_validator

_FREE_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")
CHECKIN_FIELDS = {"agent_id", "free", "interests", "skills", "group_pref", "area"}


class Window(NamedTuple):
    """A free-time window: day plus start/end in minutes since midnight."""

    day: str
    start: int
    end: int

    def __str__(self) -> str:
        return f"{self.day} {self.start // 60:02d}:{self.start % 60:02d}-{self.end // 60:02d}:{self.end % 60:02d}"

    @property
    def minutes(self) -> int:
        return self.end - self.start


def parse_free(text: str) -> Window:
    """Parse "Sat 14:00-16:00" into a Window."""
    m = _FREE_RE.match(text.strip())
    if not m:
        raise ValueError('free must look like "Sat 14:00-16:00"')
    day, h1, m1, h2, m2 = m.groups()
    start, end = int(h1) * 60 + int(m1), int(h2) * 60 + int(m2)
    if not (0 <= start < end <= 24 * 60) or int(m1) > 59 or int(m2) > 59:
        raise ValueError("free window must be a valid start < end time on one day")
    return Window(day, start, end)


def shared(a: Window, b: Window) -> Window | None:
    """The part of two windows that overlaps, if any."""
    if a.day != b.day or min(a.end, b.end) <= max(a.start, b.start):
        return None
    return Window(a.day, max(a.start, b.start), min(a.end, b.end))


class Persona(BaseModel):
    """personas/<agent_id>.json: the coarse profile the coordinator sees, plus how this person's agent decides."""

    agent_id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    free: str
    interests: list[str] = []
    skills: list[str] = []
    group_pref: Literal["small", "any", "large"] = "small"
    area: str = Field(min_length=1, max_length=64)
    # Agent-side only, never sent to the coordinator.
    style: str = ""
    max_minutes: int | None = Field(default=None, ge=60)
    open_to_invites: bool = True

    @field_validator("free")
    @classmethod
    def _check_free(cls, v: str) -> str:
        return str(parse_free(v))

    @property
    def window(self) -> Window:
        return parse_free(self.free)

    def checkin(self, callback_url: str | None = None) -> dict:
        """POST /agents/checkin body: coarse fields only."""
        body = self.model_dump(include=CHECKIN_FIELDS)
        if callback_url:
            body["callback_url"] = callback_url
        return body


class Question(BaseModel):
    """What the coordinator's broker POSTs to an agent's callback_url."""

    question_id: str
    agent_id: str
    question: str
    plan: dict | None = None


class Reply(BaseModel):
    """The answer, returned in the callback's response body. A counter carries a new free window."""

    answer: Literal["yes", "no", "counter"]
    free: str | None = None
    note: str | None = None
