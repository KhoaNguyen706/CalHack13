// Game page for one player: /?match=<match_id>&agent=<agent_id>
// The challenge the AI invented for this group: your part (and hint), who's here, "We're here", then the photo steps
// (each checked by the AI), the group photo, and the end screen (rating, meet again?, OK to share the photo?).
import { useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import { AGENTS, API } from './config.ts';
import { CardHead, Icon, MeetCard, Pebble, SignScreen, Walrus, type Meet } from './ui.tsx';

const params = new URLSearchParams(location.search);
const MATCH = params.get('match') ?? '';
const AGENT = params.get('agent') ?? '';
const CHAT = `?agent=${AGENT}`;

type Step = { id: string; task: string; passed: boolean; reason: string | null };
type View = {
  state: string;
  members: { agent_id: string; name: string }[];
  challenge: { title?: string; goal?: string; steps?: { id: string; task: string }[]; meet?: Meet | null } | null;
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
function Camera({ facing, label, busy, big, onPhoto }: { facing: 'user' | 'environment'; label: string; busy: string; big?: boolean; onPhoto: (f: File) => Promise<unknown> }) {
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
      <button className={big ? 'btn primary big' : 'btn'} disabled={working} onClick={() => input.current?.click()}>
        <Icon name="camera" size={big ? 24 : 20} />
        {working ? busy : label}
      </button>
    </>
  );
}

// Top bar: back to your agent, the screen's title, minutes left.
function Top({ heading, minutes }: { heading: string; minutes?: number }) {
  return (
    <header className="topbar">
      <a className="icon-btn" href={CHAT} aria-label="Back to chat"><Icon name="back" size={22} /></a>
      <h1>{heading}</h1>
      {minutes !== undefined && <span className="timer"><Icon name="clock" size={16} />{minutes} min left</span>}
    </header>
  );
}

function Waiting({ text }: { text: string }) {
  return (
    <Shell>
      <Top heading="Challenge" />
      <section className="conn">
        <div className="avatar xl"><Pebble mood="thinking" size={164} /></div>
        <h2>{text}</h2>
      </section>
    </Shell>
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
  const [sign, setSign] = useState(false); // the group's sign, full screen
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
    location.assign(CHAT);
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

  if (!MATCH || !AGENT) return <Waiting text="Open the game link from your agent." />;
  if (!isActive) return <Waiting text="Connecting…" />;
  if (!round) return <Waiting text="Waiting for your round to start…" />;

  // Called off: too few people were left. The Coordinator says sorry and your agent keeps looking.
  if (round.state === 'cancelled') {
    return (
      <Shell>
        <Top heading="Chat" />
        <div className="grow" />
        <p className="bubble">Quick update from the Coordinator.</p>
        <section className="card plain center" style={{ alignItems: 'center' }}>
          <div className="avatar violet lg"><Walrus mood="sorry" size={90} /></div>
          <CardHead eyebrow="Called off" tone="muted" title="This challenge was called off." />
          <p className="muted">Too few people were left to finish it. I'm sorry about that. I'll keep an eye out for the next one.</p>
          <a className="btn primary" style={{ alignSelf: 'stretch' }} href={CHAT}>Back to chat</a>
        </section>
      </Shell>
    );
  }

  if (round.state === 'done') {
    const others = view?.members.filter(m => m.agent_id !== AGENT).map(m => m.name) ?? [];
    return (
      <Shell>
        <Top heading="All done" />
        <section className="card">
          <CardHead eyebrow="Challenge done" tone="green" title="You did it!" sub={others.length ? `With ${others.join(' and ')}` : undefined} />
          {photos[0] && <img className="group-photo" src={photos[0].url} alt="Your group photo" />}
        </section>
        <EndScreen photoUrl={photos[0]?.url} />
        {error && <p className="error">{error}</p>}
      </Shell>
    );
  }

  const unlocked = round.state === 'unlocked';
  const passed = steps.filter(s => s.passed).length;
  const here = seats.filter(s => s.checkedIn).length;
  const meet = view?.challenge?.meet;

  return (
    <Shell>
      <Top heading={view?.challenge?.title ?? 'Challenge'} minutes={minutesLeft} />

      {/* Until everyone's here: where to meet and the sign to hold up, before anything else. */}
      {meet && round.state === 'playing' && <MeetCard meet={meet} onShow={() => setSign(true)} />}
      {meet && sign && <SignScreen meet={meet} onClose={() => setSign(false)} />}

      {myKey ? (
        <section className="card hero">
          <CardHead eyebrow="Your part" />
          <h2>{myKey.key}</h2>
          {view?.challenge?.goal && <p className="muted">{view.challenge.goal}</p>}
          <p className="muted small">Tell your group your part. Nobody can finish alone.</p>
          {myHint && (showHint ? <p className="hint">💡 {myHint}</p> : <button className="link" onClick={() => setShowHint(true)}>Need a hint?</button>)}
        </section>
      ) : (
        <section className="card plain"><p className="muted">Joining as {nameOf(AGENT)}…</p></section>
      )}

      <section className="card plain">
        <div className="split-head">
          <span className="eyebrow muted">Steps</span>
          {steps.length > 0 && <span className="muted small"><b>{passed} of {steps.length}</b> done</span>}
        </div>
        <ol className="step-list">
          <li className={unlocked ? 'done' : 'cur'}>
            <p className="step"><span className="dot">{unlocked && <Icon name="check" size={14} />}</span>{meet ? `Meet at ${meet.spot} and tap We're here` : "Meet your group and tap We're here"}</p>
          </li>
          {steps.map(s => (
            <li key={s.id} className={s.passed ? 'done' : unlocked ? 'cur' : 'lock'}>
              <p className="step"><span className="dot">{s.passed && <Icon name="check" size={14} />}</span>{s.task}</p>
              {s.passed && s.reason && <p className="why">{s.reason}</p>}
              {!s.passed && unlocked && (
                <>
                  {notQuite[s.id] && <p className="why not">Not quite: {notQuite[s.id]}</p>}
                  <Camera facing="environment" label={notQuite[s.id] ? 'Try another photo' : 'Photo for this step'} busy="Checking…" onPhoto={f => run(checkStep(s.id, f))} />
                </>
              )}
            </li>
          ))}
          <li className={unlocked && passed === steps.length ? 'cur' : 'lock'}>
            <p className="step"><span className="dot" />Take the group photo</p>
          </li>
        </ol>
      </section>

      <section className="card plain">
        <div className="split-head">
          <span className="eyebrow muted">Who's here</span>
          <b className="small" style={{ color: here === seats.length ? 'var(--green-text)' : 'var(--muted)' }}>{here} of {seats.length} here</b>
        </div>
        <div className="marks left">
          {seats.map(s => (
            <span key={s.seatId} className={`mark big${s.checkedIn ? ' ok' : ''}`}>
              <Icon name={s.checkedIn ? 'check' : 'clock'} size={16} />
              {s.seatId === mySeat?.seatId ? 'You' : nameOf(s.agentId)}
              <small>· {s.checkedIn ? 'here' : s.player ? 'on the way' : 'not joined'}</small>
            </span>
          ))}
        </div>
      </section>

      <div className="grow" />

      {round.state === 'playing' && (mySeat?.checkedIn ? (
        <div className="status big"><Icon name="clock" size={20} /><span>Waiting for the others…</span></div>
      ) : (
        <button className="btn primary big" disabled={!mySeat} onClick={() => run(checkIn({ matchId: MATCH }))}>We're here</button>
      ))}
      {unlocked && <Camera facing="user" big label="Take the group photo" busy="Sending the photo…" onPhoto={f => run(groupPhoto(f))} />}

      <button className="link" onClick={() => run(leave())}>Leave challenge</button>
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
    const mine = summary.my_matches;
    return (
      <section className="card">
        <CardHead eyebrow="Thanks!" title={mine.length ? 'You want to meet again' : 'Your answers are in'} />
        {mine.length > 0 ? (
          <p>
            {mine.length === 1
              ? `You and ${mine[0]} both want to meet again.`
              : `You, ${mine.slice(0, -1).join(', ')} and ${mine[mine.length - 1]} all want to meet again.`}
          </p>
        ) : (
          <p className="muted">Your answers stay private. If someone you'd meet again says yes too, you'll see it here.</p>
        )}
        {summary.waiting_for.length > 0 && <p className="muted small">Waiting for {summary.waiting_for.length} more to answer.</p>}
        {summary.share_ok ? (
          <button className="btn primary" onClick={sharePhoto}>Share the group photo</button>
        ) : (
          <p className="note"><Icon name="lock" size={16} /><span>The photo is only shared when everyone says yes. Nobody is told who said no.</span></p>
        )}
        <a className="btn" href={CHAT}>Back to your agent</a>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="question">
        <div className="split-head">
          <p><b>How was it?</b></p>
          <span className="private"><Icon name="lock" size={14} />Private</span>
        </div>
        <div className="stars" role="radiogroup" aria-label="Rating">
          {[1, 2, 3, 4, 5].map(n => (
            <button key={n} className={n <= rating ? 'on' : ''} role="radio" aria-checked={n === rating} aria-label={`${n} of 5`} onClick={() => setRating(n)}>
              <Icon name="star" size={36} />
            </button>
          ))}
        </div>
      </div>
      <div className="question">
        <p>Meet them again?</p>
        <div className="btn-row">
          <button className="choice" aria-pressed={again === true} onClick={() => setAgain(true)}>Yes</button>
          <button className="choice" aria-pressed={again === false} onClick={() => setAgain(false)}>Not now</button>
        </div>
      </div>
      <div className="question">
        <span className="eyebrow amber">Needs everyone's OK</span>
        <p>Share the group photo?</p>
        <div className="btn-row">
          <button className="choice" aria-pressed={share === true} onClick={() => setShare(true)}>I'm OK sharing</button>
          <button className="choice" aria-pressed={share === false} onClick={() => setShare(false)}>No</button>
        </div>
      </div>
      <p className="note"><Icon name="lock" size={16} /><span>Nobody sees your rating, and a "meet again" only shows if they said yes too.</span></p>
      <button className="btn primary" disabled={!rating || again === null || share === null} onClick={send}>Done</button>
      {error && <p className="error">{error}</p>}
    </section>
  );
}

function Shell({ children }: { children: React.ReactNode }) {
  return <main className="app">{children}</main>;
}
