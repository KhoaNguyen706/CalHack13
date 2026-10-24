import json

import httpx

from coordinator.mirror import Mirror
from coordinator.models import Agent, Match
from coordinator.store import Store


class FakeSpacetime:
    """agent_store behind SpacetimeDB's HTTP API: store_put / store_delete calls and SQL by kind."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/call/store_put"):
            row = json.loads(request.content)
            self.rows[row["key"]] = row
            return httpx.Response(200)
        if path.endswith("/call/store_delete"):
            self.rows.pop(json.loads(request.content)["key"], None)
            return httpx.Response(200)
        kind = request.content.decode().split("'")[1]
        rows = [[r["body"]] for r in self.rows.values() if r["kind"] == kind]
        return httpx.Response(200, json=[{"schema": {"elements": [{"name": {"some": "body"}}]}, "rows": rows}])


def mirror(fake):
    return Mirror("http://stdb", "threekey", token="tok", transport=httpx.MockTransport(fake))


def test_a_restart_keeps_agents_and_matches():
    fake = FakeSpacetime()
    m = mirror(fake)
    store = Store(":memory:", m)
    store.save_agent(Agent(agent_id="linh", free="Sat 14:00-16:00", area="Here", match_id="abc"))
    store.save_match(Match(match_id="abc", members=["linh"], area="Here", window="Sat 14:00-16:00", host="linh", size=3))
    m.flush()

    fresh = Store(":memory:", mirror(fake))
    assert fresh.agents["linh"].match_id == "abc" and fresh.matches["abc"].host == "linh"

    fresh.reset()  # demo reset clears SpacetimeDB too
    fresh.mirror.flush()
    assert Store(":memory:", mirror(fake)).agents == {}
