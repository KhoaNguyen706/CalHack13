"""Minimal SpacetimeDB HTTP client: call reducers and run SQL against the game database (game/spacetimedb).

Wire format notes (SpacetimeDB 2.x, TypeScript module):
- Reducer args are a JSON object keyed by the reducer's (camelCase) parameter names, but fields of
  nested objects use their snake_case column names, e.g. {"matchId": ..., "seats": [{"agent_id": ...}]}.
- SQL returns positional rows plus a schema; options come back as [tag, value] with tag 0 = some.
"""

import httpx


class SpacetimeError(RuntimeError):
    pass


class Spacetime:
    def __init__(self, http: httpx.AsyncClient, base_url: str, database: str, token: str):
        self.http = http
        self.base = f"{base_url.rstrip('/')}/v1/database/{database}"
        self.headers = {"Authorization": f"Bearer {token}"}

    async def call(self, reducer: str, args: dict) -> None:
        resp = await self.http.post(f"{self.base}/call/{reducer}", json=args, headers=self.headers, timeout=10)
        if resp.is_error:
            raise SpacetimeError(f"{reducer}: {resp.status_code} {resp.text}")

    async def sql(self, query: str) -> list[dict]:
        resp = await self.http.post(f"{self.base}/sql", content=query, headers=self.headers, timeout=10)
        if resp.is_error:
            raise SpacetimeError(f"sql: {resp.status_code} {resp.text}")
        result = resp.json()[0]
        names = [e["name"]["some"] for e in result["schema"]["elements"]]
        return [dict(zip(names, row)) for row in result["rows"]]


def option(value):
    """Decode a SpacetimeDB option ([0, v] is some(v), [1, []] is none)."""
    return value[1] if value[0] == 0 else None
