// Game page for one player: /?match=<match_id>&agent=<agent_id>
// Spot & Pose: meet your group, then put everyone's hints together to find the spot. When you're all there and have
// tapped "We're here", each phone shows its part of one meme picture: together the parts show the pose. One group
// photo there doing the pose, which the AI checks. Then the end screen (rating, meet again?, OK to share the photo?).
import { useEffect, useRef, useState } from 'react';
import { useReducer, useSpacetimeDB, useTable } from 'spacetimedb/react';
import { reducers, tables } from './module_bindings';
import { AGENTS, API, openedKey } from './config.ts';
import { buzz, CardHead, Confetti, Icon, MeetCard, Pebble, SignScreen, Walrus, type Meet, type PebbleMood } from './ui.tsx';

const params = new URLSearchParams(location.search);
const MATCH = params.get('match') ?? '';
const AGENT = params.get('agent') ?? '';
const CHAT = `?agent=${AGENT}`;
const VIEW = `krew-view:${MATCH}`;

// You've seen this challenge: from now on the chat lets you stay when you tap "<".
if (MATCH) {
  try {
    localStorage.setItem(openedKey(MATCH), '1');
  } catch {
    // private mode: the chat may open the challenge again, which is the old behavior
  }
}

// The picture, and where it's cut: part i runs from cuts[i] to cuts[i + 1] (fractions of the width). A pose found
// with Apify is a copy on the agents service ("/photos/<id>"); the hand-picked ones are full links.
type Pose = { image: string; cuts: number[]; top?: number };
type View = {
  state: string;
  members: { agent_id: string; name: string }[];
  challenge: {
    title?: string;
    goal?: string;
    steps?: { id: string; task: string }[];
    meet?: Meet | null;
    pose?: Pose | null;
    pieces?: Record<string, number> | null; // agent_id -> their part of the picture
    shared?: Record<string, string> | null; // agent_id -> the hint of someone who left
  } | null;
};
type Summary = { waiting_for: string[]; mutual_meet_again: boolean; my_matches: string[]; share_ok: boolean };

function imageUrl(url: string) {
  return url.startsWith('/') ? AGENTS + url : url;
}

