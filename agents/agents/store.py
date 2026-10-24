"""Users, memories, photo checks and feedback in SQLite, so a restart keeps the demo state."""

import sqlite3
import time
import uuid

from .models import Feedback, User


class Store:
    def __init__(self, path: str = "agents.db"):
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
        self.users = {r[0]: User.model_validate_json(r[1]) for r in self.db.execute("SELECT id, body FROM users")}

    # --- users -------------------------------------------------------------------

    def save_user(self, user: User) -> User:
        self.users[user.agent_id] = user
        self.db.execute("INSERT OR REPLACE INTO users (id, body) VALUES (?, ?)", (user.agent_id, user.model_dump_json()))
        self.db.commit()
        return user

    def user_by_identity(self, identity: str) -> User | None:
        return next((u for u in self.users.values() if u.identity == identity), None)

    # --- memories ----------------------------------------------------------------

    def add_memory(self, agent_id: str, kind: str, text: str, memory_id: str | None = None) -> str:
        """A fixed memory_id makes the write idempotent (seeded memories)."""
        memory_id = memory_id or f"{agent_id}-{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT OR REPLACE INTO memories (id, agent_id, kind, text, ts) VALUES (?, ?, ?, ?, ?)",
            (memory_id, agent_id, kind, text, time.time()),
        )
        self.db.commit()
        return memory_id

    def recent_memories(self, agent_id: str, n: int = 5) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM memories WHERE agent_id = ? ORDER BY ts DESC LIMIT ?", (agent_id, n)
        )
        return [r[0] for r in rows]

    # --- photos (the group photo the game page uploads) --------------------------

    def save_photo(self, match_id: str, agent_id: str | None, media_type: str, data: bytes) -> str:
        photo_id = uuid.uuid4().hex
        self.db.execute(
            "INSERT INTO photos (id, match_id, agent_id, media_type, data, ts) VALUES (?, ?, ?, ?, ?, ?)",
            (photo_id, match_id, agent_id, media_type, data, time.time()),
        )
        self.db.commit()
        return photo_id

    def photo(self, photo_id: str) -> tuple[str, bytes] | None:
        row = self.db.execute("SELECT media_type, data FROM photos WHERE id = ?", (photo_id,)).fetchone()
        return (row[0], row[1]) if row else None

    # --- photo checks ------------------------------------------------------------

    def save_check(self, match_id: str, step: str, passed: bool, reason: str, agent_id: str | None) -> None:
        """Latest photo wins, except that a step that already passed stays passed."""
        self.db.execute(
            "INSERT INTO photo_checks (match_id, step, passed, reason, agent_id, ts) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(match_id, step) DO UPDATE SET passed = excluded.passed, reason = excluded.reason,"
            " agent_id = excluded.agent_id, ts = excluded.ts WHERE photo_checks.passed = 0",
            (match_id, step, int(passed), reason, agent_id, time.time()),
        )
        self.db.commit()

    def checks(self, match_id: str) -> dict[str, dict]:
        rows = self.db.execute("SELECT step, passed, reason FROM photo_checks WHERE match_id = ?", (match_id,))
        return {r[0]: {"passed": bool(r[1]), "reason": r[2]} for r in rows}

    # --- feedback ----------------------------------------------------------------

    def save_feedback(self, fb: Feedback) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO feedback (match_id, agent_id, body) VALUES (?, ?, ?)",
            (fb.match_id, fb.agent_id, fb.model_dump_json()),
        )
        self.db.commit()

    def feedback(self, match_id: str | None = None) -> list[Feedback]:
        if match_id is None:
            rows = self.db.execute("SELECT body FROM feedback")
        else:
            rows = self.db.execute("SELECT body FROM feedback WHERE match_id = ?", (match_id,))
        return [Feedback.model_validate_json(r[0]) for r in rows]
