// Game page for one player: /?match=<match_id>&agent=<agent_id>
// Shows your key, the live board of who's here, the "We're here" button, then the photo step.
import { useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import { AGENTS, API } from './config.ts';

const params = new URLSearchParams(location.search);
const MATCH = params.get('match') ?? '';
const AGENT = params.get('agent') ?? '';

const ROLE_LABEL: Record<string, string> = { riddles: 'Key 1 · riddles', map: 'Key 2 · map', camera: 'Key 3 · camera' };

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

// Shrink the camera photo (max 1280 px, JPEG) so it uploads fast on mobile data. Returns base64 without the prefix.
async function shrink(file: File): Promise<string> {
  const img = new Image();
  img.src = URL.createObjectURL(file);
  try {
    await img.decode();
    const scale = Math.min(1, 1280 / Math.max(img.naturalWidth, img.naturalHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(img.naturalWidth * scale);
    canvas.height = Math.round(img.naturalHeight * scale);
    canvas.getContext('2d')!.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.82).split(',')[1];
  } finally {
    URL.revokeObjectURL(img.src);
  }
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
  const [uploading, setUploading] = useState(false);
  const camera = useRef<HTMLInputElement>(null);
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

  // Leave at any point: the coordinator drops your seat (or calls the challenge off if too few are left).
  const leave = async () => {
    if (!confirm('Leave this challenge? The others carry on without you.')) return;
    const res = await fetch(`${API}/matches/${MATCH}/leave`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent_id: AGENT }),
    });
    if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `couldn't leave (${res.status})`);
    location.assign(`?agent=${AGENT}`);
  };

  // Camera -> agents service (stores it, gives a link) -> the live board, which completes the round.
  const sendPhoto = async (file: File) => {
    setUploading(true);
    try {
      const res = await fetch(`${AGENTS}/photos`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ match_id: MATCH, agent_id: AGENT, image_base64: await shrink(file) }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `upload failed (${res.status})`);
      const { path } = await res.json();
      await submitPhoto({ matchId: MATCH, url: AGENTS + path });
    } finally {
      setUploading(false);
    }
  };

  if (!MATCH || !AGENT) return <Shell><p>Open the game link from your match page.</p></Shell>;
  if (!isActive) return <Shell><p className="muted">Connecting…</p></Shell>;
  if (!round) return <Shell><p className="muted">Waiting for your round to start…</p></Shell>;

  return (
    <Shell>
      <header>
        <h1>Krew</h1>
        {!['done', 'cancelled'].includes(round.state) && minutesLeft !== undefined && <span className="pill">{minutesLeft} min left</span>}
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
        <section className="card">
          <p className="label">Everyone's here! Take the group photo</p>
          {/* capture opens the camera straight away on phones (front camera: everyone in the shot) */}
          <input
            ref={camera}
            type="file"
            accept="image/*"
            capture="user"
            hidden
            onChange={e => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) run(sendPhoto(file));
            }}
          />
          <button className="primary" disabled={uploading} onClick={() => camera.current?.click()}>
            {uploading ? 'Sending the photo…' : '📸 Take the group photo'}
          </button>
        </section>
      )}

      {round.state === 'cancelled' && (
        <section className="card">
          <p className="big">This challenge was called off</p>
          <p className="muted">Too few people were left to finish it. Your agent can find you a new one.</p>
          <a className="pill" href={`?agent=${AGENT}`}>Back to your agent</a>
        </section>
      )}

      {round.state === 'done' && (
        <section className="card done">
          <p className="big">Challenge complete!</p>
          {round.answer && <p className="muted">The secret spot: {round.answer.replace(/_/g, ' ')}</p>}
          {photos[0] && <img src={photos[0].url} alt="Group photo" />}
        </section>
      )}

      {['playing', 'unlocked'].includes(round.state) && (
        <button className="link" onClick={() => run(leave())}>Leave challenge</button>
      )}
      {round.state === 'done' && <a className="pill" href={`?agent=${AGENT}`}>Back to your agent</a>}

      {error && <p className="error">{error}</p>}
    </Shell>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="app">{children}</main>;
}
