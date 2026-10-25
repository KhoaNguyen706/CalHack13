"""Frozen interface contract. Every agent (seeded, web chat, MCP) speaks these models."""

import os
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Smallest host-led group. 2 lets two people test a whole round; set MIN_GROUP_SIZE=3 so strangers never meet one on one.
MIN_GROUP_SIZE = int(os.environ.get("MIN_GROUP_SIZE", "2"))
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_FREE_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


class Window(NamedTuple):
    """A free-time window: day plus start/end in minutes since midnight."""

    day: str
    start: int
    end: int

    def __str__(self) -> str:
        return f"{self.day} {self.start // 60:02d}:{self.start % 60:02d}-{self.end // 60:02d}:{self.end % 60:02d}"


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


def _clean(items: list[str]) -> list[str]:
    return sorted({i.strip().lower() for i in items if i.strip()})


class CheckIn(BaseModel):
    """POST /agents/checkin. Unknown fields (names, exact location, ...) are dropped: coarse info only."""

    model_config = ConfigDict(extra="ignore")

    agent_id: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    free: str
    interests: list[str] = []
    skills: list[str] = []
    group_pref: Literal["small", "any", "large"] = "small"
    area: str = Field(min_length=1, max_length=64)
    # Where the coordinator POSTs questions. Omit it and poll GET /agents/{id}/questions instead.
    callback_url: str | None = None
    # The person's own agent app (e.g. "Meta Muse"), shown in the log.
    via: str | None = Field(None, max_length=32)

    @field_validator("free")
    @classmethod
    def _check_free(cls, v: str) -> str:
        return str(parse_free(v))

    @field_validator("interests", "skills")
    @classmethod
    def _check_tags(cls, v: list[str]) -> list[str]:
        return _clean(v)

    @field_validator("area")
    @classmethod
    def _check_area(cls, v: str) -> str:
        return v.strip()

    @property
    def window(self) -> Window:
        return parse_free(self.free)


class Agent(CheckIn):
    """What the coordinator keeps per agent."""

    match_id: str | None = None
    # Seeded personas with no live agent behind them answer "yes" to every plan.
    auto_reply: bool = False


class Ask(BaseModel):
    """POST /agents/{id}/ask"""

    question: str
    plan: dict | None = None


class Question(Ask):
    question_id: str
    agent_id: str


class Reply(BaseModel):
    """POST /agents/{id}/reply. A counter carries a new free window."""

    question_id: str | None = None
    answer: Literal["yes", "no", "counter"]
    free: str | None = None
    note: str | None = None

    @field_validator("free")
    @classmethod
    def _check_free(cls, v: str | None) -> str | None:
        return None if v is None else str(parse_free(v))


class MatchState(str, Enum):
    inviting = "inviting"  # host-led: seats still open, the coordinator is inviting
    pending = "pending"
    confirmed = "confirmed"
    playing = "playing"
    done = "done"
    cancelled = "cancelled"


class Match(BaseModel):
    """GET /matches/{id}"""

    match_id: str
    members: list[str]
    # Fixed riddles / map / camera roles, only from /matching/run. Host-led groups leave it empty:
    # the challenge designer writes each player's part.
    roles: dict[str, str] = {}
    area: str
    window: str
    state: MatchState = MatchState.pending
    accepted: list[str] = []
    reason: str = ""
    picked_by: Literal["claude", "rules"] = "rules"
    challenge: dict | None = None
    # Host-led challenges (POST /challenges). Unset on groups from /matching/run.
    host: str | None = None
    size: int | None = None
    invited: list[str] = []  # everyone ever invited, so nobody is asked twice
    declined: list[str] = []
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class NewChallenge(BaseModel):
    """POST /challenges. Groups of MIN_GROUP_SIZE (default 2) to 5."""

    agent_id: str
    size: int = Field(3, le=5)

    @field_validator("size")
    @classmethod
    def _check_size(cls, v: int) -> int:
        if v < MIN_GROUP_SIZE:
            raise ValueError(f"size must be at least {MIN_GROUP_SIZE}")
        return v


class Accept(BaseModel):
    """POST /matches/{id}/accept"""

    agent_id: str
    accept: bool = True


class Leave(BaseModel):
    """POST /matches/{id}/leave"""

    agent_id: str


class StateChange(BaseModel):
    """POST /matches/{id}/state, used by the game page."""

    state: Literal["playing", "done"]
