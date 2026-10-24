// Game page for one player: /?match=<match_id>&agent=<agent_id>
// The challenge the AI invented for this group: your part (and hint), who's here, "We're here", then the photo steps
// (each checked by the AI), the group photo, and the end screen (rating, meet again?, OK to share the photo?).
import { useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import { AGENTS, API } from './config.ts';

const params = new URLSearchParams(location.search);
const MATCH = params.get('match') ?? '';
const AGENT = params.get('agent') ?? '';

const ROLE_LABEL: Record<string, string> = { riddles: 'Key 1 · riddles', map: 'Key 2 · map', camera: 'Key 3 · camera' };

type Step = { id: string; task: string; passed: boolean; reason: string | null };
type View = {
  state: string;
  members: { agent_id: string; name: string }[];
  challenge: { title?: string; goal?: string; steps?: { id: string; task: string }[] } | null;
  progress: { steps: Step[]; done: boolean } | null;
};
type Summary = { waiting_for: string[]; mutual_meet_again: boolean; my_matches: string[]; share_ok: boolean };

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

async function post<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
  return res.json();
}

// Shrink a camera photo (max 1280 px, JPEG) so it uploads fast on mobile data. Returns base64 without the prefix.
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

// The challenge as everyone may see it (title, goal, steps and their progress), from the agents service.
function useChallenge() {
  const [view, setView] = useState<View>();
  useEffect(() => {
    let live = true;
    const tick = () =>
      fetch(`${AGENTS}/challenges/${MATCH}`)
        .then(r => (r.ok ? r.json() : null))
        .then(v => live && v && setView(v))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);
  return [view, setView] as const;
}

// A hidden camera input: capture opens the camera straight away on phones.
function Camera({ facing, label, busy, onPhoto }: { facing: 'user' | 'environment'; label: string; busy: string; onPhoto: (f: File) => Promise<unknown> }) {
  const input = useRef<HTMLInputElement>(null);
  const [working, setWorking] = useState(false);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="image/*"
        capture={facing}
        hidden
        onChange={e => {
          const file = e.target.files?.[0];
          e.target.value = '';
          if (!file) return;
          setWorking(true);
          onPhoto(file).finally(() => setWorking(false));
        }}
      />
      <button className="primary" disabled={working} onClick={() => input.current?.click()}>{working ? busy : label}</button>
    </>
  );
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
  const [myHints] = useTable(tables.myHint);
  const [view, setView] = useChallenge();

  const [error, setError] = useState('');
  const [showHint, setShowHint] = useState(false);
  const [notQuite, setNotQuite] = useState<Record<string, string>>({}); // step -> why its last photo didn't pass
  const joined = useRef(false);

  const round = rounds[0];
  const myKey = myKeys.find(k => k.matchId === MATCH);
  const myHint = myHints.find(h => h.matchId === MATCH)?.hint;
  const mySeat = seats.find(s => identity && s.player?.isEqual(identity));
  const minutesLeft = useMinutesLeft(round?.startedAt.toDate().getTime(), round?.timerMin);
  const names = Object.fromEntries((view?.members ?? []).map(m => [m.agent_id, m.name]));
  const nameOf = (id: string) => names[id] ?? title(id);
  const steps = view?.progress?.steps ?? [];

  // Claim this person's seat once the round exists.
  useEffect(() => {
    if (!isActive || !round || joined.current) return;
    joined.current = true;
    join({ matchId: MATCH, agentId: AGENT }).catch(e => setError(String(e?.message ?? e)));
  }, [isActive, round, join]);

  const run = (p: Promise<unknown>) => {
    setError('');
    return p.catch(e => setError(String(e?.message ?? e)));
  };

  // Leave at any point: the coordinator drops your seat (or calls the challenge off if too few are left).
  const leave = async () => {
    if (!confirm('Leave this challenge? The others carry on without you.')) return;
    await post(`${API}/matches/${MATCH}/leave`, { agent_id: AGENT });
    location.assign(`?agent=${AGENT}`);
  };

  // One step's photo: the AI checks it and every phone sees the ✓.
  const checkStep = async (step: string, file: File) => {
    const out = await post<{ passed: boolean; reason: string; steps: Step[]; done: boolean }>(
      `${AGENTS}/challenges/${MATCH}/check`,
      { step, agent_id: AGENT, image_base64: await shrink(file), media_type: 'image/jpeg' },
    );
    setView(v => (v ? { ...v, progress: { steps: out.steps, done: out.done } } : v));
    setNotQuite(n => ({ ...n, [step]: out.passed ? '' : out.reason }));
  };

  // The group photo -> agents service (stores it, gives a link) -> the live board, which completes the round.
  const groupPhoto = async (file: File) => {
    const { path } = await post<{ path: string }>(`${AGENTS}/photos`, { match_id: MATCH, agent_id: AGENT, image_base64: await shrink(file) });
    await submitPhoto({ matchId: MATCH, url: AGENTS + path });
  };

  if (!MATCH || !AGENT) return <Shell><p>Open the game link from your agent.</p></Shell>;
  if (!isActive) return <Shell><p className="muted">Connecting…</p></Shell>;
  if (!round) return <Shell><p className="muted">Waiting for your round to start…</p></Shell>;

  const live = ['playing', 'unlocked'].includes(round.state);
  const passed = steps.filter(s => s.passed).length;

  return (
    <Shell>
      <header>
        <h1>{view?.challenge?.title ?? 'Krew'}</h1>
        {live && minutesLeft !== undefined && <span className="pill">{minutesLeft} min left</span>}
      </header>
      {view?.challenge?.goal && live && <p className="muted">{view.challenge.goal}</p>}

      {live && (myKey ? (
        <section className="card key">
          <p className="label">{ROLE_LABEL[myKey.role] ?? 'Your part'}</p>
          <p className="big">{myKey.key}</p>
          <p className="muted">Tell your group your part. Nobody can finish alone.</p>
          {myHint && (showHint ? (
            <p className="hint">💡 {myHint}</p>
          ) : (
            <button className="link" onClick={() => setShowHint(true)}>💡 Need a hint?</button>
          ))}
        </section>
      ) : (
        <section className="card"><p className="muted">Joining as {nameOf(AGENT)}…</p></section>
      ))}

      {live && (
        <section className="card">
          <p className="label">Your group</p>
          <ul className="board">
            {seats.map(s => (
              <li key={s.seatId} className={s.checkedIn ? 'here' : ''}>
                <span>{nameOf(s.agentId)}{s.seatId === mySeat?.seatId ? ' (you)' : ''}</span>
                <span>{s.checkedIn ? '✓ here' : s.player ? 'on the way' : 'not joined'}</span>
              </li>
            ))}
          </ul>
          {round.state === 'playing' && (
            <button className="primary" disabled={!mySeat || mySeat.checkedIn} onClick={() => run(checkIn({ matchId: MATCH }))}>
              {mySeat?.checkedIn ? 'Waiting for the others…' : "We're here"}
            </button>
          )}
        </section>
      )}

      {round.state === 'unlocked' && steps.length > 0 && (
        <section className="card">
          <p className="label">Steps · {passed} of {steps.length} done</p>
          <ol className="challenge-steps">
            {steps.map(s => (
              <li key={s.id} className={s.passed ? 'passed' : ''}>
                <p>{s.passed ? '✓ ' : ''}{s.task}</p>
                {s.passed ? (
                  s.reason && <p className="muted small">{s.reason}</p>
                ) : (
                  <>
                    {notQuite[s.id] && <p className="small not-quite">Not quite: {notQuite[s.id]}</p>}
                    <Camera facing="environment" label={notQuite[s.id] ? '📸 Try another photo' : '📸 Photo for this step'} busy="Checking…" onPhoto={f => run(checkStep(s.id, f))} />
                  </>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}

      {round.state === 'unlocked' && (
        <section className="card">
          <p className="label">
            {steps.length === 0 || passed === steps.length ? "Everyone's here! Finish with the group photo" : 'When you\'re done: the group photo'}
          </p>
          <Camera facing="user" label="📸 Take the group photo" busy="Sending the photo…" onPhoto={f => run(groupPhoto(f))} />
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
          <p className="big">Challenge complete! 🎉</p>
          {view && <p className="muted">With {view.members.filter(m => m.agent_id !== AGENT).map(m => m.name).join(' and ')}</p>}
          {photos[0] && <img src={photos[0].url} alt="Your group photo" />}
        </section>
      )}
      {round.state === 'done' && <EndScreen photoUrl={photos[0]?.url} />}

      {live && <button className="link" onClick={() => run(leave())}>Leave challenge</button>}
      {round.state === 'done' && <a className="pill" href={`?agent=${AGENT}`}>Back to your agent</a>}

      {error && <p className="error">{error}</p>}
    </Shell>
  );
}

// Rating, meet again? and OK to share the photo? Ratings stay private; a "meet again" match shows only when both
// said yes; the photo can be shared only once everyone said OK.
function EndScreen({ photoUrl }: { photoUrl?: string }) {
  const [rating, setRating] = useState(0);
  const [again, setAgain] = useState<boolean | null>(null);
  const [share, setShare] = useState<boolean | null>(null);
  const [summary, setSummary] = useState<Summary>();
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    const tick = () =>
      fetch(`${AGENTS}/feedback/${MATCH}?agent_id=${encodeURIComponent(AGENT)}`)
        .then(r => (r.ok ? r.json() : null))
        .then(s => live && s && setSummary(s))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 4000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  const answered = summary !== undefined && !summary.waiting_for.includes(AGENT);
  const send = () =>
    post<Summary>(`${AGENTS}/feedback`, { match_id: MATCH, agent_id: AGENT, rating, meet_again: again, share_ok: share })
      .then(setSummary)
      .catch(e => setError(String(e.message)));

  const sharePhoto = async () => {
    if (!photoUrl) return;
    try {
      if (navigator.share) await navigator.share({ title: 'Krew', text: 'We did a Krew challenge!', url: photoUrl });
      else window.open(photoUrl, '_blank');
    } catch {
      // closing the share sheet isn't an error
    }
  };

  if (summary && answered) {
    return (
      <section className="card">
        <p className="label">Thanks!</p>
        {summary.my_matches.length > 0 ? (
          <p>
            🤝 {summary.my_matches.length === 1
              ? `You and ${summary.my_matches[0]} both want to meet again.`
              : `You, ${summary.my_matches.slice(0, -1).join(', ')} and ${summary.my_matches[summary.my_matches.length - 1]} all want to meet again.`}
          </p>
        ) : (
          <p className="muted">Your answers stay private. If someone you'd meet again says yes too, you'll see it here.</p>
        )}
        {summary.waiting_for.length > 0 && <p className="muted small">Waiting for {summary.waiting_for.length} more to answer.</p>}
        {summary.share_ok ? (
          <button className="primary" onClick={sharePhoto}>Share the group photo</button>
        ) : (
          <p className="muted small">The photo can be shared once everyone says OK.</p>
        )}
      </section>
    );
  }

  return (
    <section className="card">
      <p className="label">How was it?</p>
      <div className="row stars" role="radiogroup" aria-label="Rating">
        {[1, 2, 3, 4, 5].map(n => (
          <button key={n} className={n <= rating ? 'star on' : 'star'} aria-label={`${n} of 5`} onClick={() => setRating(n)}>★</button>
        ))}
      </div>
      <p>Meet them again?</p>
      <div className="row">
        <button className={again === true ? 'primary' : 'secondary'} onClick={() => setAgain(true)}>Yes</button>
        <button className={again === false ? 'primary' : 'secondary'} onClick={() => setAgain(false)}>No</button>
      </div>
      <p>OK to share the group photo?</p>
      <div className="row">
        <button className={share === true ? 'primary' : 'secondary'} onClick={() => setShare(true)}>OK</button>
        <button className={share === false ? 'primary' : 'secondary'} onClick={() => setShare(false)}>No</button>
      </div>
      <p className="muted small">Private: nobody sees your rating, and a "meet again" only shows if they said yes too.</p>
      <button className="primary" disabled={!rating || again === null || share === null} onClick={send}>Send</button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="app">{children}</main>;
}
