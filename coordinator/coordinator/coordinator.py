"""Coordinator: the one place agents meet. Matching runs, approvals and challenge hand-off live here."""

import asyncio
import logging
import uuid

import anthropic
import httpx

from .broker import Broker
from .events import EventLog
from .matching import Candidate, candidate_groups, overlap, pick
from .models import Accept, Agent, CheckIn, Match, MatchState, Window
from .spacetime import Spacetime, SpacetimeError, option
from .store import Store

log = logging.getLogger(__name__)

TIMER_MIN = 15
# Fallback round when no challenge designer is reachable: the demo's Three-key sponsor hunt.
DEMO_KEYS = {
    "riddles": "This sponsor's tool helps you scrape the web.",
    "map": "Their booth is near the snacks.",
    "camera": "Ask them: what's the coolest thing someone built with your tool?",
}
NEXT_STATE = {MatchState.confirmed: MatchState.playing, MatchState.playing: MatchState.done}


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def name(agent_id: str) -> str:
    return agent_id.replace("_", " ").title()


def pretty(w: Window) -> str:
    """Window(Sat, 840, 960) -> "Sat 2:00–4:00pm"."""

    def clock(minutes: int) -> tuple[str, str]:
        h, m = divmod(minutes, 60)
        return f"{(h - 1) % 12 + 1}:{m:02d}", "am" if h % 24 < 12 else "pm"

    (s, s_half), (e, e_half) = clock(w.start), clock(w.end)
    return f"{w.day} {s}{'' if s_half == e_half else s_half}–{e}{e_half}"


def default_challenge(match: Match) -> dict:
    return {
        "group": match.members,
        "round": {
            "type": "split_riddle",
            "keys": {m: DEMO_KEYS[match.roles[m]] for m in match.members},
            "answer": "apify_booth",
            "unlock": "all_checked_in + photo",
            "timer_min": TIMER_MIN,
        },
    }


