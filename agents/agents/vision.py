"""Photo check: Claude looks at a group's photo and says whether it passes the step's check.

Generous by design (it's a game, not an exam), strict about safety: a photo with an identifiable stranger's
face fails. Photos are downloaded here and sent inline, since the API can't fetch from every host. A photo
that can't be opened fails; without Claude, or if Claude errors, the photo counts, so a demo never gets stuck.
"""

import asyncio
import base64
import logging

import anthropic
import httpx
from pydantic import BaseModel

from . import llm

log = logging.getLogger(__name__)

MAX_INLINE = 3_700_000  # bytes; base64 adds a third, and the API takes up to 5 MB per image
MEDIA = ("image/jpeg", "image/png", "image/webp", "image/gif")
USER_AGENT = "CalHacks13-agents/0.1 (hackathon demo)"  # some hosts (Wikimedia) refuse requests without one

SYSTEM = """You check one photo for a group's outdoor challenge. Pass it if it shows what the check asks for. \
Be generous with angles, lighting, blur and partial views: this is a game, not an exam. Fail it if it clearly \
doesn't show it, or if a stranger's face is clearly identifiable (the players themselves in a group photo are \
fine). Never identify, name or describe people's looks. If a reference photo is given, compare against it. \
`reason`: one short, friendly sentence the group will read on their phones."""


class Verdict(BaseModel):
    passed: bool
    reason: str


def image_block(url: str | None = None, data: str | None = None, media_type: str = "image/jpeg") -> dict:
    if url:
        return {"type": "image", "source": {"type": "url", "url": url}}
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


async def fetch(http: httpx.AsyncClient, url: str) -> dict | None:
    """The image as an inline block; by URL if it's too big to inline; None if it isn't a reachable image."""
    try:
        resp = await http.get(url, headers={"User-Agent": USER_AGENT}, timeout=15, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("couldn't download %s: %s", url, e)
        return None
    media_type = resp.headers.get("content-type", "").split(";")[0].strip()
    if media_type not in MEDIA:
        return None
    if len(resp.content) > MAX_INLINE:
        return image_block(url=url)
    return image_block(data=base64.b64encode(resp.content).decode(), media_type=media_type)


async def check(
    claude: anthropic.AsyncAnthropic | None,
    model: str,
    budget_s: float,
    http: httpx.AsyncClient,
    challenge_title: str,
    step: dict,
    image_url: str | None = None,
    image_base64: str | None = None,
    media_type: str = "image/jpeg",
    reference_url: str | None = None,
) -> Verdict:
    if claude is None:
        return Verdict(passed=True, reason="Looks good! (The photo check is offline, so it counts.)")
    if image_url:
        photo = await fetch(http, image_url)
        if photo is None:
            return Verdict(passed=False, reason="I couldn't open that photo. Try uploading it again.")
    else:
        photo = image_block(data=image_base64, media_type=media_type)
    reference = await fetch(http, reference_url) if reference_url else None  # no reference beats no check
    content: list[dict] = []
    if reference:
        content += [{"type": "text", "text": "Reference photo:"}, reference]
    content += [
        {"type": "text", "text": "The group's photo:"},
        photo,
        {"type": "text", "text": f'Challenge: {challenge_title}\nStep: {step["task"]}\nPasses if: {step["check"]}'},
    ]
    try:
        verdict = await asyncio.wait_for(
            llm.parse(claude, model, system=SYSTEM, content=content, output=Verdict, effort="low"), budget_s
        )
    except Exception as e:  # slow or API error: count it
        log.warning("photo check failed: %r", e)
        return Verdict(passed=True, reason="I couldn't check this one, so it counts.")
    return verdict or Verdict(passed=False, reason="I can't check this photo. Try another one.")
