"""Agents and matches in memory, written through to SQLite (and to SpacetimeDB when a mirror is set), so a restart
keeps challenges in progress."""

import json
import sqlite3

from .mirror import Mirror
from .models import Agent, Match

KINDS = {"agents": "coord_agents", "matches": "coord_matches"}


class Store:
    def __init__(self, path: str = "coordinator.db", mirror: Mirror | None = None):
        self.mirror = mirror
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        if mirror:
            for table, kind in KINDS.items():
                for row in mirror.load(kind):
                    self.db.execute(f"INSERT OR REPLACE INTO {table} (id, body) VALUES (?, ?)", (row["id"], json.dumps(row["body"])))
            self.db.commit()
        self.agents = {r[0]: Agent.model_validate_json(r[1]) for r in self.db.execute("SELECT id, body FROM agents")}
        self.matches = {r[0]: Match.model_validate_json(r[1]) for r in self.db.execute("SELECT id, body FROM matches")}

    def save_agent(self, agent: Agent) -> Agent:
        self.agents[agent.agent_id] = agent
        self._write("agents", agent.agent_id, agent.model_dump_json())
        return agent

    def save_match(self, match: Match) -> Match:
        self.matches[match.match_id] = match
        self._write("matches", match.match_id, match.model_dump_json())
        return match

    def reset(self) -> None:
        if self.mirror:
            for table, kind in KINDS.items():
                for key in (self.agents if table == "agents" else self.matches):
                    self.mirror.delete(kind, key)
        self.agents.clear()
        self.matches.clear()
        self.db.execute("DELETE FROM agents")
        self.db.execute("DELETE FROM matches")
        self.db.commit()

    def _write(self, table: str, key: str, body: str) -> None:
        self.db.execute(f"INSERT OR REPLACE INTO {table} (id, body) VALUES (?, ?)", (key, body))
        self.db.commit()
        if self.mirror:
            self.mirror.put(KINDS[table], key, {"id": key, "body": json.loads(body)})
