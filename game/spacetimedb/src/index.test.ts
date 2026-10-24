import { describe, expect, it } from 'vitest';
import { ConnectionId } from 'spacetimedb';
import { createModuleTestHarness, TestAuth } from 'spacetimedb/server/test-utils';

import spacetime, * as game from './index';

function auth(subject: string, connectionId: bigint) {
  return TestAuth.fromJwtPayload(JSON.stringify({ iss: 'threekey-test', sub: subject }), new ConnectionId(connectionId));
}

const coord = auth('coordinator', 1n);
const [linh, marcus, priya, stranger] = ['linh', 'marcus', 'priya', 'stranger'].map((s, i) => auth(s, BigInt(i + 2)));

const ROUND = {
  matchId: 'm1',
  kind: 'split_riddle',
  timerMin: 15,
  answer: 'apify_booth',
  seats: [
    { agentId: 'linh', role: 'riddles', key: "This sponsor's tool helps you scrape the web." },
    { agentId: 'marcus', role: 'map', key: 'Their booth is near the snacks.' },
    { agentId: 'priya', role: 'camera', key: 'Ask them: what is the coolest thing built with your tool?' },
  ],
};

function started() {
  const test = createModuleTestHarness(spacetime, game);
  test.withReducerTx(coord, ctx => {
    game.init(ctx, {});
    game.start_round(ctx, ROUND);
  });
  return test;
}

function joinAll(test: ReturnType<typeof started>) {
  test.withReducerTx(linh, ctx => game.join(ctx, { matchId: 'm1', agentId: 'linh' }));
  test.withReducerTx(marcus, ctx => game.join(ctx, { matchId: 'm1', agentId: 'marcus' }));
  test.withReducerTx(priya, ctx => game.join(ctx, { matchId: 'm1', agentId: 'priya' }));
}

const round = (test: ReturnType<typeof started>) => test.db.round.matchId.find('m1');

describe('three-key round', () => {
  it('only the coordinator can start a round', () => {
    const test = createModuleTestHarness(spacetime, game);
    test.withReducerTx(coord, ctx => game.init(ctx, {}));
    expect(() => test.withReducerTx(stranger, ctx => game.start_round(ctx, ROUND))).toThrow('only the coordinator');
    expect(round(test)).toBeNull();
  });

  it('starts with 3 empty seats and keys kept out of public tables', () => {
    const test = started();
    expect(round(test)).toMatchObject({ state: 'playing', timerMin: 15, answer: undefined });
    const seats = [...test.db.seat.matchId.filter('m1')];
    expect(seats.map(s => s.agentId).sort()).toEqual(['linh', 'marcus', 'priya']);
    expect(seats.every(s => !s.checkedIn && s.player === undefined)).toBe(true);
    expect(JSON.stringify(seats)).not.toContain('scrape the web');
    expect(test.db.seatKey.seatId.find('m1:linh')?.key).toContain('scrape the web');
  });

  it('a seat goes to the first phone, and a phone holds one seat', () => {
    const test = started();
    test.withReducerTx(linh, ctx => game.join(ctx, { matchId: 'm1', agentId: 'linh' }));
    test.withReducerTx(linh, ctx => game.join(ctx, { matchId: 'm1', agentId: 'linh' })); // rejoin is fine
    expect(() => test.withReducerTx(stranger, ctx => game.join(ctx, { matchId: 'm1', agentId: 'linh' }))).toThrow('already taken');
    expect(() => test.withReducerTx(linh, ctx => game.join(ctx, { matchId: 'm1', agentId: 'marcus' }))).toThrow('already holds');
    expect(() => test.withReducerTx(stranger, ctx => game.join(ctx, { matchId: 'm1', agentId: 'zed' }))).toThrow('not in this round');
  });

  it('unlocks when all 3 check in, then the photo completes it and reveals the answer', () => {
    const test = started();
    joinAll(test);
    expect(() => test.withReducerTx(stranger, ctx => game.check_in(ctx, { matchId: 'm1' }))).toThrow('join a seat');

    test.withReducerTx(linh, ctx => game.check_in(ctx, { matchId: 'm1' }));
    test.withReducerTx(marcus, ctx => game.check_in(ctx, { matchId: 'm1' }));
    expect(round(test)?.state).toBe('playing');
    expect(() =>
      test.withReducerTx(linh, ctx => game.submit_photo(ctx, { matchId: 'm1', url: 'https://x.test/p.jpg' }))
    ).toThrow('everyone has to check in');

    test.withReducerTx(priya, ctx => game.check_in(ctx, { matchId: 'm1' }));
    expect(round(test)?.state).toBe('unlocked');

    expect(() =>
      test.withReducerTx(priya, ctx => game.submit_photo(ctx, { matchId: 'm1', url: 'javascript:alert(1)' }))
    ).toThrow('http(s) URL');
    test.withReducerTx(priya, ctx => game.submit_photo(ctx, { matchId: 'm1', url: 'https://x.test/p.jpg' }));
    expect(round(test)).toMatchObject({ state: 'done', answer: 'apify_booth' });
    expect(test.db.photo.matchId.find('m1')?.url).toBe('https://x.test/p.jpg');
  });

  it('a player who leaves loses their seat; the rest can still unlock the photo', () => {
    const test = started();
    joinAll(test);
    test.withReducerTx(linh, ctx => game.check_in(ctx, { matchId: 'm1' }));
    test.withReducerTx(marcus, ctx => game.check_in(ctx, { matchId: 'm1' }));
    expect(() => test.withReducerTx(stranger, ctx => game.drop_seat(ctx, { matchId: 'm1', agentId: 'priya' }))).toThrow('only the coordinator');
    test.withReducerTx(coord, ctx => game.drop_seat(ctx, { matchId: 'm1', agentId: 'priya' }));
    expect([...test.db.seat.matchId.filter('m1')].map(s => s.agentId).sort()).toEqual(['linh', 'marcus']);
    expect(test.db.seatKey.seatId.find('m1:priya')).toBeNull();
    expect(round(test)?.state).toBe('unlocked');
  });

  it('the coordinator can call a round off', () => {
    const test = started();
    expect(() => test.withReducerTx(stranger, ctx => game.end_round(ctx, { matchId: 'm1' }))).toThrow('only the coordinator');
    test.withReducerTx(coord, ctx => game.end_round(ctx, { matchId: 'm1' }));
    expect(round(test)?.state).toBe('cancelled');
  });

  it('restarting a round clears seats, check-ins and the photo', () => {
    const test = started();
    joinAll(test);
    test.withReducerTx(linh, ctx => game.check_in(ctx, { matchId: 'm1' }));
    test.withReducerTx(coord, ctx => game.start_round(ctx, ROUND));
    const seats = [...test.db.seat.matchId.filter('m1')];
    expect(seats).toHaveLength(3);
    expect(seats.every(s => !s.checkedIn && s.player === undefined)).toBe(true);
  });
});
