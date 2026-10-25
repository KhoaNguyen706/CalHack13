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
    spot_name?: string | null; // shown once everyone has tapped We're here, to confirm the spot
    spot_about?: string | null;
    event?: string | null;
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

// The camera for the group photo, opened straight from the button: a selfie camera by default, so the group can pose
// and shoot at once, with one space per person, left to right in the same order as the pose picture, so everyone
// knows where to stand. Without camera access (an old browser, or permission refused) it falls back to the phone's
// own camera.
function GroupCamera({ names, onPhoto, onClose }: { names: string[]; onPhoto: (image: string) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const [facing, setFacing] = useState<'environment' | 'user'>('user');
  const [size, setSize] = useState<{ w: number; h: number }>(); // the live video's, once it plays
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!navigator.mediaDevices?.getUserMedia) return setFailed(true);
    let live = true;
    let stream: MediaStream | undefined;
    setSize(undefined);
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: facing, width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false })
      .then(s => {
        if (!live) return s.getTracks().forEach(t => t.stop());
        stream = s;
        if (video.current) video.current.srcObject = s;
      })
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
      stream?.getTracks().forEach(t => t.stop());
    };
  }, [facing]);

  // The frame as shown: the selfie camera is mirrored, so the photo matches what everyone saw.
  const shoot = () => {
    const v = video.current;
    if (!v?.videoWidth) return;
    const scale = Math.min(1, 1280 / Math.max(v.videoWidth, v.videoHeight));
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(v.videoWidth * scale);
    canvas.height = Math.round(v.videoHeight * scale);
    const ctx = canvas.getContext('2d')!;
    if (facing === 'user') {
      ctx.translate(canvas.width, 0);
      ctx.scale(-1, 1);
    }
    ctx.drawImage(v, 0, 0, canvas.width, canvas.height);
    buzz(20);
    onPhoto(canvas.toDataURL('image/jpeg', 0.82).split(',')[1]);
  };

  const ratio = size ? size.w / size.h : 3 / 4;
  return (
    <div className="camera" role="dialog" aria-label="Group photo">
      {failed ? (
        <div className="camera-off">
          <p>This browser can't open the camera here.</p>
          <input
            ref={input}
            type="file"
            accept="image/*"
            capture="environment"
            hidden
            onChange={e => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) shrink(file).then(onPhoto);
            }}
          />
          <button className="btn primary" onClick={() => input.current?.click()}><Icon name="camera" size={20} />Use the phone's camera</button>
        </div>
      ) : (
        <div className="camera-view" style={{ aspectRatio: `${ratio}`, width: `min(100%, calc((100dvh - 170px) * ${ratio}))` }}>
          <video
            ref={video}
            autoPlay
            playsInline
            muted
            className={facing === 'user' ? 'mirror' : undefined}
            onLoadedMetadata={e => setSize({ w: e.currentTarget.videoWidth, h: e.currentTarget.videoHeight })}
          />
          {/* One space per person, left to right like the pose picture. */}
          <div className="camera-spaces">
            {names.map((name, i) => <div key={i}><span>{name}</span></div>)}
          </div>
        </div>
      )}
      <div className="camera-bar">
        <button className="camera-btn" onClick={onClose} aria-label="Close the camera"><Icon name="close" size={22} /></button>
        <button className="shutter" disabled={!size} onClick={shoot} aria-label="Take the photo" />
        <button className="camera-btn" disabled={failed} onClick={() => setFacing(f => (f === 'user' ? 'environment' : 'user'))} aria-label="Switch camera">Flip</button>
      </div>
    </div>
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
  const [shooting, setShooting] = useState(false); // the group photo camera is open
  const [sending, setSending] = useState(false);
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
  const groupPhoto = async (image: string) => {
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
        <EndScreen photoUrl={photos[0]?.url} poseUrl={pose ? imageUrl(pose.image) : undefined} answer={round.answer} event={challenge?.event ?? undefined} />
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

      {/* 3. Everyone's at the spot: the answer to the hints, to confirm it, and your part of the pose picture. */}
      {unlocked && challenge?.spot_name && (
        <section className="card">
          <CardHead eyebrow="The spot" tone="green" title={challenge.spot_name.charAt(0).toUpperCase() + challenge.spot_name.slice(1)} />
          {challenge.spot_about && <p className="muted">{challenge.spot_about}</p>}
          <p className="muted small">Not there yet? Head over before you take the photo.</p>
        </section>
      )}
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
            {unlocked && <p className="why">Take a selfie together, each of you in your own space in the frame.</p>}
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
      {unlocked && (
        <button className="btn primary big" disabled={sending} onClick={() => setShooting(true)}>
          <Icon name="camera" size={24} />
          {sending ? 'Checking…' : notQuite ? 'Try another photo' : 'Take the group photo'}
        </button>
      )}
      {shooting && (
        <GroupCamera
          names={pose ? pose.cuts.slice(1).map((_, i) => holders[i] ?? 'Someone') : seats.map(s => nameOf(s.agentId))}
          onClose={() => setShooting(false)}
          onPhoto={image => {
            setShooting(false);
            setSending(true);
            run(groupPhoto(image)).finally(() => setSending(false));
          }}
        />
      )}

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

// iPhones save to Photos only from the share sheet ("Save Image"); a download goes to Files instead.
const IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

// For drawing into a canvas: CORS, and a URL of its own so a copy cached without CORS can't taint the canvas.
async function loadImage(url: string) {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  img.src = url + (url.includes('?') ? '&' : '?') + 'canvas=1';
  await img.decode();
  return img;
}

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number) {
  const lines: string[] = [];
  for (const word of text.split(' ')) {
    const last = lines[lines.length - 1];
    if (last !== undefined && ctx.measureText(`${last} ${word}`).width <= width) lines[lines.length - 1] = `${last} ${word}`;
    else lines.push(word);
  }
  return lines;
}

