// Live game for the Three-key hunt: shared clue board and "We're here" check-ins.
//
// The coordinator (Python) starts a round with start_round once a group is confirmed.
// Phones join a seat, read their own key through the my_key view, tap check_in, and
// submit the group photo. Round state: playing -> unlocked (all checked in) -> done (photo), or cancelled
// when the coordinator calls it off (someone left and too few are left).
import { schema, t, table, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

const round = table(
  { name: 'round', public: true },
  {
    matchId: t.string().primaryKey(),
    kind: t.string(),
    timerMin: t.u32(),
    startedAt: t.timestamp(),
    state: t.string(),
    // The secret spot, revealed when the round is done.
    answer: t.option(t.string()),
  }
);

// Public: everyone in the group sees who has joined and checked in, but not the keys.
const seat = table(
  { name: 'seat', public: true },
  {
    seatId: t.string().primaryKey(),
    matchId: t.string().index('btree'),
    agentId: t.string(),
    role: t.string(),
    player: t.option(t.identity()),
    checkedIn: t.bool(),
  }
);

// Private: a key only reaches the phone holding that seat, so nobody can solve it alone.
const seatKey = table({ name: 'seat_key' }, { seatId: t.string().primaryKey(), key: t.string() });
// Private too: a nudge for that player's own part, which their phone shows only when they ask for it.
const seatHint = table({ name: 'seat_hint' }, { seatId: t.string().primaryKey(), hint: t.string() });
const roundSecret = table({ name: 'round_secret' }, { matchId: t.string().primaryKey(), answer: t.string() });

const photo = table(
  { name: 'photo', public: true },
  { matchId: t.string().primaryKey(), url: t.string(), uploadedBy: t.identity(), at: t.timestamp() }
);

// Private storage for the agents service (people, chats, memories, photos), so a server restart keeps everyone
// signed in. Only the coordinator identity reads (SQL with its token) or writes it; phones never see it.
const agentStore = table(
  { name: 'agent_store' },
  { key: t.string().primaryKey(), kind: t.string().index('btree'), body: t.string() }
);

// Identities allowed to start rounds. The publisher is added on init.
const coordinator = table({ name: 'coordinator' }, { identity: t.identity().primaryKey() });

const spacetimedb = schema({ round, seat, seatKey, seatHint, roundSecret, photo, coordinator, agentStore });
export default spacetimedb;

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;

const SeatIn = t.object('SeatIn', { agentId: t.string(), role: t.string(), key: t.string(), hint: t.string() }); // hint '' = none
const HintCard = t.object('HintCard', { matchId: t.string(), agentId: t.string(), hint: t.string() });
const KeyCard = t.object('KeyCard', { matchId: t.string(), agentId: t.string(), role: t.string(), key: t.string() });

function seatIdOf(matchId: string, agentId: string) {
  return `${matchId}:${agentId}`;
}

function mySeat(ctx: Ctx, matchId: string) {
  const s = [...ctx.db.seat.matchId.filter(matchId)].find(s => s.player?.isEqual(ctx.sender));
  if (!s) throw new SenderError('join a seat in this round first');
  return s;
}

function playingRound(ctx: Ctx, matchId: string) {
  const r = ctx.db.round.matchId.find(matchId);
  if (!r) throw new SenderError(`no round for match ${matchId}`);
  return r;
}

export const init = spacetimedb.init(ctx => {
  ctx.db.coordinator.insert({ identity: ctx.sender });
});

// Coordinator only. Re-running it for the same match restarts the round from scratch.
export const start_round = spacetimedb.reducer(
  { matchId: t.string(), kind: t.string(), timerMin: t.u32(), answer: t.string(), seats: t.array(SeatIn) },
  (ctx, { matchId, kind, timerMin, answer, seats }) => {
    if (!ctx.db.coordinator.identity.find(ctx.sender)) throw new SenderError('only the coordinator starts rounds');
    if (seats.length < 2) throw new SenderError('a round needs at least 2 seats');

    for (const old of [...ctx.db.seat.matchId.filter(matchId)]) {
      ctx.db.seatKey.seatId.delete(old.seatId);
      ctx.db.seatHint.seatId.delete(old.seatId);
      ctx.db.seat.seatId.delete(old.seatId);
    }
    ctx.db.round.matchId.delete(matchId);
    ctx.db.roundSecret.matchId.delete(matchId);
    ctx.db.photo.matchId.delete(matchId);

    ctx.db.round.insert({ matchId, kind, timerMin, startedAt: ctx.timestamp, state: 'playing', answer: undefined });
    ctx.db.roundSecret.insert({ matchId, answer });
    for (const s of seats) {
      const seatId = seatIdOf(matchId, s.agentId);
      ctx.db.seat.insert({ seatId, matchId, agentId: s.agentId, role: s.role, player: undefined, checkedIn: false });
      ctx.db.seatKey.insert({ seatId, key: s.key });
      if (s.hint) ctx.db.seatHint.insert({ seatId, hint: s.hint });
    }
  }
);

function onlyCoordinator(ctx: Ctx) {
  if (!ctx.db.coordinator.identity.find(ctx.sender)) throw new SenderError('only the coordinator changes rounds');
}

// Coordinator only: a player left the challenge. Their seat goes; if everyone still here has checked in, the
// photo step unlocks for them.
export const drop_seat = spacetimedb.reducer({ matchId: t.string(), agentId: t.string() }, (ctx, { matchId, agentId }) => {
  onlyCoordinator(ctx);
  const r = playingRound(ctx, matchId);
  const seatId = seatIdOf(matchId, agentId);
  ctx.db.seatKey.seatId.delete(seatId);
  ctx.db.seatHint.seatId.delete(seatId);
  ctx.db.seat.seatId.delete(seatId);
  const left = [...ctx.db.seat.matchId.filter(matchId)];
  if (r.state === 'playing' && left.length > 0 && left.every(o => o.checkedIn)) {
    ctx.db.round.matchId.update({ ...r, state: 'unlocked' });
  }
});

// Coordinator only: the challenge was called off. Phones show it and send everyone back to their agent.
export const end_round = spacetimedb.reducer({ matchId: t.string() }, (ctx, { matchId }) => {
  onlyCoordinator(ctx);
  const r = playingRound(ctx, matchId);
  if (r.state !== 'done') ctx.db.round.matchId.update({ ...r, state: 'cancelled' });
});

// Coordinator only: save one row of the agents service's data (insert or replace).
export const store_put = spacetimedb.reducer({ key: t.string(), kind: t.string(), body: t.string() }, (ctx, row) => {
  onlyCoordinator(ctx);
  if (ctx.db.agentStore.key.find(row.key)) ctx.db.agentStore.key.update(row);
  else ctx.db.agentStore.insert(row);
});

export const store_delete = spacetimedb.reducer({ key: t.string() }, (ctx, { key }) => {
  onlyCoordinator(ctx);
  ctx.db.agentStore.key.delete(key);
});

// A phone claims its person's seat. First claim wins; the same phone can rejoin.
export const join = spacetimedb.reducer({ matchId: t.string(), agentId: t.string() }, (ctx, { matchId, agentId }) => {
  playingRound(ctx, matchId);
  const s = ctx.db.seat.seatId.find(seatIdOf(matchId, agentId));
  if (!s) throw new SenderError(`${agentId} is not in this round`);
  if (s.player?.isEqual(ctx.sender)) return;
  if (s.player) throw new SenderError('that seat is already taken');
  const held = [...ctx.db.seat.matchId.filter(matchId)].find(o => o.player?.isEqual(ctx.sender));
  if (held) throw new SenderError(`this phone already holds ${held.agentId}'s seat`);
  ctx.db.seat.seatId.update({ ...s, player: ctx.sender });
});

// "We're here". When every seat has checked in, the round unlocks the photo step.
export const check_in = spacetimedb.reducer({ matchId: t.string() }, (ctx, { matchId }) => {
  const r = playingRound(ctx, matchId);
  if (r.state !== 'playing') return;
  const s = mySeat(ctx, matchId);
  if (!s.checkedIn) ctx.db.seat.seatId.update({ ...s, checkedIn: true });
  if ([...ctx.db.seat.matchId.filter(matchId)].every(o => o.checkedIn || o.seatId === s.seatId)) {
    ctx.db.round.matchId.update({ ...r, state: 'unlocked' });
  }
});

// Group photo URL (uploaded to storage by the web app). Completes the round and reveals the answer.
export const submit_photo = spacetimedb.reducer({ matchId: t.string(), url: t.string() }, (ctx, { matchId, url }) => {
  const r = playingRound(ctx, matchId);
  if (r.state !== 'unlocked') throw new SenderError('everyone has to check in before the photo');
  if (!/^https?:\/\//.test(url)) throw new SenderError('photo must be an http(s) URL');
  mySeat(ctx, matchId);
  ctx.db.photo.insert({ matchId, url, uploadedBy: ctx.sender, at: ctx.timestamp });
  ctx.db.round.matchId.update({ ...r, state: 'done', answer: ctx.db.roundSecret.matchId.find(matchId)?.answer });
});

// Each phone sees only the key for the seat it holds.
export const my_key = spacetimedb.view({ name: 'my_key', public: true }, t.array(KeyCard), ctx => {
  const keys = [];
  for (const s of ctx.db.seat.iter()) {
    if (!s.player?.isEqual(ctx.sender)) continue;
    const k = ctx.db.seatKey.seatId.find(s.seatId);
    if (k) keys.push({ matchId: s.matchId, agentId: s.agentId, role: s.role, key: k.key });
  }
  return keys;
});

// Each phone sees only the hint for the seat it holds (the page keeps it hidden until "Need a hint?").
export const my_hint = spacetimedb.view({ name: 'my_hint', public: true }, t.array(HintCard), ctx => {
  const hints = [];
  for (const s of ctx.db.seat.iter()) {
    if (!s.player?.isEqual(ctx.sender)) continue;
    const h = ctx.db.seatHint.seatId.find(s.seatId);
    if (h) hints.push({ matchId: s.matchId, agentId: s.agentId, hint: h.hint });
  }
  return hints;
});
