"""Users, memories, photo checks, photos and feedback in SQLite, copied to SpacetimeDB when a mirror is set.

SQLite answers every query. With a mirror (agents/mirror.py) each write is also saved to SpacetimeDB, and a new
Store starts by reloading those rows, so a restart (Heroku wipes the disk) keeps everyone signed in.
"""

import base64
import sqlite3
import time
import uuid
from typing import Protocol

from .models import Feedback, User


class Mirror(Protocol):
    def load(self) -> list[tuple[str, dict]]: ...
    def put(self, kind: str, key: str, row: dict) -> None: ...
    def delete(self, kind: str, key: str) -> None: ...


TABLES = ("users", "memories", "feedback", "photo_checks", "photos")


class Store:
    def __init__(self, path: str = "agents.db", mirror: Mirror | None = None):
        self.mirror = mirror
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS memories (id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, kind TEXT NOT NULL,"
            " text TEXT NOT NULL, ts REAL NOT NULL)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS feedback (match_id TEXT NOT NULL, agent_id TEXT NOT NULL, body TEXT NOT NULL,"
            " PRIMARY KEY (match_id, agent_id))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS photo_checks (match_id TEXT NOT NULL, step TEXT NOT NULL, passed INTEGER NOT NULL,"
            " reason TEXT NOT NULL, agent_id TEXT, ts REAL NOT NULL, PRIMARY KEY (match_id, step))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS photos (id TEXT PRIMARY KEY, match_id TEXT NOT NULL, agent_id TEXT,"
            " media_type TEXT NOT NULL, data BLOB NOT NULL, ts REAL NOT NULL)"
        )
        if mirror:
            for kind, row in mirror.load():
                if kind == "photos":
                    row = {**row, "data": base64.b64decode(row["data"])}
                if kind in TABLES:
                    self._write(kind, row)
            self.db.commit()
        self.users = {r[0]: User.model_validate_json(r[1]) for r in self.db.execute("SELECT id, body FROM users")}

    def _write(self, table: str, row: dict) -> None:
        cols = ", ".join(row)
        self.db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def _save(self, table: str, key: str, row: dict) -> None:
        """Write one row locally, then copy it to SpacetimeDB."""
        self._write(table, row)
        self.db.commit()
        if self.mirror:
            if table == "photos":
                row = {**row, "data": base64.b64encode(row["data"]).decode()}
            self.mirror.put(table, key, row)

    # --- users -------------------------------------------------------------------

    def save_user(self, user: User) -> User:
        self.users[user.agent_id] = user
        self._save("users", user.agent_id, {"id": user.agent_id, "body": user.model_dump_json()})
        return user

    def delete_user(self, agent_id: str) -> None:
        """Forget a person and their memories (the demo reset)."""
        self.users.pop(agent_id, None)
        memories = [r[0] for r in self.db.execute("SELECT id FROM memories WHERE agent_id = ?", (agent_id,))]
        self.db.execute("DELETE FROM users WHERE id = ?", (agent_id,))
        self.db.execute("DELETE FROM memories WHERE agent_id = ?", (agent_id,))
        self.db.commit()
        if self.mirror:
            self.mirror.delete("users", agent_id)
            for memory_id in memories:
                self.mirror.delete("memories", memory_id)

    def user_by_identity(self, identity: str) -> User | None:
        return next((u for u in self.users.values() if u.identity == identity), None)

    # --- memories ----------------------------------------------------------------

    def add_memory(self, agent_id: str, kind: str, text: str, memory_id: str | None = None) -> str:
        """A fixed memory_id makes the write idempotent (seeded memories)."""
        memory_id = memory_id or f"{agent_id}-{uuid.uuid4().hex[:10]}"
        row = {"id": memory_id, "agent_id": agent_id, "kind": kind, "text": text, "ts": time.time()}
        self._save("memories", memory_id, row)
        return memory_id

    def recent_memories(self, agent_id: str, n: int = 5, kind: str | None = None) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM memories WHERE agent_id = ? AND (? IS NULL OR kind = ?) ORDER BY ts DESC LIMIT ?",
            (agent_id, kind, kind, n),
        )
        return [r[0] for r in rows]

    # --- photos (the group photo the game page uploads) --------------------------

    def save_photo(self, match_id: str, agent_id: str | None, media_type: str, data: bytes) -> str:
        photo_id = uuid.uuid4().hex
        row = {"id": photo_id, "match_id": match_id, "agent_id": agent_id, "media_type": media_type, "data": data, "ts": time.time()}
        self._save("photos", photo_id, row)
        return photo_id

    def photo(self, photo_id: str) -> tuple[str, bytes] | None:
        row = self.db.execute("SELECT media_type, data FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return (row[0], row[1]) if row else None

    # --- photo checks ------------------------------------------------------------

    def save_check(self, match_id: str, step: str, passed: bool, reason: str, agent_id: str | None) -> None:
        """Latest photo wins, except that a step that already passed stays passed."""
        old = self.db.execute("SELECT passed FROM photo_checks WHERE match_id = ? AND step = ?", (match_id, step)).fetchone()
        if old and old[0]:
            return
        row = {"match_id": match_id, "step": step, "passed": int(passed), "reason": reason, "agent_id": agent_id, "ts": time.time()}
        self._save("photo_checks", f"{match_id}/{step}", row)

    def checks(self, match_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT step, passed, reason FROM photo_checks WHERE match_id = ?", (match_id,))
        return {r[0]: {"passed": bool(r[1]), "reason": r[2]} for r in rows}

    # --- feedback ----------------------------------------------------------------

    def save_feedback(self, fb: Feedback) -> None:
        row = {"match_id": fb.match_id, "agent_id": fb.agent_id, "body": fb.model_dump_json()}
        self._save("feedback", f"{fb.match_id}/{fb.agent_id}", row)

    def feedback(self, match_id: str | None = None) -> list[Feedback]:
        if match_id is None:
            rows = self.db.execute("SELECT body FROM feedback")
        else:
            rows = self.db.execute("SELECT body FROM feedback WHERE match_id = ?", (match_id,))
        return [Feedback.model_validate_json(r[0]) for r in rows]
