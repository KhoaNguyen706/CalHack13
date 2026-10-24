"""Coordinator: the one place agents meet. Matching runs, approvals and challenge hand-off live here."""

import asyncio
import re
import logging
import uuid

import anthropic
import httpx

from .broker import Broker
from .events import EventLog
from .matching import Candidate, candidate_groups, overlap, pick, pick_invitees, rank_invitees
from .models import MIN_GROUP_SIZE, Accept, Agent, CheckIn, Match, MatchState, NewChallenge, Reply, Window, parse_free
from .spacetime import Spacetime, SpacetimeError, option
from .store import Store

log = logging.getLogger(__name__)

TIMER_MIN = 15
MIN_GROUP = MIN_GROUP_SIZE  # a host-led challenge can start early with this many accepted
ACTIVITY = "creative outdoor challenge"
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
    """ "phong-2" (a second Phong) -> "Phong": the suffix only keeps ids unique."""
    return re.sub(r"-\d+$", "", agent_id).replace("_", " ").title()


def pretty(w: Window) -> str:
    """Window(Sat, 840, 960) -> "Sat 2:00–4:00pm"."""

    def clock(minutes: int) -> tuple[str, str]:
        h, m = divmod(minutes, 60)
        return f"{(h - 1) % 12 + 1}:{m:02d}", "am" if h % 24 < 12 else "pm"

    (s, s_half), (e, e_half) = clock(w.start), clock(w.end)
    return f"{w.day} {s}{'' if s_half == e_half else s_half}–{e}{e_half}"