// A photo printed on cream paper, tilted, centered on (x, y) and fitting w by h.
function printed(ctx: CanvasRenderingContext2D, img: HTMLImageElement, x: number, y: number, w: number, h: number, degrees: number) {
  const scale = Math.min(w / img.naturalWidth, h / img.naturalHeight);
  const iw = img.naturalWidth * scale;
  const ih = img.naturalHeight * scale;
  const pad = 22;
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate((degrees * Math.PI) / 180);
  ctx.shadowColor = 'rgba(0, 0, 0, 0.45)';
  ctx.shadowBlur = 40;
  ctx.shadowOffsetY = 16;
  ctx.fillStyle = '#fbf8f1';
  ctx.fillRect(-iw / 2 - pad, -ih / 2 - pad, iw + 2 * pad, ih + 2 * pad + 36);
  ctx.shadowColor = 'transparent';
  ctx.drawImage(img, -iw / 2, -ih / 2, iw, ih);
  ctx.restore();
}

// The Story card (1080 x 1920, Instagram's size): the group photo, the meme they recreated, and what it was.
async function storyCard(photoUrl: string, poseUrl: string | undefined, answer: string | undefined, event: string | undefined) {
  await Promise.all(['700 80px Caveat', '700 64px Fredoka', '700 40px Nunito'].map(f => document.fonts.load(f).catch(() => [])));
  const photo = await loadImage(photoUrl);
  const meme = poseUrl ? await loadImage(poseUrl).catch(() => null) : null; // a host without CORS: no meme
  const [spot, poseName] = (answer ?? '').split(' · ');
  const W = 1080;
  const H = 1920;
  const canvas = document.createElement('canvas');
  canvas.width = W;
  canvas.height = H;
  const ctx = canvas.getContext('2d')!;
  const bg = ctx.createLinearGradient(0, 0, 0, H);
  bg.addColorStop(0, '#12403a');
  bg.addColorStop(1, '#07070a');
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, W, H);
  ctx.textAlign = 'center';
  ctx.fillStyle = '#2dd4bf';
  ctx.font = '700 88px Caveat';
  ctx.fillText('Spot & Pose', W / 2, 170);

  printed(ctx, photo, W / 2, 720, 860, 820, -2);
  if (meme) {
    printed(ctx, meme, W - 250, 1330, 380, 280, 5);
    ctx.save(); // its label, to the left of it and below the group photo
    ctx.translate(W - 470, 1350);
    ctx.rotate((-6 * Math.PI) / 180);
    ctx.textAlign = 'right';
    ctx.fillStyle = '#fbf8f1';
    ctx.font = '700 58px Caveat';
    ctx.fillText('the original →', 0, 0);
    ctx.restore();
  }

  let y = meme ? 1600 : 1460;
  ctx.textAlign = 'left';
  ctx.fillStyle = '#ffffff';
  ctx.font = '700 68px Fredoka';
  for (const line of wrap(ctx, poseName || 'We did it!', 900)) {
    ctx.fillText(line, 90, y);
    y += 78;
  }
  ctx.fillStyle = '#d4d4d8';
  ctx.font = '700 44px Nunito';
  if (spot) {
    for (const line of wrap(ctx, `at ${spot}`, 900)) {
      ctx.fillText(line, 90, y);
      y += 56;
    }
  }
  ctx.fillStyle = '#2dd4bf';
  ctx.font = '700 38px Nunito';
  ctx.fillText(event ? `${event} · made with Krew` : 'Made with Krew', 90, H - 110);
  return new Promise<Blob>((ok, fail) => canvas.toBlob(b => (b ? ok(b) : fail(new Error('no image'))), 'image/jpeg', 0.9));
}

