"""The agents service: sample personal agents (real people's chatbots, plus stand-ins), their memory, challenges
(design and photo checks), feedback, and a watcher that turns coordinator state into chat updates."""

import asyncio
import logging
import random
import re

import anthropic

from . import designer, personal, timeutil, vision
from .config import Settings
from .coordinator_client import CoordinatorClient, CoordinatorError
from .memory import Memory
from .models import Answer, ChatLine, Feedback, Login, PersonalContext, PhotoCheck, Profile, Question, User
from .place import PlaceService
from .store import Store

log = logging.getLogger(__name__)

GREETING = (
    "Hi {name}! I'm your agent. Want to do a quick outdoor challenge with people nearby? "
    "Tell me how many people (3 to 5), or tap Start."
)
# Sample contexts' free windows are written around a 14:00 demo start; shift_to_now moves that start to now.
DEMO_START_MIN = 14 * 60


class NotFound(LookupError):
    pass


class Conflict(ValueError):
    pass


def name(agent_id: str) -> str:
    return re.sub(r"[_-]+", " ", agent_id).title()


def pretty(window: str | None) -> str:
    """ "Sat 14:00-16:00" -> "Sat 2:00–4:00pm"."""
    if not timeutil.valid(window):
        return window or "soon"
    w = timeutil.parse(window)

    def clock(m: int) -> tuple[str, str]:
        h, mm = divmod(m, 60)
        return f"{(h - 1) % 12 + 1}:{mm:02d}", "am" if h % 24 < 12 else "pm"

    (s, sh), (e, eh) = clock(w.start), clock(w.end)
    return f"{w.day} {s}{'' if sh == eh else sh}–{e}{eh}"


def slug_id(text: str) -> str:
    """A coordinator-safe agent_id (^[a-z0-9_-]{1,32}$) from a display name."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:24] or "player"


def public_round(rnd: dict) -> dict:
    """The challenge as every player may see it: everything except the other players' parts."""
    keys = ("type", "title", "goal", "connection_hook", "steps", "reference_photo", "timer_min", "unlock")
    return {k: rnd.get(k) for k in keys}