def default_challenge(match: Match) -> dict:
    keys = list(DEMO_KEYS.values())
    return {
        "group": match.members,
        "round": {
            "type": "split_riddle",
            # Groups without fixed roles (host-led) get the demo keys in turn.
            "keys": {m: DEMO_KEYS.get(match.roles.get(m), keys[i % len(keys)]) for i, m in enumerate(match.members)},
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
        self._asking: set[str] = set()  # agents with an open question; nobody else may ask them meanwhile
        self._inviters: dict[str, asyncio.Task] = {}  # host-led match_id -> its invite loop
        self._stalled: dict[str, int] = {}  # match_id -> group size when it last ran out of people

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
        if agent.match_id is None:  # someone new: challenges with open seats may want them
            for m in list(self.store.matches.values()):
                if m.state == MatchState.inviting:
                    self._keep_inviting(m.match_id)
        return agent

    def agent(self, agent_id: str) -> Agent:
        if agent_id not in self.store.agents:
            raise NotFound(f"unknown agent {agent_id}")
        return self.store.agents[agent_id]

    def pool(self) -> list[Agent]:
        """Agents free to be asked: not in a group and not answering another question."""
        return [a for a in self.store.agents.values() if a.match_id is None and a.agent_id not in self._asking]

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _ask(self, agents: list[Agent], question: str, plan: dict) -> dict[str, Reply]:
        ids = {a.agent_id for a in agents}
        self._asking |= ids
        try:
            return await self.broker.ask_all(agents, question, plan)
        finally:
            self._asking -= ids

    def _log_reply(self, agent_id: str, r: Reply, **data) -> None:
        extra = f" ({r.free})" if r.answer == "counter" and r.free else ""
        note = f" — {r.note}" if r.note else ""
        self.events.emit("broker", f"{name(agent_id)}: {r.answer}{extra}{note}", agent_id=agent_id, answer=r.answer, **data)

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
        replies = await self._ask(members, question, plan)

        for agent_id, r in replies.items():
            self._log_reply(agent_id, r)
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

    # --- host-led challenges ----------------------------------------------------

    def start_challenge(self, body: NewChallenge) -> Match:
        """The host's group, seated with the host only. Invitations run in the background."""
        host = self.store.agents.get(body.agent_id)
        if host is None:
            raise Conflict(f"{body.agent_id} must check in before starting a challenge")
        if host.match_id is not None or host.agent_id in self._asking:
            raise Conflict(f"{body.agent_id} is already in a group")
        match = Match(
            match_id=uuid.uuid4().hex[:8],
            members=[host.agent_id],
            accepted=[host.agent_id],
            area=host.area,
            window=host.free,
            state=MatchState.inviting,
            host=host.agent_id,
            size=body.size,
            invited=[host.agent_id],
        )
        self.store.save_match(match)
        self.store.save_agent(host.model_copy(update={"match_id": match.match_id}))
        self.events.emit(
            "challenge",
            f"{name(host.agent_id)} wants a {body.size}-person challenge · {host.area} · {pretty(host.window)}",
            match_id=match.match_id,
            host=host.agent_id,
            size=body.size,
        )
        self._keep_inviting(match.match_id)
        return match

    def _keep_inviting(self, match_id: str) -> None:
        task = self._inviters.get(match_id)
        if task is None or task.done():
            self._inviters[match_id] = self._spawn(self._invite(match_id))

    def _inviting(self, match_id: str) -> Match | None:
        match = self.store.matches.get(match_id)
        return match if match and match.state == MatchState.inviting else None

    async def _invite(self, match_id: str) -> None:
        """Invite the best candidates in parallel until every seat is filled or nobody is left to ask."""
        while match := self._inviting(match_id):
            need = match.size - len(match.members)
            if need <= 0:
                self._seats_filled(match)
                return
            group = [self.store.agents[m] for m in match.members]
            ranked = rank_invitees(group, [a for a in self.pool() if a.agent_id not in match.invited], match.size)
            if not ranked:
                if self._stalled.get(match_id) != len(match.members):  # say it once, not on every check-in
                    self._stalled[match_id] = len(match.members)
                    self.events.emit(
                        "match",
                        f"{len(match.members)} of {match.size} joined · nobody else nearby is free then",
                        match_id=match_id,
                        host=match.host,
                    )
                return
            chosen, reason, picked_by = await pick_invitees(ranked, group, need, self.store.agents, self.claude)
            if not (match := self._inviting(match_id)):
                return
            free = {a.agent_id for a in self.pool()}  # anyone grabbed while Claude was choosing stays out
            invitees = [self.store.agents[c] for c in chosen if c in free]
            if not invitees:
                continue
            ids = [a.agent_id for a in invitees]
            match = self.store.save_match(match.model_copy(update={"invited": match.invited + ids}))
            self.events.emit(
                "match", "inviting " + ", ".join(map(name, ids)), match_id=match_id, host=match.host, invited=ids
            )
            self.events.emit("match", f"picked by {picked_by}: {reason}", picked_by=picked_by, reason=reason, match_id=match_id)

            plan = {"host": match.host, "size": match.size, "area": match.area, "window": match.window, "activity": ACTIVITY}
            when = pretty(parse_free(match.window))
            # The host stays anonymous until everyone has accepted; plan.host is for the agents, not for people.
            question = f"Someone nearby wants a {match.size}-person challenge in {match.area}, {when}. Want to join?"
            replies = await self._ask(invitees, question, plan)
            for agent_id, r in replies.items():
                self._log_reply(agent_id, r, match_id=match_id)
                self._join(match_id, agent_id, r)

    def _join(self, match_id: str, agent_id: str, r: Reply) -> None:
        """Seat an invitee who said yes, or countered with a window that still fits the group."""
        match, agent = self.store.matches.get(match_id), self.store.agents.get(agent_id)
        if match is None or agent is None:
            return
        if r.answer == "counter" and r.free:
            agent = self.store.save_agent(agent.model_copy(update={"free": r.free}))
        willing = r.answer == "yes" or (r.answer == "counter" and r.free)
        window = overlap([*(self.store.agents[m] for m in match.members), agent]) if willing else None
        if window is None:
            self.store.save_match(match.model_copy(update={"declined": match.declined + [agent_id]}))
            return
        if match.state != MatchState.inviting or len(match.members) >= match.size or agent.match_id is not None:
            return  # called off, started or filled meanwhile
        members = match.members + [agent_id]
        self.store.save_match(match.model_copy(update={"members": members, "window": str(window)}))
        self.store.save_agent(agent.model_copy(update={"match_id": match_id}))
        self.events.emit(
            "match", f"{name(agent_id)} joined · {len(members)} of {match.size}", match_id=match_id, host=match.host, joined=agent_id
        )

    def _seats_filled(self, match: Match) -> None:
        if set(match.accepted) == set(match.members):
            self._confirm(match)
            return
        match = self._save_state(match, MatchState.pending)
        self.events.emit(
            "match",
            f"all {match.size} seats filled · waiting for {len(match.accepted)}/{match.size} approvals",
            match_id=match.match_id,
            host=match.host,
        )

    def _host_led(self, match_id: str) -> Match:
        match = self.match(match_id)
        if match.host is None:
            raise Conflict(f"match {match_id} isn't a host-led challenge")
        if match.state not in (MatchState.inviting, MatchState.pending):
            raise Conflict(f"match is {match.state.value}")
        return match

    def start_now(self, match_id: str) -> Match:
        """Start with whoever accepted, if that's at least 3. The rest are released."""
        match = self._host_led(match_id)
        if len(match.accepted) < MIN_GROUP:
            raise Conflict(f"{len(match.accepted)} accepted; a challenge needs at least {MIN_GROUP}")
        self._release(match, [m for m in match.members if m not in match.accepted])
        match = self.store.save_match(match.model_copy(update={"members": list(match.accepted), "size": len(match.accepted)}))
        return self._confirm(match)

    def cancel(self, match_id: str) -> Match:
        match = self._save_state(self._host_led(match_id), MatchState.cancelled)
        self._release(match)
        self.events.emit("approval", f"challenge called off · {len(match.members)} back in the pool", match_id=match_id)
        return match

    # --- one person's view of the negotiation ------------------------------------

    def feed(self, agent_id: str) -> dict:
        """What one person's live screen shows: their agent, the coordinator, and the agents considered for their
        current (or latest) challenge. Nobody else. Before everyone has accepted, the others are "Agent A, B…" and only
        the kind of their answer shows (never their times or reasons); after, their names do."""
        mine = [m for m in self.store.matches.values() if agent_id in m.members or agent_id in m.invited]
        match = max(mine, key=lambda m: m.created_at) if mine else None
        others: list[str] = []
        for a in (match.invited + match.members) if match else []:
            if a != agent_id and a not in others:
                others.append(a)
        named = bool(match) and match.state in (MatchState.confirmed, MatchState.playing, MatchState.done)
        # Keys stand in for agent ids in what the phone gets, so it never sees who hasn't accepted yet.
        keys = {agent_id: "you", **{a: (a if named else chr(97 + i)) for i, a in enumerate(others)}}
        labels = {"you": "You", **{keys[a]: (name(a) if named else f"Agent {chr(65 + i)}") for i, a in enumerate(others)}}
        spoken = {agent_id: "You", **{a: labels[keys[a]] for a in others}}
        words = {"yes": "yes", "no": "no", "counter": "asked for another time"}

        def relabel(value):
            if isinstance(value, list):
                return [keys.get(v, "someone") for v in value]
            return keys.get(value, "someone")

        events = []
        for e in self.events.history:
            d = e["data"]
            ours = bool(match) and d.get("match_id") == match.match_id
            if not ours and not (d.get("agent_id") == agent_id and "match_id" not in d):
                continue
            text = e["text"]
            if e["kind"] == "broker" and d.get("agent_id") in others:
                text = f"{spoken[d['agent_id']]}: {words.get(d.get('answer'), d.get('answer'))}"
            else:
                for a, label in sorted(spoken.items(), key=lambda kv: -len(name(kv[0]))):
                    text = text.replace(name(a), label)
                text = text.replace("You wants", "You want")
            data = {k: (relabel(v) if k in ("agent_id", "host", "joined", "left", "invited", "accepted", "members") else v) for k, v in d.items()}
            if events and events[-1]["text"] == text and events[-1]["kind"] == e["kind"]:
                continue
            events.append({**e, "text": text, "data": data})
        return {"match_id": match.match_id if match else None, "state": match.state.value if match else None, "labels": labels, "events": events}

    # --- approval and game state -----------------------------------------------

    def match(self, match_id: str) -> Match:
        if match_id not in self.store.matches:
            raise NotFound(f"unknown match {match_id}")
        return self.store.matches[match_id]

    def accept(self, match_id: str, body: Accept) -> Match:
        match = self.match(match_id)
        if body.agent_id not in match.members:
            raise Conflict(f"{body.agent_id} is not in match {match_id}")
        if match.state not in (MatchState.pending, MatchState.inviting):
            raise Conflict(f"match is {match.state.value}, not pending")

        if not body.accept and match.host and body.agent_id != match.host:
            # An invitee backs out: free their seat and invite someone else, instead of cancelling everyone.
            members = [m for m in match.members if m != body.agent_id]
            match = self.store.save_match(
                match.model_copy(
                    update={
                        "members": members,
                        "accepted": [m for m in match.accepted if m != body.agent_id],
                        "declined": match.declined + [body.agent_id],
                        "window": str(overlap([self.store.agents[m] for m in members]) or match.window),
                        "state": MatchState.inviting,
                    }
                )
            )
            self._release(match, [body.agent_id])
            self.events.emit(
                "approval",
                f"{name(body.agent_id)} ✗ · inviting someone else ({len(members)} of {match.size})",
                match_id=match_id,
                host=match.host,
                left=body.agent_id,
            )
            self._keep_inviting(match_id)
            return match

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
        seats = match.size or len(match.members)
        if len(accepted) < seats:
            self.events.emit("approval", f"{self._ticks(match)}  ({len(accepted)}/{seats})", match_id=match_id, accepted=accepted)
            return match
        return self._confirm(match)

    def _ticks(self, match: Match) -> str:
        return "  ".join(f"{name(m)} {'✓' if m in match.accepted else '…'}" for m in match.members)

    def _confirm(self, match: Match) -> Match:
        """Everyone seated has accepted: lock the group and get its challenge designed."""
        match = self._save_state(match, MatchState.confirmed)
        self.events.emit("approval", f"{self._ticks(match)}  → CONFIRMED", match_id=match.match_id, accepted=match.accepted)
        self._spawn(self.design_challenge(match))
        return match

    async def leave(self, match_id: str, agent_id: str) -> Match:
        """Someone leaves the challenge. Before it starts: like declining (seat reopens; the host leaving calls it
        off). Once it's confirmed or playing: their seat goes and the rest carry on, unless fewer than MIN_GROUP
        are left, which calls it off for everyone."""
        match = self.match(match_id)
        if agent_id not in match.members:
            raise Conflict(f"{agent_id} is not in match {match_id}")
        if match.state in (MatchState.inviting, MatchState.pending):
            return self.accept(match_id, Accept(agent_id=agent_id, accept=False))
        if match.state not in (MatchState.confirmed, MatchState.playing):
            raise Conflict(f"match is {match.state.value}; there's nothing to leave")

        playing = match.state == MatchState.playing
        rest = [m for m in match.members if m != agent_id]
        if len(rest) < MIN_GROUP:
            match = self._save_state(match, MatchState.cancelled)
            self._release(match)
            self.events.emit(
                "approval", f"{name(agent_id)} left · challenge called off, everyone back in the pool", match_id=match_id
            )
            if playing:
                await self._board("end_round", {"matchId": match_id})
            return match

        update: dict = {"members": rest, "accepted": [m for m in match.accepted if m != agent_id], "size": len(rest)}
        if match.challenge:
            rnd = match.challenge.get("round") or {}
            keys = {m: k for m, k in (rnd.get("keys") or {}).items() if m != agent_id}
            update["challenge"] = {**match.challenge, "group": rest, "round": {**rnd, "keys": keys}}
        match = self.store.save_match(match.model_copy(update=update))
        self._release(match, [agent_id])
        self.events.emit(
            "approval", f"{name(agent_id)} left · {len(rest)} carry on", match_id=match_id, host=match.host, left=agent_id
        )
        if playing:
            await self._board("drop_seat", {"matchId": match_id, "agentId": agent_id})
        return match

    async def _board(self, reducer: str, args: dict) -> None:
        """A change to the live board that the coordinator's own state already reflects; a failure only logs."""
        if self.spacetime is None:
            return
        try:
            await self.spacetime.call(reducer, args)
        except (httpx.HTTPError, SpacetimeError) as e:
            log.warning("%s failed: %s", reducer, e)

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

    def _release(self, match: Match, who: list[str] | None = None) -> None:
        for m in match.members if who is None else who:
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
                {"agent_id": m, "role": match.roles.get(m, "player"), **self.store.agents[m].model_dump(include={"skills", "interests"})}
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
        if set(keys) > set(match.members):  # someone left while it was being designed: drop their part
            keys = {m: k for m, k in keys.items() if m in match.members}
            challenge = {**challenge, "group": match.members, "round": {**challenge["round"], "keys": keys}}
        if set(keys) != set(match.members):
            raise ValueError("challenge.round.keys must have exactly one key per member")
        match = self.store.save_match(match.model_copy(update={"challenge": challenge}))
        rnd = challenge["round"]
        what = f"“{rnd['title']}”" if rnd.get("title") else "secret spot chosen"
        self.events.emit(
            "challenge", f"{what} · {len(keys)} parts sent · timer {rnd.get('timer_min', TIMER_MIN)} min", match_id=match_id
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
            "seats": [
                {"agent_id": m, "role": match.roles.get(m, "player"), "key": rnd["keys"][m], "hint": (rnd.get("hints") or {}).get(m) or ""}
                for m in match.members
            ],
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
