import base64
import json

import httpx

from agents.mirror import Mirror
from agents.models import Feedback, User
from agents.store import Store


class FakeSpacetime:
    """The agent_store table behind SpacetimeDB's HTTP API: store_put calls and one SQL query."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        if request.url.path.endswith("/call/store_put"):
            row = json.loads(request.content)
            self.rows[row["key"]] = row
            return httpx.Response(200)
        assert request.url.path.endswith("/sql") and b"agent_store" in request.content
        schema = {"elements": [{"name": {"some": "kind"}}, {"name": {"some": "body"}}]}
        return httpx.Response(200, json=[{"schema": schema, "rows": [[r["kind"], r["body"]] for r in self.rows.values()]}])


def test_a_restart_reloads_everything_from_spacetimedb():
    fake = FakeSpacetime()
    mirror = Mirror("http://stdb", "threekey", token="tok", transport=httpx.MockTransport(fake))
    store = Store(":memory:", mirror)
    store.save_user(User(agent_id="minh", identity="spacetimeauth:1", name="Minh", consented=True))
    store.add_memory("minh", "chat", "loves film cameras")
    photo = store.save_photo("m1", "minh", "image/jpeg", b"\xff\xd8\xffjpeg")
    store.save_check("m1", "s1", True, "a blue door", "minh")
    store.save_check("m1", "s1", False, "blurry", "minh")  # a passed step stays passed
    store.save_feedback(Feedback(match_id="m1", agent_id="minh", rating=5, meet_again=True))
    mirror.flush()
    assert set(fake.rows) >= {"users/minh", "photos/" + photo, "photo_checks/m1/s1", "feedback/m1/minh"}

    fresh = Store(":memory:", Mirror("http://stdb", "threekey", token="tok", transport=httpx.MockTransport(fake)))
    assert fresh.users["minh"].consented and fresh.users["minh"].name == "Minh"
    assert fresh.recent_memories("minh") == ["loves film cameras"]
    assert fresh.photo(photo) == ("image/jpeg", b"\xff\xd8\xffjpeg")
    assert fresh.checks("m1") == {"s1": {"passed": True, "reason": "a blue door"}}
    assert [f.rating for f in fresh.feedback("m1")] == [5]


def test_spacetimedb_down_still_starts():
    def down(request):
        return httpx.Response(503)

    store = Store(":memory:", Mirror("http://stdb", "threekey", token="tok", transport=httpx.MockTransport(down)))
    assert store.users == {}