function download(file: File) {
  const a = document.createElement('a');
  a.href = URL.createObjectURL(file);
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
}

// Through the phone's share sheet (Instagram there offers Stories); where sharing files isn't possible, a download.
async function shareFile(file: File) {
  try {
    if (navigator.canShare?.({ files: [file] })) await navigator.share({ files: [file] });
    else download(file);
  } catch {
    // closing the share sheet isn't an error
  }
}

// Once everyone said OK: the Story card to share, and the plain photo to keep. Both are ready before the tap, since
// phones only open the share sheet straight from one.
function SharePhoto({ photoUrl, poseUrl, answer, event }: { photoUrl: string; poseUrl?: string; answer?: string; event?: string }) {
  const [card, setCard] = useState<File>();
  const [photo, setPhoto] = useState<File>();
  const [preview, setPreview] = useState<string>();

  useEffect(() => {
    let live = true;
    let url = '';
    storyCard(photoUrl, poseUrl, answer, event)
      .then(blob => {
        if (!live) return;
        url = URL.createObjectURL(blob);
        setPreview(url);
        setCard(new File([blob], 'krew-story.jpg', { type: 'image/jpeg' }));
      })
      .catch(() => {});
    fetch(photoUrl)
      .then(r => r.blob())
      .then(b => live && setPhoto(new File([b], 'krew-group-photo.jpg', { type: b.type || 'image/jpeg' })))
      .catch(() => {});
    return () => {
      live = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [photoUrl, poseUrl, answer, event]);

  const save = (file: File) => (IOS && navigator.canShare?.({ files: [file] }) ? shareFile(file) : download(file));

  return (
    <div className="share">
      {preview ? <img className="story-preview" src={preview} alt="Your Story card" /> : <p className="muted small">Making your Story card…</p>}
      <button className="btn primary" disabled={!card} onClick={() => card && shareFile(card)}>Share to your Story</button>
      <button className="btn" disabled={!photo} onClick={() => photo && save(photo)}>Save the photo</button>
      {IOS && <p className="muted small">On iPhone, pick Save Image to keep it in Photos.</p>}
    </div>
  );
}

// Rating, meet again? and OK to share the photo? Ratings stay private; a "meet again" match shows only when both
// said yes; the photo can be shared only once everyone said OK.
function EndScreen({ photoUrl, poseUrl, answer, event }: { photoUrl?: string; poseUrl?: string; answer?: string; event?: string }) {
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
        {summary.share_ok && photoUrl ? (
          <SharePhoto photoUrl={photoUrl} poseUrl={poseUrl} answer={answer} event={event} />
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
