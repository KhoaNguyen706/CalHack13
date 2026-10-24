"""Sign in with Google: check the ID token the page got from Google Identity Services.

Krew keeps only the Google account id (to recognise you next time) and your first name. The email and photo
in the token are never stored or passed on.
"""

import time

import httpx
from pydantic import BaseModel

TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
ISSUERS = {"accounts.google.com", "https://accounts.google.com"}


class InvalidToken(ValueError):
    pass


class GoogleUser(BaseModel):
    sub: str
    first_name: str


async def verify(web: httpx.AsyncClient, credential: str, client_id: str) -> GoogleUser:
    if not client_id:
        raise InvalidToken("Google sign-in isn't set up here (GOOGLE_CLIENT_ID)")
    try:
        resp = await web.get(TOKENINFO, params={"id_token": credential}, timeout=10)
    except httpx.HTTPError as e:
        raise InvalidToken(f"couldn't reach Google: {e}") from e
    if resp.status_code != 200:
        raise InvalidToken("Google didn't accept that sign-in; try again")
    claims = resp.json()
    if claims.get("aud") != client_id or claims.get("iss") not in ISSUERS or int(claims.get("exp", 0)) < time.time():
        raise InvalidToken("that sign-in isn't for Krew")
    first = (claims.get("given_name") or (claims.get("name") or "").split(" ")[0]).strip()[:40] or "Friend"
    return GoogleUser(sub=claims["sub"], first_name=first)