class AgentsService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        memory: Memory,
        coordinator: CoordinatorClient,
        claude: anthropic.AsyncAnthropic | None,
        place: PlaceService,
        contexts: list[PersonalContext],
    ):
        self.s = settings
        self.store = store
        self.memory = memory
        self.coordinator = coordinator
        self.claude = claude
        self.place = place
        self.contexts = {c.id: c for c in contexts}
        self._stand_in_free: dict[str, str] = {}  # the window each stand-in last checked in with
        self._locks: dict[str, asyncio.Lock] = {}
        self._accepting: set[tuple[str, str]] = set()
        self._tasks: set[asyncio.Task] = set()

    # --- people ------------------------------------------------------------------

    def user(self, agent_id: str) -> User:
        if agent_id not in self.store.users:
            raise NotFound(f"unknown user {agent_id}")
        return self.store.users[agent_id]

    def lock(self, agent_id: str) -> asyncio.Lock:
        return self._locks.setdefault(agent_id, asyncio.Lock())

    def display(self, agent_id: str) -> str:
        user = self.store.users.get(agent_id)
        return user.name if user else name(agent_id)

    def login(self, body: Login) -> User:
        """SpacetimeDB identity -> our sample agent, loaded with a personal context. Same identity, same agent."""
        if user := self.store.user_by_identity(body.identity):
            return user
        context = self._pick_context(body.context_id)
        base = slug_id(body.name)
        agent_id, n = base, 2
        while agent_id in self.store.users or agent_id in self.contexts:
            agent_id, n = f"{base}-{n}", n + 1
        display = body.name.strip()
        user = User(
            agent_id=agent_id,
            identity=body.identity,
            name=display,
            context_id=context.id if context else None,
            # The context's free window is sample data; a person is free from whenever they start.
            profile=context.profile.model_copy(update={"free": None}) if context else Profile(),
            transcript=[ChatLine(role="agent", text=GREETING.format(name=display))],
        )
        self.store.save_user(user)
        if context and context.memories:
            items = [(f"{agent_id}-ctx-{i}", agent_id, "context", text) for i, text in enumerate(context.memories)]
            self._spawn(self.memory.remember_many(items))
        self._spawn(self._quiet_check_in(user))  # like a real personal agent: available, so others can invite them
        return user

    def _pick_context(self, context_id: str | None) -> PersonalContext | None:
        claimed = {u.context_id for u in self.store.users.values()}
        if context_id:
            if context_id not in self.contexts:
                raise NotFound(f"unknown personal context {context_id}")
            if context_id in claimed:
                raise Conflict(f"personal context {context_id} is already taken")
            return self.contexts[context_id]
        return next((c for c in self.contexts.values() if c.id not in claimed and c.area is None), None)

    def contexts_view(self) -> list[dict]:
        """For a picker on the sign-in screen."""
        owners = {u.context_id: u.name for u in self.store.users.values() if u.context_id}
        return [{**c.model_dump(exclude={"memories", "free", "area"}), "taken_by": owners.get(c.id)} for c in self.contexts.values()]

    def callback(self, agent_id: str) -> str:
        return f"{self.s.public_url.rstrip('/')}/agents/{agent_id}/question"

    def update_profile(self, user: User, fields: dict) -> Profile:
        data = user.profile.model_dump()
        for key in ("interests", "skills"):
            if isinstance(fields.get(key), list):
                data[key] = sorted({str(x).strip().lower() for x in fields[key] if str(x).strip()})
        if fields.get("free"):
            data["free"] = fields["free"]
        if fields.get("group_pref") in ("small", "any", "large"):
            data["group_pref"] = fields["group_pref"]
        if about := str(fields.get("about") or "").strip():
            data["about"] = f"{data['about']} {about}".strip()[-1000:]
            self._spawn(self.memory.remember(user.agent_id, f"Told their agent: {about}", kind="profile"))
        user.profile = Profile.model_validate(data)  # raises on a badly formatted free window
        self.store.save_user(user)
        return user.profile

    async def status(self, user: User) -> dict:
        """Everything the chat and game pages need. Other players' parts stay hidden."""
        agent = match = None
        try:
            agent = await self.coordinator.agent(user.agent_id)
            if agent is None:  # e.g. the coordinator restarted: check back in
                self._spawn(self._quiet_check_in(user))
            elif agent.get("match_id"):
                match = await self.coordinator.match(agent["match_id"])
        except CoordinatorError as e:
            log.warning("status for %s: %s", user.agent_id, e.detail)
        me = user.agent_id
        rnd = ((match or {}).get("challenge") or {}).get("round") or {}
        return {
            "agent_id": me,
            "name": user.name,
            "context_id": user.context_id,
            "profile": user.profile.model_dump(),
            "transcript": [line.model_dump() for line in user.transcript],
            "checked_in": agent is not None,
            "match": {**match, "challenge": None} if match else None,
            "is_host": bool(match and match.get("host") == me),
            "needs_accept": bool(match and match["state"] in ("inviting", "pending") and me not in match["accepted"]),
            "my_part": (rnd.get("keys") or {}).get(me),
            "challenge": public_round(rnd) if rnd else None,
            "progress": self.progress(match["match_id"], rnd) if rnd else None,
        }

    # --- starting and joining challenges ---------------------------------------------

    def _window(self, free: str | None) -> str:
        """Their saved window if it's today with an hour left; otherwise the next two hours."""
        now = timeutil.now(self.s.tz)
        if free:
            w = timeutil.parse(free)
            if w.day == timeutil.DAYS[now.weekday()] and w.end - max(w.start, now.hour * 60 + now.minute) >= 60:
                return free
        return timeutil.from_now(self.s.tz, at=now)

    async def check_in(self, user: User) -> dict:
        p = user.profile
        return await self.coordinator.checkin(
            {
                "agent_id": user.agent_id,
                "free": self._window(p.free),
                "interests": p.interests,
                "skills": p.skills,
                "group_pref": p.group_pref,
                "area": self.s.area,
                "callback_url": self.callback(user.agent_id),
            }
        )

    async def _quiet_check_in(self, user: User) -> None:
        try:
            await self.check_in(user)
        except CoordinatorError as e:
            log.warning("check-in for %s: %s", user.agent_id, e.detail)

    async def start(self, user: User, size: int = 3) -> str:
        """Start a challenge (Start button or chat): check in, then the coordinator invites people up to `size`."""
        agent = await self.check_in(user)
        self.notify(user.agent_id, None, f"Starting a {size}-person challenge for {pretty(agent['free'])}. Inviting people nearby…")
        self._spawn(self._invite(user.agent_id, size))
        return agent["free"]

    async def _invite(self, agent_id: str, size: int) -> None:
        try:
            await self.coordinator.start_challenge(agent_id, size)
            return
        except CoordinatorError as e:
            if not (e.status == 405 or (e.status == 404 and e.detail == "Not Found")):
                self.notify(agent_id, None, f"I couldn't start it ({e.detail}). Try again in a moment.")
                return
        # The coordinator doesn't have host-led challenges yet: fall back to its groups of 3.
        try:
            await self.coordinator.run_matching()
            agent = await self.coordinator.agent(agent_id)
        except CoordinatorError as e:
            self.notify(agent_id, None, f"I couldn't reach the coordinator ({e.detail}). Try again in a moment.")
            return
        if not (agent and agent.get("match_id")):
            self.notify(agent_id, None, "No group yet: waiting for more people to check in. I'll tell you when one forms.")

    async def respond(self, user: User, accept: bool) -> dict:
        agent = await self.coordinator.agent(user.agent_id)
        if not agent or not agent.get("match_id"):
            raise Conflict("there's no invitation to answer")
        match = await self.coordinator.accept(agent["match_id"], user.agent_id, accept)
        if not accept:  # they already know; skip the "called off" update
            user.notified.append(f"{agent['match_id']}:cancelled")
        text = "You're in. Waiting for the others…" if accept else "You passed on this one. Tap Start to start your own."
        self.notify(user.agent_id, None, text)
        return match

    # --- the coordinator's questions -----------------------------------------------

    async def answer(self, q: Question) -> Answer:
        """POST /agents/{id}/question: the coordinator invites this agent's person to a challenge."""
        user, context = self.store.users.get(q.agent_id), self.contexts.get(q.agent_id)
        if user is not None:
            profile, display = user.profile, user.name
        elif context is not None:
            free = await self._stand_in_window(context)
            profile, display = context.profile.model_copy(update={"free": free}), name(context.id)
        else:
            return Answer(answer="yes", note="no profile, open to it")

        memories = await self.memory.recall(q.agent_id, personal.memory_query(q))
        ans = await personal.decide(self.claude, self.s.model, self.s.reply_budget_s, display, profile, memories, q)
        log.info("%s → %s (%s)", q.agent_id, ans.answer, ans.note)
        if user is not None:
            said = f"{ans.answer} ({ans.free})" if ans.answer == "counter" else ans.answer
            self.notify(
                user.agent_id,
                None,
                f"The coordinator asked if you'd join {self.invite_text(q.plan)}. I said {said}{f': {ans.note}' if ans.note else ''}.",
            )
        return ans

    def invite_text(self, plan: dict | None) -> str:
        """ "Linh's 4-person challenge, Sat 2:00–4:00pm" """
        plan = plan or {}
        host, size = plan.get("host"), plan.get("size") or len(plan.get("roles") or {}) or None
        what = f"a {size}-person challenge" if size else "a group challenge"
        if host:
            what = f"{self.display(host)}'s {size}-person challenge" if size else f"{self.display(host)}'s challenge"
        return f"{what}, {pretty(plan.get('window'))}"

    # --- stand-in agents -------------------------------------------------------------

    async def seed_stand_ins(self, shift_to_now: bool = False) -> dict:
        """Check unclaimed personal contexts in as stand-in agents (callbacks to us), with their memories.
        For rehearsals with fewer real people; contexts someone signed in with are skipped."""
        now = timeutil.now(self.s.tz)
        today = timeutil.DAYS[now.weekday()]
        # Start the samples now, but early enough that a 3-hour window still ends before midnight.
        offset = min(now.hour * 60 + now.minute, timeutil.LAST_MINUTE - 3 * 60) - DEMO_START_MIN
        claimed = {u.context_id for u in self.store.users.values()}
        seeded = []
        for c in self.contexts.values():
            if c.id in claimed:
                continue
            free = c.free
            if shift_to_now and timeutil.parse(free).day == "Sat":
                free = timeutil.shift(free, offset, today)
                if free is None:
                    continue
            await self.coordinator.checkin(
                {
                    "agent_id": c.id,
                    **c.model_dump(include={"interests", "skills", "group_pref"}),
                    "free": free,
                    "area": c.area or self.s.area,
                    "callback_url": self.callback(c.id),
                }
            )
            self._stand_in_free[c.id] = free
            seeded.append(c.id)
        await self.memory.remember_many(
            [(f"{c.id}-seed-{i}", c.id, "seed", text) for c in self.contexts.values() if c.id in seeded for i, text in enumerate(c.memories)]
        )
        return {"seeded": len(seeded), "agents": seeded, "shift_to_now": shift_to_now}

    # --- challenges ----------------------------------------------------------------

    async def design(self, body: dict) -> dict:
        """POST /challenge, called by the coordinator when a group confirms. Never waits on a scrape."""
        players = [
            {"agent_id": m["agent_id"], "name": self.display(m["agent_id"]), "interests": m.get("interests", []), "skills": m.get("skills", [])}
            for m in body.get("members") or []
        ]
        now = timeutil.now(self.s.tz)
        place = await self.place.get(now)
        match_id = (body.get("match") or {}).get("match_id", "")
        challenge, how = await designer.design(
            self.claude, self.s.model, self.s.design_budget_s, players, place, now.strftime("%A, %-I:%M %p"), match_id
        )
        challenge["round"]["designed_by"] = how
        log.info("designed %r for %s via %s", challenge["round"]["title"], challenge["group"], how)
        return challenge

    async def check_photo(self, match_id: str, body: PhotoCheck) -> dict:
        """One photo for one step: Claude checks it, the result is kept, and the group hears about a pass."""
        match = await self.coordinator.match(match_id)
        rnd = (match.get("challenge") or {}).get("round") or {}
        if not rnd.get("steps"):
            raise Conflict("this group has no photo challenge yet")
        steps = rnd["steps"]
        step = next((s for s in steps if s["id"] == body.step), None)
        if step is None:
            raise NotFound(f"no step {body.step} in this challenge")
        verdict = await vision.check(
            self.claude, self.s.model, self.s.vision_budget_s, self.place.http, rnd.get("title", ""), step,
            body.image_url, body.image_base64, body.media_type, rnd.get("reference_photo"),
        )
        self.store.save_check(match_id, body.step, verdict.passed, verdict.reason, body.agent_id)
        progress = self.progress(match_id, rnd)
        if verdict.passed:
            number = steps.index(step) + 1
            for a in match["members"]:
                self.notify(a, f"{match_id}:{body.step}:passed", f"Step {number} of {len(steps)} done: {verdict.reason}")
                if progress["done"]:
                    self.notify(a, f"{match_id}:complete", "Challenge complete! Take a moment, then rate it on the end screen.")
        return {"step": body.step, "passed": verdict.passed, "reason": verdict.reason, **progress}

    def progress(self, match_id: str, rnd: dict) -> dict:
        checks = self.store.checks(match_id)
        steps = [
            {"id": s["id"], "task": s["task"], "passed": checks.get(s["id"], {}).get("passed", False), "reason": checks.get(s["id"], {}).get("reason")}
            for s in rnd.get("steps") or []
        ]
        return {"steps": steps, "done": bool(steps) and all(s["passed"] for s in steps)}

    async def progress_view(self, match_id: str) -> dict:
        match = await self.coordinator.match(match_id)
        return self.progress(match_id, (match.get("challenge") or {}).get("round") or {})

    # --- feedback ------------------------------------------------------------------

    async def feedback(self, fb: Feedback) -> dict:
        """End screen answers. The rating becomes a memory, so the agent decides better next time."""
        self.store.save_feedback(fb)
        match = None
        try:
            match = await self.coordinator.match(fb.match_id)
        except CoordinatorError:
            pass
        # One memory per player per group: resubmitting the end screen replaces it.
        text = self.describe(fb, match)
        await self.memory.remember(fb.agent_id, text, kind="rating", memory_id=f"{fb.agent_id}-rating-{fb.match_id}")
        return self.feedback_summary(fb.match_id, fb.agent_id, match)

    async def feedback_view(self, match_id: str, viewer: str | None = None) -> dict:
        """The summary for the end screen. Needs the group's members, or share_ok could pass with answers missing."""
        try:
            match = await self.coordinator.match(match_id)
        except CoordinatorError:
            match = None
        return self.feedback_summary(match_id, viewer, match)

    def feedback_summary(self, match_id: str, viewer: str | None = None, match: dict | None = None) -> dict:
        """Ratings stay private. A viewer who said "meet again" sees who else did (mutual opt-in)."""
        answers = {a.agent_id: a for a in self.store.feedback(match_id)}
        members = (match or {}).get("members") or list(answers)
        yes = [m for m, a in answers.items() if a.meet_again]
        mine = answers.get(viewer) if viewer else None
        return {
            "match_id": match_id,
            "answered": len(answers),
            "waiting_for": [m for m in members if m not in answers],
            "avg_rating": round(sum(a.rating for a in answers.values()) / len(answers), 1) if answers else None,
            "mutual_meet_again": len(yes) >= 2,
            "my_matches": [self.display(m) for m in yes if m != viewer] if mine and mine.meet_again else [],
            "share_ok": bool(members) and all(m in answers and answers[m].share_ok for m in members),
        }

    def stats(self) -> dict:
        groups: dict[str, list[Feedback]] = {}
        for fb in self.store.feedback():
            groups.setdefault(fb.match_id, []).append(fb)
        again = sum(1 for answers in groups.values() if sum(a.meet_again for a in answers) >= 2)
        return {"groups": len(groups), "meet_again_groups": again, "text": f"{again} of {len(groups)} groups wanted to meet again"}

    def describe(self, fb: Feedback, match: dict | None) -> str:
        """A rating as a memory: 'Played "Coral Hunt" with Linh and Marcus and rated it 5/5. ...'"""
        match = match or {}
        others = [self.display(m) for m in match.get("members", []) if m != fb.agent_id]
        title = ((match.get("challenge") or {}).get("round") or {}).get("title")
        text = f'Played "{title}"' if title else "Played an outdoor challenge"
        if role := match.get("roles", {}).get(fb.agent_id):
            text += f" as the {role} person"
        text += f" with {' and '.join(others)}" if others else ""
        text += f" ({len(match['members'])} people)" if match.get("members") else ""
        text += f" and rated it {fb.rating}/5."
        if fb.comment:
            text += f' They said: "{fb.comment}".'
        text += " Wants to meet them again." if fb.meet_again else " Doesn't need to meet them again."
        return text

    # --- chat updates and the watcher ----------------------------------------------

    def notify(self, agent_id: str, key: str | None, text: str) -> None:
        """Post coordinator news into a real person's chat (and queue it for their agent). key = say it once."""
        user = self.store.users.get(agent_id)
        if user is None or (key and key in user.notified):
            return
        user.transcript.append(ChatLine(role="update", text=text))
        user.inbox.append(text)
        if key:
            user.notified.append(key)
        self.store.save_user(user)

    async def watch(self) -> None:
        while True:
            try:
                await self.tick()
            except CoordinatorError as e:
                log.debug("watcher: %s", e.detail)
            except Exception:
                log.exception("watcher tick failed")
            await asyncio.sleep(self.s.watch_every_s)

    async def tick(self) -> None:
        for m in await self.coordinator.matches():
            mid, state, members, accepted = m["match_id"], m["state"], m["members"], m["accepted"]
            people = [a for a in members if a in self.store.users]
            host = m.get("host")
            if host in self.store.users and state != "cancelled":  # incl. the last one, who confirms it
                self._announce_joins(m)
            if state in ("inviting", "pending"):
                waiting = [a for a in members if a not in accepted]
                for i, a in enumerate(a for a in waiting if self._is_stand_in(a)):
                    if self.s.auto_accept and (mid, a) not in self._accepting:
                        self._accepting.add((mid, a))
                        self._spawn(self._stand_in_accept(mid, a, delay=1.0 + 1.5 * i + random.random()))
                for a in people:
                    if a not in accepted:
                        self.notify(a, f"{mid}:invite", self._invitation(m, a))
            elif state in ("confirmed", "playing") and m.get("challenge"):
                rnd = m["challenge"].get("round") or {}
                for a in people:
                    part = (rnd.get("keys") or {}).get(a, "")
                    if rnd.get("goal"):
                        text = f"Everyone's in! {rnd.get('title', 'Your challenge')}: {rnd['goal']} Your part: “{part}”"
                    else:
                        text = f"Everyone's in! Your key: “{part}” Read it out loud to your group."
                    self.notify(a, f"{mid}:challenge", text)
            elif state == "cancelled":
                for a in people:
                    self.notify(a, f"{mid}:cancelled", "That challenge was called off. Tap Start to start your own.")

    def _announce_joins(self, m: dict) -> None:
        """Tell the host about each person who joined, once. `accepted` is in group order, not join order."""
        host, mid = m["host"], m["match_id"]
        size = m.get("size") or len(m["members"])
        for a in m["accepted"]:
            if a != host and f"{mid}:joined:{a}" not in self.store.users[host].notified:
                count = 2 + sum(1 for k in self.store.users[host].notified if k.startswith(f"{mid}:joined:"))
                self.notify(host, f"{mid}:joined:{a}", f"{self.display(a)} joined ({count} of {size}).")

    def _invitation(self, m: dict, agent_id: str) -> str:
        host = m.get("host")
        if host and host != agent_id:
            size = m.get("size") or len(m["members"])
            return f"{self.display(host)} invited you to a {size}-person challenge, {pretty(m['window'])}. Tap Accept to join."
        others = " and ".join(self.display(o) for o in m["members"] if o != agent_id)
        return f"Found you a group with {others}, {pretty(m['window'])}. Tap Accept to join."

    async def _stand_in_window(self, context: PersonalContext) -> str:
        """The window the stand-in is checked in with. After a restart, ask the coordinator (shift_to_now moved it)."""
        if context.id not in self._stand_in_free:
            try:
                agent = await self.coordinator.agent(context.id)
                self._stand_in_free[context.id] = (agent or {}).get("free") or context.free
            except CoordinatorError:
                return context.free
        return self._stand_in_free[context.id]

    def _is_stand_in(self, agent_id: str) -> bool:
        return agent_id in self.contexts and agent_id not in self.store.users

    async def _stand_in_accept(self, match_id: str, agent_id: str, delay: float) -> None:
        await asyncio.sleep(delay)  # staggered, so the log shows approvals arriving one by one
        try:
            await self.coordinator.accept(match_id, agent_id)
        except CoordinatorError as e:
            log.info("%s couldn't accept %s: %s", agent_id, match_id, e.detail)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