// "phong-2" (a second Phong) still reads as Phong: the suffix only keeps ids unique.
function title(id: string) {
  const base = id.replace(/-\d+$/, '');
  return base.charAt(0).toUpperCase() + base.slice(1);
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

// The challenge as everyone may see it (title, goal, where to meet, the picture's cuts, who holds which part), from
// the agents service. The last copy is kept for this tab, so coming back to the game shows it at once.
function useChallenge() {
  const [view, setView] = useState<View | undefined>(() => {
    try {
      return JSON.parse(sessionStorage.getItem(VIEW) ?? 'null') ?? undefined;
    } catch {
      return undefined;
    }
  });
  useEffect(() => {
    let live = true, last = '';
    const tick = () =>
      fetch(`${AGENTS}/challenges/${MATCH}`)
        .then(r => (r.ok ? r.json() : null))
        .then(v => {
          const text = JSON.stringify(v);
          if (!live || !v || text === last) return;
          last = text;
          setView(v);
          try {
            sessionStorage.setItem(VIEW, text);
          } catch {
            // private mode
          }
        })
        .catch(() => {});
    tick();
    const id = setInterval(tick, 3000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);
  return view;
}

// height / width of an image; null if it can't be loaded.
function useRatio(src: string) {
  const [ratio, setRatio] = useState<number | null>();
  useEffect(() => {
    const img = new Image();
    img.onload = () => setRatio(img.naturalHeight / img.naturalWidth);
    img.onerror = () => setRatio(null);
    img.src = src;
  }, [src]);
  return ratio;
}

// The whole pose picture with only some parts showing: yours (and those of people who left). The other parts are
// covered with the name of who has them, so it's clear they're pieces of one picture.
function PoseFrame({ pose, mine, shown, holders }: { pose: Pose; mine?: number; shown: number[]; holders: string[] }) {
  const src = imageUrl(pose.image);
  const ratio = useRatio(src);
  const top = pose.top ?? 0;
  if (ratio === null) return <p className="muted small">Couldn't load the picture.</p>;
  if (ratio === undefined) return <p className="muted small">Loading the picture…</p>;
  const span = (i: number) => ({ left: `${pose.cuts[i] * 100}%`, width: `${(pose.cuts[i + 1] - pose.cuts[i]) * 100}%` });
  return (
    <div className="pose-frame" style={{ aspectRatio: `${1 / (ratio * (1 - top))}` }}>
      <img src={src} alt="Your part of the pose" style={{ marginTop: `${-top * ratio * 100}%` }} />
      {pose.cuts.slice(0, -1).map((_, i) =>
        i === mine ? (
          <div key={i} className="pose-mine" style={span(i)}><span>You</span></div>
        ) : shown.includes(i) ? null : (
          <div key={i} className="pose-cover" style={span(i)}><span>{holders[i] ?? 'Someone'}</span></div>
        ),
      )}
    </div>
  );
}

// A hidden photo input. With `facing` it opens the camera straight away on phones; without it, phones also offer the
// photo library, so a photo taken on the camera's timer works too.
function Camera({ facing, label, busy, big, onPhoto }: { facing?: 'user' | 'environment'; label: string; busy: string; big?: boolean; onPhoto: (f: File) => Promise<unknown> }) {
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

// Pebble pipes up for a moment when something happens: a friend arrives, everyone's here, the photo counts (or not).
type Reaction = { mood: PebbleMood; text: string; id: number };

function useReaction() {
  const [reaction, setReaction] = useState<Reaction>();
  useEffect(() => {
    if (!reaction) return;
    const t = setTimeout(() => setReaction(undefined), 2800);
    return () => clearTimeout(t);
  }, [reaction]);
  return [reaction, (mood: PebbleMood, text: string) => setReaction({ mood, text, id: Date.now() })] as const;
}

function Toast({ reaction }: { reaction?: Reaction }) {
  if (!reaction) return null;
  return (
    <div key={reaction.id} className="toast" role="status">
      <span className="avatar"><Pebble decorative mood={reaction.mood} size={44} /></span>
      <span>{reaction.text}</span>
    </div>
  );
}

function Waiting({ text, heading = 'Challenge', mood = 'thinking' }: { text: string; heading?: string; mood?: PebbleMood }) {
  return (
    <Shell>
      <Top heading={heading} />
      <section className="conn">
        <div className="avatar xl"><Pebble mood={mood} size={164} /></div>
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
  const view = useChallenge();

  const [error, setError] = useState('');
  const [sign, setSign] = useState(false); // the group's sign, full screen
  const [notQuite, setNotQuite] = useState(''); // why the last group photo didn't pass
  const [reaction, react] = useReaction();
  const joined = useRef(false);
  const arrived = useRef<Set<string> | null>(null);
  const lastState = useRef('');

  const round = rounds[0];
  const myHint = myKeys.find(k => k.matchId === MATCH)?.key;
  const mySeat = seats.find(s => identity && s.player?.isEqual(identity));
  const minutesLeft = useMinutesLeft(round?.startedAt.toDate().getTime(), round?.timerMin);
  const names = Object.fromEntries((view?.members ?? []).map(m => [m.agent_id, m.name]));
  const nameOf = (id: string) => names[id] ?? title(id);
  const challenge = view?.challenge;
  const pose = challenge?.pose;
  const pieces = challenge?.pieces ?? {};
  const shared = Object.entries(challenge?.shared ?? {});
  const holders: string[] = [];
  for (const [id, i] of Object.entries(pieces)) holders[i] = nameOf(id);
  const photoStep = challenge?.steps?.[0]?.id;

  // Claim this person's seat once the round exists.
  useEffect(() => {
    if (!isActive || !round || joined.current) return;
    joined.current = true;
    join({ matchId: MATCH, agentId: AGENT }).catch(e => setError(String(e?.message ?? e)));
  }, [isActive, round, join]);

  // Someone else just tapped We're here: say so, by name (everyone has accepted by now).
  useEffect(() => {
    if (!seats.length) return;
    const now = new Set(seats.filter(s => s.checkedIn).map(s => String(s.seatId)));
    if (arrived.current) {
      const fresh = seats.find(s => s.checkedIn && !arrived.current!.has(String(s.seatId)) && s.seatId !== mySeat?.seatId);
      if (fresh) react('excited', `${nameOf(fresh.agentId)} is here!`);
    }
    arrived.current = now;
  }, [seats]);

  // Everyone's at the spot: the pose shows up.
  useEffect(() => {
    if (!round) return;
    if (lastState.current === 'playing' && round.state === 'unlocked') {
      react('cheer', "Everyone's here. Here's the pose!");
      buzz([12, 50, 12]);
    }
    lastState.current = round.state;
  }, [round?.state]);

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

  // The group photo: the AI checks the spot and the pose. Once it passes it goes to the agents service (stores it,
  // gives a link) and then the live board, which completes the round on every phone.
  const groupPhoto = async (file: File) => {
    const image = await shrink(file);
    if (photoStep) {
      const out = await post<{ passed: boolean; reason: string }>(`${AGENTS}/challenges/${MATCH}/check`, {
        step: photoStep, agent_id: AGENT, image_base64: image, media_type: 'image/jpeg',
      });
      if (!out.passed) {
        buzz(30);
        react('oops', 'Not quite. Try another photo.');
        return setNotQuite(out.reason);
      }
    }
    setNotQuite('');
    const { path } = await post<{ path: string }>(`${AGENTS}/photos`, { match_id: MATCH, agent_id: AGENT, image_base64: image });
    await submitPhoto({ matchId: MATCH, url: AGENTS + path });
    buzz([12, 60, 12, 60, 24]);
  };

  const heading = challenge?.title ?? 'Challenge';
  if (!MATCH || !AGENT) return <Waiting text="Open the game link from your agent." mood="oops" />;
  if (!isActive) return <Waiting text="Connecting…" heading={heading} mood="sleepy" />;
  if (!round) return <Waiting text="Waiting for your round to start…" heading={heading} />;

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
        <Celebrate />
        <section className="card done">
          <div className="speaker">
            <div className="avatar lg"><Pebble mood="cheer" size={84} /></div>
            <CardHead eyebrow="Challenge done" tone="green" title="You did it!" sub={others.length ? `With ${others.join(' and ')}` : undefined} />
          </div>
          {round.answer && <p>It was <b>{round.answer}</b>.</p>}
          {photos[0] && <img className="group-photo" src={photos[0].url} alt="Your group photo" />}
          {pose && (
            <>
              <p className="eyebrow muted">The original</p>
              <img className="group-photo" src={imageUrl(pose.image)} alt="The pose you recreated" />
            </>
          )}
        </section>
        <EndScreen photoUrl={photos[0]?.url} />
        {error && <p className="error">{error}</p>}
      </Shell>
    );
  }

  const unlocked = round.state === 'unlocked';
  const here = seats.filter(s => s.checkedIn).length;
  const meet = challenge?.meet;

  return (
    <Shell>
      <Top heading={challenge?.title ?? 'Challenge'} minutes={minutesLeft} />

      {/* 1. Meet your group: where, and the sign to hold up. */}
      {meet && round.state === 'playing' && <MeetCard meet={meet} onShow={() => setSign(true)} />}
      {meet && sign && <SignScreen meet={meet} onClose={() => setSign(false)} />}

      {/* 2. Find the spot: your hint, until everyone's there. */}
      {round.state === 'playing' && (myHint !== undefined ? (
        <section className="card hero">
          <CardHead eyebrow="Your hint for the spot" />
          <h2 className={myHint.length > 60 ? 'mid' : undefined}>{myHint}</h2>
          {shared.map(([id, hint]) => <p key={id} className="muted">{nameOf(id)} left. Their hint: <b>{hint}</b></p>)}
          <p className="muted small">Everyone has a different hint. Put them together to work out where to go.</p>
        </section>
      ) : (
        <section className="card plain"><p className="muted">Joining as {nameOf(AGENT)}…</p></section>
      ))}

      {/* 3. Everyone's at the spot: your part of the pose picture. */}
      {unlocked && pose && (
        <section className="card hero">
          <CardHead eyebrow="The pose" title="Put your parts together" />
          <PoseFrame
            pose={pose}
            mine={pieces[AGENT]}
            shown={shared.map(([id]) => pieces[id]).filter(i => i !== undefined)}
            holders={holders}
          />
          <p className="muted">Each of you has one part of the same picture. Show each other your screens to see the whole pose, then do it together right here.</p>
        </section>
      )}

      <section className="card plain">
        <span className="eyebrow muted">Steps</span>
        <ol className="step-list">
          <li className={unlocked ? 'done' : 'cur'}>
            <p className="step"><span className="dot">{unlocked && <Icon name="check" size={14} />}</span>Find the spot together and tap We're here</p>
          </li>
          <li className={unlocked ? 'cur' : 'lock'}>
            <p className="step"><span className="dot" />Do the pose and take the group photo there</p>
            {!unlocked && <p className="why">The pose shows up when everyone has tapped We're here.</p>}
            {unlocked && <p className="why">Ask someone nearby to take it, or prop a phone up and use its timer.</p>}
            {notQuite && <p className="why not">Not quite: {notQuite}</p>}
          </li>
        </ol>
      </section>

      <section className="card plain">
        <div className="split-head">
          <span className="eyebrow muted">Who's at the spot</span>
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
        <button className="btn primary big" disabled={!mySeat} onClick={() => { buzz(14); run(checkIn({ matchId: MATCH })); }}>We're here</button>
      ))}
      {unlocked && <Camera big label={notQuite ? 'Try another photo' : 'Add the group photo'} busy="Checking…" onPhoto={f => run(groupPhoto(f))} />}

      <button className="link" onClick={() => run(leave())}>Leave challenge</button>
      {error && <p className="error">{error}</p>}
      <Toast reaction={reaction} />
    </Shell>
  );
}

// Confetti the first time this phone sees the challenge finished (not again when you come back to rate it).
function Celebrate() {
  const [first] = useState(() => {
    try {
      if (sessionStorage.getItem(`krew-done:${MATCH}`)) return false;
      sessionStorage.setItem(`krew-done:${MATCH}`, '1');
    } catch {
      // private mode: celebrate anyway
    }
    return true;
  });
  useEffect(() => {
    if (first) buzz([16, 70, 16, 70, 30]);
  }, [first]);
  return first ? <Confetti /> : null;
}

// Pebble's face follows your stars while you pick them.
const FACE_FOR_RATING: PebbleMood[] = ['happy', 'sad', 'oops', 'happy', 'cheer', 'love'];

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
        <div className="speaker">
          <div className="avatar lg"><Pebble mood={mine.length ? 'love' : 'happy'} size={84} /></div>
          <CardHead eyebrow="Thanks!" title={mine.length ? 'You want to meet again' : 'Your answers are in'} />
        </div>
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
          <div className="speaker">
            <div className="avatar"><Pebble mood={FACE_FOR_RATING[rating]} size={50} /></div>
            <p><b>How was it?</b></p>
          </div>
          <span className="private"><Icon name="lock" size={14} />Private</span>
        </div>
        <div className="stars" role="radiogroup" aria-label="Rating">
          {[1, 2, 3, 4, 5].map(n => (
            <button key={n} className={n <= rating ? 'on' : ''} style={{ '--i': n } as React.CSSProperties} role="radio" aria-checked={n === rating} aria-label={`${n} of 5`} onClick={() => { buzz(6); setRating(n); }}>
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
