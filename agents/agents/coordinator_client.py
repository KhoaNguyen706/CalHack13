"""Client for the coordinator's HTTP API (the frozen agent interface, see coordinator/README.md)."""

import httpx


class CoordinatorError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


class CoordinatorClient:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http  # base_url is the coordinator

    async def checkin(self, body: dict) -> dict:
        return await self._call("POST", "/agents/checkin", body)

    async def start_challenge(self, agent_id: str, size: int) -> dict:
        """The host-led flow: the coordinator invites other agents until the group has `size` people."""
        return await self._call("POST", "/challenges", {"agent_id": agent_id, "size": size})

    async def run_matching(self) -> list[dict]:
        # The coordinator asks every candidate agent and waits for them, so give it room.
        return await self._call("POST", "/matching/run", timeout=120)

    async def agent(self, agent_id: str) -> dict | None:
        try:
            return await self._call("GET", f"/agents/{agent_id}")
        except CoordinatorError as e:
            if e.status == 404:
                return None
            raise

    async def match(self, match_id: str) -> dict:
        return await self._call("GET", f"/matches/{match_id}")

    async def matches(self) -> list[dict]:
        return await self._call("GET", "/matches")

    async def accept(self, match_id: str, agent_id: str, accept: bool = True) -> dict:
        return await self._call("POST", f"/matches/{match_id}/accept", {"agent_id": agent_id, "accept": accept})

    async def _call(self, method: str, path: str, body: dict | None = None, timeout: float = 20) -> dict | list:
        try:
            resp = await self.http.request(method, path, json=body, timeout=timeout)
        except httpx.HTTPError as e:
            raise CoordinatorError(502, f"coordinator unreachable: {e}") from e
        if resp.is_error:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CoordinatorError(resp.status_code, str(detail))
        return resp.json()