class Coordinator:
    def __init__(
        self,
        store: Store,
        events: EventLog,
        broker: Broker,
        http: httpx.AsyncClient,
        claude: anthropic.AsyncAnthropic | None,
        challenge_url: str | None = None,
        spacetime: Spacetime | None = None,
    ):
        self.store = store
        self.events = events
        self.broker = broker
        self.http = http
        self.claude = claude
        self.challenge_url = challenge_url
        self.spacetime = spacetime
        self._match_lock = asyncio.Lock()
        self._seen: dict[str, set[str]] = {}  # per playing match: who checked in, plus "unlocked"
        self._tasks: set[asyncio.Task] = set()

    # --- gateway ---------------------------------------------------------------

    def checkin(self, checkin: CheckIn, auto_reply: bool = False) -> Agent:
        existing = self.store.agents.get(checkin.agent_id)
        agent = Agent(
            **checkin.model_dump(),
            match_id=existing.match_id if existing else None,
            auto_reply=auto_reply,
        )
        self.store.save_agent(agent)
        self.events.emit(
            "coordinator",
            f"{name(agent.agent_id)} checked in · {agent.area} · {pretty(agent.window)}",
            agent_id=agent.agent_id,
        )
        return agent

    def agent(self, agent_id: str) -> Agent:
        if agent_id not in self.store.agents:
            raise NotFound(f"unknown agent {agent_id}")
        return self.store.agents[agent_id]

    def pool(self) -> list[Agent]:
        return [a for a in self.store.agents.values() if a.match_id is None]

    # --- matching --------------------------------------------------------------

    async def run_matching(self) -> list[Match]:
        """Form as many groups as the pool allows. Each group is asked, then proposed as pending."""
        async with self._match_lock:
            pool = self.pool()
            areas = ", ".join(sorted({a.area for a in pool})) or "none"
            self.events.emit("coordinator", f"{len(pool)} agents in the pool · area: {areas}", pool=len(pool))

            made: list[Match] = []
            excluded: set[str] = set()  # said no during this run
            while candidates := candidate_groups([a for a in self.pool() if a.agent_id not in excluded]):
                group, reason, picked_by = await pick(candidates, self.store.agents, self.claude)
                match = await self._negotiate(group, reason, picked_by, excluded)
                if match:
                    made.append(match)

            if not made:
                self.events.emit("match", "no valid group right now")
            return made

    async def _negotiate(self, group: Candidate, reason: str, picked_by: str, excluded: set[str]) -> Match | None:
        members = [self.store.agents[m] for m in group.members]
        self.events.emit("match", f"overlap window: {pretty(group.window)}", members=list(group.members))
        self.events.emit(
            "match",
            "roles → " + " · ".join(f"{name(m)}: {r}" for m, r in group.roles.items()),
            roles=group.roles,
            picked_by=picked_by,
            reason=reason,
        )
        self.events.emit("match", f"picked by {picked_by}: {reason}")

        plan = {
            "area": members[0].area,
            "window": str(group.window),
            "group_size": len(members),
            "roles": group.roles,
            "activity": f"{TIMER_MIN}-minute cooperative hunt at a public spot",
        }
        others = f"{len(members) - 1} other" + ("s" if len(members) > 2 else "")
        question = f"Plan: meet {others} in {plan['area']}, {pretty(group.window)}, for a {plan['activity']}. Does it fit?"
        replies = await self.broker.ask_all(members, question, plan)

        for agent_id, r in replies.items():
            extra = f" ({r.free})" if r.answer == "counter" and r.free else ""
            self.events.emit("broker", f"{name(agent_id)}: {r.answer}{extra}", agent_id=agent_id, answer=r.answer)
            if r.answer == "counter" and r.free:
                agent = self.store.agents[agent_id].model_copy(update={"free": r.free})
                self.store.save_agent(agent)

        declined = {m for m, r in replies.items() if r.answer == "no" or (r.answer == "counter" and not r.free)}
        countered = {m for m, r in replies.items() if r.answer == "counter" and r.free}
        window = overlap([self.store.agents[m] for m in group.members])
        if declined or window is None:
            # A counter that kills the overlap drops that agent from this run; next run uses the new window.
            excluded |= declined or countered
            self.events.emit("broker", "plan doesn't fit everyone · trying another group")
            return None

        match = Match(
            match_id=uuid.uuid4().hex[:8],
            members=list(group.members),
            roles=group.roles,
            area=members[0].area,
            window=str(window),
            reason=reason,
            picked_by=picked_by,
        )
        self.store.save_match(match)
        for m in match.members:
            self.store.save_agent(self.store.agents[m].model_copy(update={"match_id": match.match_id}))
        self.events.emit("match", f"group proposed · waiting for {len(match.members)}/{len(match.members)} approvals", match_id=match.match_id)
        return match

    # --- approval and game state -----------------------------------------------

    def match(self, match_id: str) -> Match:
        if match_id not in self.store.matches:
            raise NotFound(f"unknown match {match_id}")
        return self.store.matches[match_id]

    def accept(self, match_id: str, body: Accept) -> Match:
        match = self.match(match_id)
        if body.agent_id not in match.members:
            raise Conflict(f"{body.agent_id} is not in match {match_id}")
        if match.state != MatchState.pending:
            raise Conflict(f"match is {match.state.value}, not pending")

        if not body.accept:
            match = self._save_state(match, MatchState.cancelled)
            self._release(match)
            self.events.emit(
                "approval",
                f"{name(body.agent_id)} ✗ → group released, {len(match.members) - 1} back in the pool",
                match_id=match_id,
            )
            return match

        accepted = sorted(set(match.accepted) | {body.agent_id}, key=match.members.index)
        match = self.store.save_match(match.model_copy(update={"accepted": accepted}))
        ticks = "  ".join(f"{name(m)} {'✓' if m in accepted else '…'}" for m in match.members)
        if len(accepted) < len(match.members):
            self.events.emit("approval", f"{ticks}  ({len(accepted)}/{len(match.members)})", match_id=match_id, accepted=accepted)
            return match

        match = self._save_state(match, MatchState.confirmed)
        self.events.emit("approval", f"{ticks}  → CONFIRMED", match_id=match_id, accepted=accepted)
        task = asyncio.create_task(self.design_challenge(match))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return match

    def set_state(self, match_id: str, state: MatchState) -> Match:
        match = self.match(match_id)
        if NEXT_STATE.get(match.state) != state:
            raise Conflict(f"can't go from {match.state.value} to {state.value}")
        match = self._save_state(match, state)
        if state == MatchState.done:
            self._release(match)
        self.events.emit("game", f"group {match_id} is {state.value}", match_id=match_id)
        return match

    def _save_state(self, match: Match, state: MatchState) -> Match:
        return self.store.save_match(match.model_copy(update={"state": state}))

    def _release(self, match: Match) -> None:
        for m in match.members:
            agent = self.store.agents.get(m)
            if agent and agent.match_id == match.match_id:
                self.store.save_agent(agent.model_copy(update={"match_id": None}))

    # --- challenge -------------------------------------------------------------

    async def design_challenge(self, match: Match) -> Match:
        """Get the round from the challenge designer, then open it on the live game board."""
        match = await self._fetch_challenge(match)
        return await self.start_game(match.match_id)

    async def _fetch_challenge(self, match: Match) -> Match:
        """Ask the challenge designer (Claude + Apify, agents team) for the round; fall back to the demo round."""
        if self.challenge_url:
            members = [
                {"agent_id": m, "role": match.roles[m], **self.store.agents[m].model_dump(include={"skills", "interests"})}
                for m in match.members
            ]
            try:
                resp = await self.http.post(
                    self.challenge_url, json={"match": match.model_dump(mode="json"), "members": members}, timeout=60
                )
                resp.raise_for_status()
                return self.set_challenge(match.match_id, resp.json())
            except (httpx.HTTPError, ValueError) as e:
                log.warning("challenge designer failed: %s", e)
                self.events.emit("challenge", "designer unavailable · using the demo round", match_id=match.match_id)
        return self.set_challenge(match.match_id, default_challenge(match))

    def set_challenge(self, match_id: str, challenge: dict) -> Match:
        match = self.match(match_id)
        if match.state not in (MatchState.confirmed, MatchState.playing):
            raise Conflict(f"match is {match.state.value}; challenges attach to confirmed groups")
        keys = (challenge.get("round") or {}).get("keys") or {}
        if set(keys) != set(match.members):
            raise ValueError("challenge.round.keys must have exactly one key per member")
        match = self.store.save_match(match.model_copy(update={"challenge": challenge}))
        timer = challenge["round"].get("timer_min", TIMER_MIN)
        self.events.emit(
            "challenge", f"secret spot chosen · {len(keys)} keys sent · timer {timer} min", match_id=match_id
        )
        return match

    # --- live game (SpacetimeDB) -----------------------------------------------

    async def start_game(self, match_id: str) -> Match:
        """Open the round on SpacetimeDB and mark the match playing. Without SpacetimeDB it stays confirmed."""
        match = self.match(match_id)
        if self.spacetime is None or not match.challenge:
            return match
        rnd = match.challenge["round"]
        args = {
            "matchId": match_id,
            "kind": rnd.get("type", "split_riddle"),
            "timerMin": int(rnd.get("timer_min", TIMER_MIN)),
            "answer": str(rnd.get("answer", "")),
            "seats": [{"agent_id": m, "role": match.roles[m], "key": rnd["keys"][m]} for m in match.members],
        }
        try:
            await self.spacetime.call("start_round", args)
        except (httpx.HTTPError, SpacetimeError) as e:
            log.warning("start_round failed: %s", e)
            self.events.emit("game", "live board unavailable · round not started", match_id=match_id)
            return match
        self._seen[match_id] = set()
        self.events.emit("game", f"round live · {len(match.members)} phones can join", match_id=match_id)
        return self.set_state(match_id, MatchState.playing) if match.state == MatchState.confirmed else match

    async def poll_games(self) -> None:
        """Mirror the live board onto the negotiation log; a finished round marks its match done."""
        playing = {m.match_id for m in self.store.matches.values() if m.state == MatchState.playing}
        if self.spacetime is None or not playing:
            return
        seats = await self.spacetime.sql("SELECT * FROM seat")
        rounds = await self.spacetime.sql("SELECT * FROM round")
        for s in seats:
            seen = self._seen.setdefault(s["match_id"], set())
            if s["match_id"] in playing and s["checked_in"] and s["agent_id"] not in seen:
                seen.add(s["agent_id"])
                self.events.emit("game", f"{name(s['agent_id'])}: we're here ✓", match_id=s["match_id"], agent_id=s["agent_id"])
        for r in rounds:
            mid, seen = r["match_id"], self._seen.setdefault(r["match_id"], set())
            if mid not in playing:
                continue
            if r["state"] in ("unlocked", "done") and "unlocked" not in seen:
                seen.add("unlocked")
                self.events.emit("game", "everyone's here → group photo unlocked", match_id=mid)
            if r["state"] == "done":
                self.events.emit("game", f"photo in · challenge complete! answer: {option(r['answer'])}", match_id=mid)
                self.set_state(mid, MatchState.done)

    async def watch_games(self, interval: float = 1.5) -> None:
        while True:
            try:
                await self.poll_games()
            except (httpx.HTTPError, SpacetimeError, KeyError, IndexError) as e:
                log.warning("game poll failed: %s", e)
            await asyncio.sleep(interval)
