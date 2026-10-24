"""Keeps the agents service's data in SpacetimeDB, so a restart (Heroku wipes the disk on every one) keeps everyone
signed in with their chats, memories and photos.

Every write to the local SQLite store is copied, in order, to the private `agent_store` table through the
`store_put` reducer, on a background thread so the chat never waits for it. At startup the store reloads all rows.
Only the publisher identity (the coordinator's token) may read or write that table.
"""

import json
import logging
import queue
import threading
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)


class Mirror:
    def __init__(
        self, base_url: str, database: str, token: str = "", token_file: str = "", transport: httpx.BaseTransport | None = None
    ):
        self.base = f"{base_url.rstrip('/')}/v1/database/{database}"
        self.token, self.token_file = token, token_file
        self.http = httpx.Client(timeout=15, transport=transport)
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._run, daemon=True, name="spacetime-mirror").start()

    @property
    def headers(self) -> dict:
        if self.token_file:  # under docker compose a restarted spacetime publishes with a new token
            try:
                self.token = Path(self.token_file).read_text(encoding="utf-8").strip() or self.token
            except OSError:
                pass
        return {"Authorization": f"Bearer {self.token}"}

    def load(self) -> list[tuple[str, dict]]:
        """Every saved row as (kind, row). Empty (and a warning) if SpacetimeDB can't be reached."""
        try:
            resp = self.http.post(f"{self.base}/sql", content="SELECT kind, body FROM agent_store", headers=self.headers)
            resp.raise_for_status()
            result = resp.json()[0]
            names = [e["name"]["some"] for e in result["schema"]["elements"]]
            rows = [dict(zip(names, r)) for r in result["rows"]]
            return [(r["kind"], json.loads(r["body"])) for r in rows]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            log.warning("couldn't load saved data from SpacetimeDB: %s", e)
            return []

    def put(self, kind: str, key: str, row: dict) -> None:
        self.q.put(("store_put", {"key": f"{kind}/{key}", "kind": kind, "body": json.dumps(row)}))

    def delete(self, kind: str, key: str) -> None:
        self.q.put(("store_delete", {"key": f"{kind}/{key}"}))

    def flush(self, timeout: float = 10) -> None:
        """Wait until every queued write is sent (tests, shutdown)."""
        end = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            reducer, args = self.q.get()
            for attempt in range(3):
                try:
                    resp = self.http.post(f"{self.base}/call/{reducer}", json=args, headers=self.headers)
                    resp.raise_for_status()
                    break
                except httpx.HTTPError as e:
                    if attempt == 2:
                        log.warning("couldn't save %s to SpacetimeDB: %s", args["key"], e)
                    time.sleep(0.5 * (attempt + 1))
            self.q.task_done()
