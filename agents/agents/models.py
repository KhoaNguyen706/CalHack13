"""Models for the agents service. The coordinator's own contract lives in coordinator/coordinator/models.py."""

import os
import time
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import timeutil

GroupPref = Literal["small", "any", "large"]


class Profile(BaseModel):
    """What a person's agent knows about them. Only the coarse fields are sent to the coordinator."""

    interests: list[str] = []
    skills: list[str] = []
    free: str | None = None  # "Sat 14:00-16:00"
    group_pref: GroupPref = "small"
    # Agent-side only, never sent to the coordinator:
    about: str = ""  # bio and chat notes
    style: str = ""  # one line on how this person decides
    max_minutes: int | None = Field(default=None, ge=60)  # longest block they'll commit to
    open_to_invites: bool = True

    @field_validator("free")
    @classmethod
    def _check_free(cls, v: str | None) -> str | None:
        return None if v is None else str(timeutil.parse(v))


class PersonalContext(BaseModel):
    """Fake personal information that a real personal agent (Muse, Grok bot, ...) would already have.

    Our sample chatbot knows nothing about the person using it, so it borrows one of these for matching.
    An unclaimed one can also run on its own as a stand-in agent (rehearsals with fewer real people).
    """

    id: str
    free: str
    interests: list[str] = []
    skills: list[str] = []
    group_pref: GroupPref = "small"
    area: str | None = None  # None = the event's area
    max_minutes: int | None = Field(default=None, ge=60)
    open_to_invites: bool = True
    style: str = ""
    bio: str
    memories: list[str] = []

    @property
    def profile(self) -> Profile:
        keep = {"interests", "skills", "free", "group_pref", "style", "max_minutes", "open_to_invites"}
        return Profile(**self.model_dump(include=keep), about=self.bio)


class ChatLine(BaseModel):
    role: Literal["user", "agent", "update"]  # update = news from the coordinator
    text: str
    ts: float = Field(default_factory=time.time)


class User(BaseModel):
    """A real person, signed in through SpacetimeDB, and the sample chatbot standing in for their personal agent."""

    agent_id: str
    identity: str
    name: str
    context_id: str | None = None  # the personal context their agent uses
    profile: Profile = Profile()
    transcript: list[ChatLine] = []  # what the chat page shows
    convo: list[dict] = []  # Claude messages, append-only: thinking blocks must come back unchanged
    inbox: list[str] = []  # coordinator news Claude hasn't seen yet; goes in front of the next message
    notified: list[str] = []  # "<match_id>:<event>" already announced


class Question(BaseModel):
    """What the coordinator POSTs to an agent's callback_url."""

    question_id: str
    agent_id: str
    question: str
    plan: dict | None = None


class Answer(BaseModel):
    """Our reply in the callback's response body. `note` is the agent's reason for the log."""

    answer: Literal["yes", "no", "counter"]
    free: str | None = None
    note: str = ""


class Login(BaseModel):
    identity: str = Field(min_length=1, max_length=200)  # SpacetimeDB identity
    name: str = Field(min_length=1, max_length=40)
    context_id: str | None = None  # a sample personal context to use; omitted = the next free one


# Smallest group; keep it equal to the coordinator's MIN_GROUP_SIZE. 3 means strangers never meet one on one.
MIN_GROUP_SIZE = int(os.environ.get("MIN_GROUP_SIZE", "2"))


class Start(BaseModel):
    """Start a challenge: the person who starts it picks how many people play (MIN_GROUP_SIZE to 5)."""

    size: int = Field(3, le=5)

    @field_validator("size")
    @classmethod
    def _check_size(cls, v: int) -> int:
        if v < MIN_GROUP_SIZE:
            raise ValueError(f"size must be at least {MIN_GROUP_SIZE}")
        return v


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class Decision(BaseModel):
    accept: bool = True


class PhotoUpload(BaseModel):
    """POST /photos: the group photo straight from the phone's camera, as base64 (the page shrinks it first)."""

    match_id: str = Field(min_length=1, max_length=64)
    agent_id: str | None = None
    image_base64: str = Field(min_length=1, max_length=6_000_000)


class PhotoCheck(BaseModel):
    """One photo for one step of a challenge. Send a public URL (uploaded to storage) or the image itself."""

    step: str  # "s1", "s2", ...
    agent_id: str | None = None
    image_url: str | None = None
    image_base64: str | None = None
    media_type: Literal["image/jpeg", "image/png", "image/webp", "image/gif"] = "image/jpeg"

    @model_validator(mode="after")
    def _one_image(self) -> "PhotoCheck":
        if bool(self.image_url) == bool(self.image_base64):
            raise ValueError("send exactly one of image_url or image_base64")
        return self


class Feedback(BaseModel):
    """End screen: rate the challenge, meet again?, OK to share the photo?"""

    match_id: str
    agent_id: str
    rating: int = Field(ge=1, le=5)
    meet_again: bool
    share_ok: bool = False
    comment: str = Field("", max_length=500)
