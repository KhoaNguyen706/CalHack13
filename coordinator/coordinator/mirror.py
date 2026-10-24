"""Keeps the coordinator's agents and matches in SpacetimeDB, so a restart (Heroku wipes the disk) keeps
challenges in progress.

Every write to the local SQLite store is copied, in order, to the private `agent_store` table (the game
database) through the publisher-only `store_put` reducer, on a background thread. At startup the store reloads
its own rows (kinds `coord_agents` and `coord_matches`).
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

    def load(self, kind: str) -> list[dict]:
        """Every saved row of one kind. Empty (and a warning) if SpacetimeDB can't be reached."""
        try:
            sql = f"SELECT body FROM agent_store WHERE kind = '{kind}'"  # kind is one of ours, never user input
            resp = self.http.post(f"{self.base}/sql", content=sql, headers=self.headers)
            resp.raise_for_status()
            return [json.loads(row[0]) for row in resp.json()[0]["rows"]]
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            log.warning("couldn't load %s from SpacetimeDB: %s", kind, e)
            return []

    def put(self, kind: str, key: str, row: dict) -> None:
        self.q.put(("store_put", {"key": f"{kind}/{key}", "kind": kind, "body": json.dumps(row)}))

    def delete(self, kind: str, key: str) -> None:
        self.q.put(("store_delete", {"key": f"{kind}/{key}"}))

    def flush(self, timeout: float = 10) -> None:
        end = time.time() + timeout
        while self.q.unfinished_tasks and time.time() < end:
            time.sleep(0.01)

    def _run(self) -> None:
        while True:
            reducer, args = self.q.get()
            for attempt in range(3):
                try:
                    self.http.post(f"{self.base}/call/{reducer}", json=args, headers=self.headers).raise_for_status()
                    break
                except httpx.HTTPError as e:
                    if attempt == 2:
                        log.warning("couldn't save %s to SpacetimeDB: %s", args["key"], e)
                    time.sleep(0.5 * (attempt + 1))
            self.q.task_done()
