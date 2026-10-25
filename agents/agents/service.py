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
from .geo import GeoService
from .memory import Memory
from .spacetimeauth import Person
from .models import Answer, ChatLine, Feedback, Login, PersonalContext, PhotoCheck, Profile, Question, User
from .place import PlaceService
from .poses import PoseSearch
from .sponsors import Booth, SponsorInfo
from .store import Store

log = logging.getLogger(__name__)

GREETING = (
    "Hi {name}! I'm your agent. I find you a few people nearby for a quick outdoor challenge. "
    "First, check what I'd share about you on the card below. Nothing leaves until you tap Share."
)
# A demo presenter: their agent already knows them, so it says so.
DEMO_GREETING = (
    "Hi {name}! I'm your agent, and I know you well: {about} "
    "I'll find you a few people nearby for a quick challenge. First, check what I'd share about you below."
)
# Signed in for real (SpacetimeAuth): we know only their first name, so the agent asks the basics itself.
NEW_PERSON_GREETING = (
    "Hi {name}! I'm your agent. I find you a few people nearby for a quick outdoor challenge. "
    "A few quick questions so I can pick good matches: what are you into, and what are you good at?"
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


# What the designer asks memory about each player: how their earlier challenges went.
PAST_CHALLENGES = "how did the last challenge go: too long, too hard, what they enjoyed"


def public_round(rnd: dict) -> dict:
    """The challenge as every player may see it: not the others' hints or the photo check. `shared` holds the hints
    of people who left, so the rest can still solve it. The game page keeps `spot_name`, `spot_about` and the pose
    hidden until everyone has tapped We're here."""
    keys = (
        "type", "title", "goal", "pose", "pieces", "shared", "reference_photo", "timer_min", "unlock", "meet",
        "spot_name", "spot_about", "event",
    )
    steps = [{"id": s["id"], "task": s["task"]} for s in rnd.get("steps") or []]
    return {**{k: rnd.get(k) for k in keys}, "steps": steps}


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
        booths: list[Booth] | None = None,
        sponsors: dict[str, SponsorInfo] | None = None,
        demo_people: list[PersonalContext] | None = None,
        spots: list[designer.Spot] | None = None,
        poses: list[designer.Pose] | None = None,
        pose_search: PoseSearch | None = None,
        geo: GeoService | None = None,
    ):
        self.s = settings
        self.geo = geo
        self.store = store
        self.memory = memory
        self.coordinator = coordinator
        self.claude = claude
        self.place = place
        self.contexts = {c.id: c for c in contexts}
        # The demo presenters: their agents know them well, they're never stand-ins or handed to other sign-ins.
        self.demo_people = {c.id: c for c in demo_people or []}
        # Where a challenge can send a group: the hand-picked spots here, plus sponsor booths whose spot is verified
        # (the others are placeholders).
        here = settings.area.strip().casefold()
        info = sponsors or {}
        self.spots = [s for s in spots or [] if s.area.strip().casefold() == here] + [
            designer.Spot(
                id=b.id, name=f"the {b.sponsor} booth", area=b.area, giveaways=sorted(b.giveaways),
                about=f"{info[b.sponsor].what_it_does if b.sponsor in info else b.hint} The booth is {b.location}.",
                check=f"a {b.sponsor} sponsor booth, banner or sign",
            )
            for b in booths or []
            if b.verified and b.area.strip().casefold() == here
        ]
        if settings.demo_spot:  # a live demo sends every group to one spot, if it's known here
            self.spots = [s for s in self.spots if s.id == settings.demo_spot] or self.spots
        self.poses = poses or []  # hand-picked; pose_search finds fresh ones with Apify
        self.pose_search = pose_search
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
        return self._new_user(body.identity, body.name, self._pick_context(body.context_id))

    def demo_people_view(self) -> list[dict]:
        """For the sign-in page: who the demo presenters are (no profile details)."""
        return [{"id": c.id, "name": c.name} for c in self.demo_people.values()]

    def login_demo(self, person_id: str) -> User:
        """A demo presenter: their agent already knows them. The same person always gets the same agent."""
        context = self.demo_people.get(person_id)
        if context is None:
            raise NotFound(f"unknown demo person {person_id}")
        identity = f"demo:{person_id}"
        return self.store.user_by_identity(identity) or self._new_user(identity, context.name or name(person_id), context)

    def reset_demo_people(self) -> list[str]:
        """A clean start for the presenters: their agents forget the chats and test runs (not their profile)."""
        gone = []
        for person_id in self.demo_people:
            if user := self.store.user_by_identity(f"demo:{person_id}"):
                self.store.delete_user(user.agent_id)
                gone.append(user.agent_id)
        return gone

    def login_person(self, person: Person) -> User:
        """A real person: no sample context. Their agent asks for interests, skills and free time in the chat."""
        identity = f"spacetimeauth:{person.sub}"
        return self.store.user_by_identity(identity) or self._new_user(identity, person.first_name, None)

    def _new_user(self, identity: str, display_name: str, context: PersonalContext | None) -> User:
        base = slug_id(display_name)
        agent_id, n = base, 2
        while agent_id in self.store.users or agent_id in self.contexts:
            agent_id, n = f"{base}-{n}", n + 1
        display = display_name.strip()
        greeting = GREETING if context else NEW_PERSON_GREETING
        if context and context.id in self.demo_people:
            greeting = DEMO_GREETING.replace("{about}", context.bio)
        user = User(
            agent_id=agent_id,
            identity=identity,
            name=display,
            context_id=context.id if context else None,
            # The context's free window is sample data; a person is free from whenever they start.
            profile=context.profile.model_copy(update={"free": None}) if context else Profile(),
            transcript=[ChatLine(role="agent", text=greeting.format(name=display))],
        )
        self.store.save_user(user)
        if context and context.memories:
            items = [(f"{agent_id}-ctx-{i}", agent_id, "context", text) for i, text in enumerate(context.memories)]
            self._spawn(self.memory.remember_many(items))
        return user

    async def consent(self, user: User, share: bool) -> None:
        """Share (check in with the coordinator, so others can invite them) or keep everything private."""
        user.consented = share
        self.store.save_user(user)
        if share:
            await self.check_in(user)
            self.notify(user.agent_id, None, "Thanks! I shared only what's on the card. Tap a group size when you want to play.")
        else:
            self.notify(user.agent_id, None, "No problem, I won't share anything. Tap Share whenever you're ready.")

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
            if agent is None:
                if user.consented:  # e.g. the coordinator restarted: check back in
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
            "consented": user.consented,
            "would_share": self.would_share(user),
            "match": {**match, "challenge": None} if match else None,
            "is_host": bool(match and match.get("host") == me),
            "needs_accept": bool(match and match["state"] in ("inviting", "pending") and me not in match["accepted"]),
            "my_part": (rnd.get("keys") or {}).get(me),
            "last_match": user.last_match,
            "my_hint": (rnd.get("hints") or {}).get(me),
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

    def checkin_body(self, user: User) -> dict:
        """Everything the coordinator gets about this person: coarse fields only."""
        p = user.profile
        return {
            "agent_id": user.agent_id,
            "free": self._window(p.free),
            "interests": p.interests,
            "skills": p.skills,
            "group_pref": p.group_pref,
            "area": self.s.area,
            "callback_url": self.callback(user.agent_id),
        }

    def would_share(self, user: User) -> dict:
        """The consent card: exactly what check_in sends, in plain words."""
        body = self.checkin_body(user)
        return {"free": pretty(body["free"]), "area": body["area"], "interests": body["interests"], "skills": body["skills"]}

    async def check_in(self, user: User) -> dict:
        if not user.consented:
            raise Conflict("tap Share first: I only tell the coordinator about you once you say OK")
        return await self.coordinator.checkin(self.checkin_body(user))

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
            # A real person decides for themselves (Accept / Not now), so their agent never declines for them:
            # it only checks the time fits, and counters with the part that does.
            mine = user.profile.model_copy(update={"open_to_invites": True, "max_minutes": None})
            ans = personal.rule_answer(mine, q)
            said = f"{ans.answer} ({ans.free})" if ans.answer == "counter" else ans.answer
            note = ans.note.replace("their", "your")  # the rules' notes are written for the public log
            text = f"The coordinator asked if you'd join {self.invite_text(q.plan)}. I said {said}: {note}."
            if ans.answer != "no":
                text += " It's your call: tap Accept or Not now."
            self.notify(user.agent_id, None, text)
            return ans
        if context is not None:
            free = await self._stand_in_window(context)
            profile, display = context.profile.model_copy(update={"free": free}), name(context.id)
        else:
            return Answer(answer="yes", note="no profile, open to it")

        memories = await self.memory.recall(q.agent_id, personal.memory_query(q))
        ans = await personal.decide(self.claude, self.s.model, self.s.reply_budget_s, display, profile, memories, q)
        log.info("%s → %s (%s)", q.agent_id, ans.answer, ans.note)
        return ans

    def invite_text(self, plan: dict | None) -> str:
        """ "a 4-person challenge nearby, Sat 2:00–4:00pm". Nobody is named until everyone has accepted."""
        plan = plan or {}
        size = plan.get("size") or len(plan.get("roles") or {}) or None
        what = f"a {size}-person challenge nearby" if size else "a group challenge nearby"
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
        # How each player rated earlier challenges (end-screen ratings live in memory), so this one learns from them.
        for p in players:
            past = await self.memory.recall(p["agent_id"], PAST_CHALLENGES, k=3, kind="rating")
            if past:
                p["past_challenges"] = past
        now = timeutil.now(self.s.tz)
        place = await self.place.get(now)
        match_id = (body.get("match") or {}).get("match_id", "")
        poses = self.pose_search.poses_for(len(players), self.poses) if self.pose_search else self.poses
        challenge, how = await designer.design(
            self.claude, self.s.designer_model, self.s.design_budget_s, players, place, f"{now:%A}, {now.hour % 12 or 12}:{now:%M %p}",
            self.spots, poses, match_id, self.s.meeting_point,
        )
        challenge["round"]["designed_by"] = how
        challenge["round"]["event"] = place.event  # for the Story card at the end
        if self.geo and (meet := challenge["round"].get("meet")):
            meet.update(await self.geo.where())  # address + lat/lng for the map and "Directions"
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
        reference = rnd.get("reference_photo")
        stored = self.store.photo(reference.removeprefix("/photos/")) if reference and reference.startswith("/photos/") else None
        verdict = await vision.check(
            self.claude, self.s.model, self.s.vision_budget_s, self.place.http, rnd.get("title", ""), step,
            body.image_url, body.image_base64, body.media_type, None if stored else reference, stored,
        )
        self.store.save_check(match_id, body.step, verdict.passed, verdict.reason, body.agent_id)
        progress = self.progress(match_id, rnd)
        if verdict.passed:
            number = steps.index(step) + 1
            for a in match["members"]:
                if len(steps) > 1:
                    self.notify(a, f"{match_id}:{body.step}:passed", f"Step {number} of {len(steps)} done: {verdict.reason}")
                if progress["done"]:
                    self.notify(a, f"{match_id}:complete", "Challenge complete! Take a moment, then rate it on the end screen.")
        return {"step": body.step, "passed": verdict.passed, "reason": verdict.reason, **progress}

    async def challenge_view(self, match_id: str) -> dict:
        """What every player may see: the public round (no one's part or hint), the step progress, the group."""
        match = await self.coordinator.match(match_id)
        rnd = (match.get("challenge") or {}).get("round") or {}
        return {
            "match_id": match_id,
            "state": match["state"],
            "members": [{"agent_id": a, "name": self.display(a)} for a in match["members"]],
            "challenge": public_round(rnd) if rnd else None,
            "progress": self.progress(match_id, rnd) if rnd else None,
            # Who's at the meeting point; the challenge starts on every phone once it's all the members.
            "met": [a for a in self.store.meetups(match_id) if a in match["members"]],
        }

    async def met(self, match_id: str, agent_id: str) -> dict:
        """A player tapped We're here at the meeting point. Returns the updated view."""
        match = await self.coordinator.match(match_id)
        if agent_id not in match["members"]:
            raise Conflict(f"{agent_id} isn't in this challenge")
        self.store.save_meetup(match_id, agent_id)
        return await self.challenge_view(match_id)

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
                    others = " and ".join(self.display(o) for o in members if o != a)
                    hint = (rnd.get("keys") or {}).get(a, "")
                    meet = rnd.get("meet") or {}
                    where = (
                        f" Meet at {meet['spot']}. Look for phones showing {meet['emoji']} {meet['sign']}, and hold yours up too."
                        if meet.get("spot") else ""
                    )
                    goal = rnd.get("goal") or "Put your hints together to find the spot."
                    text = f"Everyone's in! You're meeting {others}.{where} {rnd.get('title', 'Your challenge')}: {goal} Your hint: “{hint}”"
                    if tuned := (rnd.get("tuned") or {}).get(a):  # only this player hears what their feedback changed
                        text += f" {tuned}"
                    self.notify(a, f"{mid}:challenge", text)
            elif state == "done":
                for a in people:
                    user = self.store.users[a]
                    if f"{mid}:done" not in user.notified:
                        user.last_match = mid
                        self.notify(a, f"{mid}:done", "You did it! Rate the challenge and say if you'd meet them again on the end screen.")
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
                self.notify(host, f"{mid}:joined:{a}", f"Someone joined ({count} of {size}).")

    def _invitation(self, m: dict, agent_id: str) -> str:
        size = m.get("size") or len(m["members"])
        return f"You're invited to a {size}-person challenge nearby, {pretty(m['window'])}. Tap Accept to join."

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
