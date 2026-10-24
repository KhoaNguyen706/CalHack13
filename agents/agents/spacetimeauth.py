"""Sign in with SpacetimeAuth (SpacetimeDB's OpenID Connect provider).

The page sends the person to SpacetimeAuth (authorization code + PKCE) and hands us the code it got back. We trade
it for an ID token at SpacetimeAuth's token endpoint over TLS, so the token comes straight from the issuer, and
check that it was issued to our client. Krew keeps only the account id (to recognise you next time) and your first
name. The email and picture in the token are never stored or passed on.
"""

import base64
import json
import time

import httpx
from pydantic import BaseModel

ISSUER = "https://auth.spacetimedb.com/oidc"
TOKEN_URL = f"{ISSUER}/token"


class InvalidSignIn(ValueError):
    pass


class Person(BaseModel):
    sub: str
    first_name: str


def claims_of(id_token: str) -> dict:
    try:
        payload = id_token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as e:
        raise InvalidSignIn("SpacetimeAuth sent an unreadable ID token") from e


def first_name(claims: dict) -> str:
    for key in ("given_name", "name", "nickname", "preferred_username"):
        if word := str(claims.get(key) or "").strip().split(" ")[0]:
            return word[:40]
    return "Friend"


async def sign_in(
    web: httpx.AsyncClient, code: str, code_verifier: str, redirect_uri: str, client_id: str, client_secret: str = ""
) -> Person:
    if not client_id:
        raise InvalidSignIn("SpacetimeAuth sign-in isn't set up here (SPACETIMEAUTH_CLIENT_ID)")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:  # confidential client; a public one relies on PKCE alone
        form["client_secret"] = client_secret
    try:
        resp = await web.post(TOKEN_URL, data=form, timeout=15)
    except httpx.HTTPError as e:
        raise InvalidSignIn(f"couldn't reach SpacetimeAuth: {e}") from e
    if resp.status_code != 200:
        raise InvalidSignIn("SpacetimeAuth didn't accept that sign-in; try again")
    id_token = resp.json().get("id_token")
    if not id_token:
        raise InvalidSignIn("SpacetimeAuth sent no ID token")
    claims = claims_of(id_token)
    aud = claims.get("aud")
    audiences = aud if isinstance(aud, list) else [aud]
    if claims.get("iss") != ISSUER or client_id not in audiences or int(claims.get("exp", 0)) < time.time():
        raise InvalidSignIn("that sign-in isn't for Krew")
    return Person(sub=str(claims["sub"]), first_name=first_name(claims))
