// Game page for one player: /?match=<match_id>&agent=<agent_id>
// Shows your key, the live board of who's here, the "We're here" button, then the photo step.
import { useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';

const params = new URLSearchParams(location.search);
const MATCH = params.get('match') ?? '';
const AGENT = params.get('agent') ?? '';

const ROLE_LABEL: Record<string, string> = { riddles: 'Key 1 · riddles', map: 'Key 2 · map', camera: 'Key 3 · camera' };

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

function useMinutesLeft(startedAtMs: number | undefined, timerMin: number | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  if (startedAtMs === undefined || timerMin === undefined) return undefined;
  return Math.max(0, Math.ceil((startedAtMs + timerMin * 60_000 - now) / 60_000));
}

export default function App() {
  const { isActive, identity } = useSpacetimeDB();
  const join = useReducer(reducers.join);
  const checkIn = useReducer(reducers.checkIn);
  const submitPhoto = useReducer(reducers.submitPhoto);

  const [rounds] = useTable(tables.round.where(r => r.matchId.eq(MATCH)));
  const [seats] = useTable(tables.seat.where(r => r.matchId.eq(MATCH)));
  const [photos] = useTable(tables.photo.where(r => r.matchId.eq(MATCH)));
  const [myKeys] = useTable(tables.myKey);

  const [error, setError] = useState('');
  const [photoUrl, setPhotoUrl] = useState('');
  const joined = useRef(false);

  const round = rounds[0];
  const myKey = myKeys.find(k => k.matchId === MATCH);
  const mySeat = seats.find(s => identity && s.player?.isEqual(identity));
  const minutesLeft = useMinutesLeft(round?.startedAt.toDate().getTime(), round?.timerMin);

  // Claim this person's seat once the round exists.
  useEffect(() => {
    if (!isActive || !round || joined.current) return;
    joined.current = true;
    join({ matchId: MATCH, agentId: AGENT }).catch(e => setError(String(e?.message ?? e)));
  }, [isActive, round, join]);

  const run = (p: Promise<unknown>) => {
    setError('');
    p.catch(e => setError(String(e?.message ?? e)));
  };

  if (!MATCH || !AGENT) return <Shell><p>Open the game link from your match page.</p></Shell>;
  if (!isActive) return <Shell><p className="muted">Connecting…</p></Shell>;
  if (!round) return <Shell><p className="muted">Waiting for your round to start…</p></Shell>;

  return (
    <Shell>
      <header>
        <h1>Three-key hunt</h1>
        {round.state !== 'done' && minutesLeft !== undefined && <span className="pill">{minutesLeft} min left</span>}
      </header>

      {myKey ? (
        <section className="card key">
          <p className="label">{ROLE_LABEL[myKey.role] ?? myKey.role}</p>
          <p className="big">{myKey.key}</p>
          <p className="muted">Read it out loud. The others hold the rest.</p>
        </section>
      ) : (
        <section className="card"><p className="muted">Joining as {title(AGENT)}…</p></section>
      )}

      <section className="card">
        <p className="label">Your group</p>
        <ul className="board">
          {seats.map(s => (
            <li key={s.seatId} className={s.checkedIn ? 'here' : ''}>
              <span>{title(s.agentId)}{s.seatId === mySeat?.seatId ? ' (you)' : ''}</span>
              <span>{s.checkedIn ? "✓ here" : s.player ? 'on the way' : 'not joined'}</span>
            </li>
          ))}
        </ul>
      </section>

      {round.state === 'playing' && (
        <button
          className="primary"
          disabled={!mySeat || mySeat.checkedIn}
          onClick={() => run(checkIn({ matchId: MATCH }))}
        >
          {mySeat?.checkedIn ? 'Waiting for the others…' : "We're here"}
        </button>
      )}

      {round.state === 'unlocked' && (
        <form
          className="card"
          onSubmit={e => {
            e.preventDefault();
            run(submitPhoto({ matchId: MATCH, url: photoUrl.trim() }));
          }}
        >
          <p className="label">Everyone's here! Take the group photo</p>
          <input
            type="url"
            placeholder="https://… photo link"
            value={photoUrl}
            onChange={e => setPhotoUrl(e.target.value)}
            required
          />
          <button className="primary" type="submit">Submit photo</button>
        </form>
      )}

      {round.state === 'done' && (
        <section className="card done">
          <p className="big">Challenge complete!</p>
          {round.answer && <p className="muted">The secret spot: {round.answer.replace(/_/g, ' ')}</p>}
          {photos[0] && <img src={photos[0].url} alt="Group photo" />}
        </section>
      )}

      {error && <p className="error">{error}</p>}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="app">{children}</main>;
}
