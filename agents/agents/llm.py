"""Claude calls shared by the agents. Every caller has a plain-code fallback, so a failed call never breaks the demo."""

import os
from typing import TypeVar

import anthropic
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

# On a safety decline, the API re-runs the request on Anthropic's recommended fallback model.
FALLBACK = {
    "extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
    "extra_body": {"fallbacks": "default"},
}


def make_claude(enabled: bool = True) -> anthropic.AsyncAnthropic | None:
    if not enabled or not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    return anthropic.AsyncAnthropic(timeout=60.0, max_retries=1)


async def parse(
    client: anthropic.AsyncAnthropic,
    model: str,
    *,
    system: str,
    content: str | list[dict],
    output: type[T],
    effort: str = "low",
    max_tokens: int = 16000,
) -> T | None:
    """One structured-output call. None on a refusal; API errors reach the caller's fallback."""
    response = await client.messages.parse(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=output,
        output_config={"effort": effort},
        **FALLBACK,
    )
    if response.stop_reason == "refusal":
        return None
    return response.parsed_output
