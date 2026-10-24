// Sign-in and the chat with your personal agent (agents service): /?agent=<agent_id>
// Your agent checks you in only after you tap Share, answers the coordinator's invitations for you, and you approve
// every step here.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useRef, useState } from 'react';
import { AGENTS, API } from './config.ts';
import InstallHint from './InstallHint.tsx';
import { CardHead, Icon, Pebble, PieceIcon, PRIVACY_RULES, Reconnecting, Seats, Walrus, type Seat } from './ui.tsx';

// Groups are 2 to 5 (2 for a quick round with one other person). The host can start early once this many accepted;
// the coordinator's MIN_GROUP_SIZE has the final say on both.
const SIZES = [2, 3, 4, 5];
const START_EARLY = 2;
const KEY = 'threekey-agent';

type Line = { role: 'agent' | 'user' | 'update'; text: string };
type Match = {
  match_id: string; members: string[]; accepted: string[]; state: string; window: string; area: string;
  host?: string | null; size?: number | null;
};
type Shared = { free: string; area: string; interests: string[]; skills: string[] };
type Status = {
  agent_id: string; name: string; transcript: Line[]; match: Match | null; is_host: boolean; needs_accept: boolean;
  my_part: string | null; challenge: { title?: string; goal?: string } | null; consented: boolean; would_share: Shared;
  last_match: string | null;
};

// Sign in with SpacetimeAuth: OpenID Connect authorization code + PKCE. The page only ever holds a one-time code;
// the PKCE secret sits in localStorage, so the magic-link email can finish sign-in in a new tab of the same browser;
// the agents service trades the code for the ID token and keeps just the first name.
type AuthConfig = { spacetimeauth: { authority: string; client_id: string } | null };
const PKCE = 'krew-signin';
const REDIRECT = `${location.origin}/`;

function b64url(bytes: Uint8Array) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function goSignIn(authority: string, clientId: string) {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const challenge = b64url(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier))));
  localStorage.setItem(PKCE, JSON.stringify({ verifier, state }));
  const q = new URLSearchParams({
    response_type: 'code', client_id: clientId, redirect_uri: REDIRECT, scope: 'openid profile',
    code_challenge: challenge, code_challenge_method: 'S256', state,
  });
  location.assign(`${authority}/auth?${q}`);
}

async function call<T>(base: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(base + path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
  return res.json();
}

function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function remember(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // private mode: this tab still works, it just won't remember you
  }
}

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

const DAYS: Record<string, string> = { Mon: 'Monday', Tue: 'Tuesday', Wed: 'Wednesday', Thu: 'Thursday', Fri: 'Friday', Sat: 'Saturday', Sun: 'Sunday' };

// "Sat 14:00-15:30" -> "Saturday · 2:00 – 3:30 PM". Anything else is shown as it came.
function when(window: string) {
  const m = window.match(/^(\w{3}) (\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$/);
  if (!m) return window;
  const [, day, h1, m1, h2, m2] = m;
  const clock = (h: string, min: string) => `${((+h + 11) % 12) + 1}:${min}`;
  const half = (h: string) => (+h < 12 ? 'AM' : 'PM');
  const start = clock(h1, m1) + (half(h1) === half(h2) ? '' : ` ${half(h1)}`);
  return `${DAYS[day] ?? day} · ${start} – ${clock(h2, m2)} ${half(h2)}`;
}

function useOnline() {
  const [online, setOnline] = useState(navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true), off = () => setOnline(false);
    addEventListener('online', on);
    addEventListener('offline', off);
    return () => {
      removeEventListener('online', on);
      removeEventListener('offline', off);
    };
  }, []);
  return online;
}

const QUERY = new URLSearchParams(location.search);
const AGENT = QUERY.get('agent') ?? remembered(KEY) ?? '';

export default function Lobby() {
  if (QUERY.has('code') && QUERY.has('state')) return <FinishSignIn />;
  return AGENT ? <Chat agentId={AGENT} /> : <Login />;
}

function enter(s: Status) {
  remember(KEY, s.agent_id);
  location.assign(`?agent=${s.agent_id}`);
}

function FinishSignIn() {
  const [error, setError] = useState('');
  useEffect(() => {
    const saved = JSON.parse(localStorage.getItem(PKCE) ?? 'null') as { verifier: string; state: string } | null;
    localStorage.removeItem(PKCE);
    if (!saved || saved.state !== QUERY.get('state')) return setError("That sign-in link didn't come from this page. Try again.");
    call<Status>(AGENTS, '/users/spacetime', { code: QUERY.get('code'), code_verifier: saved.verifier, redirect_uri: REDIRECT })
      .then(enter)
      .catch(e => setError(String(e.message)));
  }, []);
  return (
    <main className="app">
      <section className="conn">
        <div className="avatar xl"><Pebble mood="thinking" size={164} /></div>
        {error ? (
          <>
            <h2>That didn't work.</h2>
            <p className="muted">{error}</p>
            <a className="btn primary" href="/">Back to sign-in</a>
          </>
        ) : (
          <h2>Signing you in…</h2>
        )}
      </section>
    </main>
  );
}

function Login() {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [auth, setAuth] = useState<AuthConfig['spacetimeauth'] | undefined>(undefined); // undefined = still checking
  const [people, setPeople] = useState<{ id: string; name: string }[]>([]);

  useEffect(() => {
    call<AuthConfig>(AGENTS, '/auth/config').then(c => setAuth(c.spacetimeauth)).catch(() => setAuth(null));
    call<{ id: string; name: string }[]>(AGENTS, '/demo/people').then(setPeople).catch(() => setPeople([]));
  }, []);

  // A demo presenter: their agent already knows them (agents/seeds/demo_people.json).
  const presenter = (person: string) => {
    setBusy(true);
    call<Status>(AGENTS, '/users/demo', { person }).then(enter).catch(e => setError(String(e.message))).finally(() => setBusy(false));
  };

  const demo = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    // A fresh identity per sign-in, so a second window becomes a second person.
    call<Status>(AGENTS, '/users/login', { identity: crypto.randomUUID(), name: name.trim() })
      .then(enter)
      .catch(e => setError(String(e.message)))
      .finally(() => setBusy(false));
  };

  return (
    <main className="app">
      <section className="welcome">
        <div className="avatar xl"><Pebble mood="happy" size={164} /></div>
        <h1>Meet Pebble, your agent</h1>
        <p className="muted">
          I find a small group nearby for a quick challenge you can only win together. You approve every step, and
          nothing about you is shared until you say OK.
        </p>
      </section>
      {auth && (
        <section className="card">
          <button className="btn primary" disabled={busy} onClick={() => { setBusy(true); goSignIn(auth.authority, auth.client_id).catch(e => { setBusy(false); setError(String(e.message)); }); }}>
            {busy ? 'Opening sign-in…' : 'Sign in'}
          </button>
          <p className="note">
            <Icon name="lock" size={16} />
            <span>Secure sign-in by SpacetimeAuth. Krew keeps only your first name; your email and photo are never stored or shared.</span>
          </p>
        </section>
      )}
      {auth === undefined && <p className="muted center">Loading sign-in…</p>}
      {people.length > 0 && (
        <section className="card plain">
          <CardHead eyebrow="Demo · who are you?" />
          <div className="people">
            {people.map(p => (
              <button key={p.id} className="btn" disabled={busy} onClick={() => presenter(p.id)}>I'm {p.name}</button>
            ))}
          </div>
          <p className="muted small">Your agent already knows you, like a personal agent you've used for a while.</p>
        </section>
      )}
      <details className="card plain" open={auth === null}>
        <summary>Just trying it out? Use the demo with a first name</summary>
        <form className="stack" onSubmit={demo}>
          <input className="field" value={name} onChange={e => setName(e.target.value)} placeholder="Your first name" required maxLength={40} aria-label="Your first name" />
          <p className="muted small">First name only. No photo. Your agent starts with a sample profile, like one a real personal agent would already have.</p>
          <button className="btn primary" type="submit" disabled={busy || !name.trim()}>Start the demo</button>
        </form>
      </details>
      <InstallHint />
      {error && <p className="error">{error}</p>}
    </main>
  );
}

// Seats for this group: you, the people in it (letters until everyone accepts), and the seats still open.
function seatsFor(m: Match, me: string): Seat[] {
  const size = Math.max(m.size ?? m.members.length, m.members.length);
  const named = ['confirmed', 'playing', 'done'].includes(m.state);
  const accepted = (p: string) => m.accepted.includes(p);
  const you: Seat = accepted(me) && m.state !== 'inviting' ? { state: 'acc', name: 'You' } : { state: 'you' };
  const others: Seat[] = m.members
    .filter(p => p !== me)
    .map(p => ({ state: accepted(p) ? 'acc' : 'in', name: named ? title(p) : undefined }));
  const open: Seat[] = Array.from({ length: Math.max(0, size - m.members.length) }, () => ({ state: m.state === 'inviting' ? 'think' : 'open' }));
  return [you, ...others, ...open];
}

function Chat({ agentId }: { agentId: string }) {
  const [status, setStatus] = useState<Status>();
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState('');
  const [lost, setLost] = useState(false); // polling the agent failed
  const [tab, setTab] = useState<'chat' | 'flow'>('chat');
  const [menu, setMenu] = useState(false);
  const [size, setSize] = useState(0);
  const [poke, setPoke] = useState(0); // "Try now" polls again straight away
  const online = useOnline();
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    const tick = () =>
      call<Status>(AGENTS, `/users/${agentId}`)
        .then(s => {
          if (!live) return;
          setStatus(s);
          setLost(false);
          if (s.match?.state === 'playing') location.assign(`?match=${s.match.match_id}&agent=${agentId}`);
        })
        .catch(() => live && setLost(true));
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [agentId, poke]);

  const lines = status?.transcript.length ?? 0;
  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call it as the cleanup.
  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [lines, thinking, tab]);

  const run = (p: Promise<Status | unknown>) => {
    setError('');
    p.then(s => s && typeof s === 'object' && 'transcript' in s && setStatus(s as Status)).catch(e => setError(String(e.message)));
  };

  const send = (e: React.FormEvent) => {
    e.preventDefault();
    const message = draft.trim();
    if (!message) return;
    setDraft('');
    setThinking(true);
    run(call<Status>(AGENTS, `/chat/${agentId}`, { message }).finally(() => setThinking(false)));
  };

  const switchPerson = () => {
    remember(KEY, null);
    location.assign('?');
  };

  if (!status) {
    return (
      <main className="app">
        {lost || !online ? (
          <>
            <Reconnecting offline={!online} onRetry={() => setPoke(n => n + 1)} />
            <button className="link" onClick={switchPerson}>Sign in again</button>
          </>
        ) : (
          <section className="conn">
            <div className="avatar xl"><Pebble mood="thinking" size={164} /></div>
            <h2>Waking up your agent…</h2>
          </section>
        )}
      </main>
    );
  }

  const m = status.match;
  const accept = (yes: boolean) => run(call(AGENTS, `/users/${agentId}/accept`, { accept: yes }));
  const leave = () => m && confirm('Leave this challenge?') && run(call(API, `/matches/${m.match_id}/leave`, { agent_id: agentId }));

  return (
    <main className="app chat">
      <header className="topbar">
        <div className="avatar"><Pebble size={50} /></div>
        <div className="who"><b>Pebble</b><span>Your agent{status.name ? ` · ${status.name}` : ''}</span></div>
        <button className="icon-btn" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu(true)}><Icon name="menu" size={22} /></button>
      </header>

      <div className="tabs" role="tablist" aria-label="Views">
        <button role="tab" aria-selected={tab === 'chat'} onClick={() => setTab('chat')}>Chat</button>
        <button role="tab" aria-selected={tab === 'flow'} onClick={() => setTab('flow')}>Flow</button>
      </div>

      {(lost || !online) && (
        <p className="note center">
          <Icon name="clock" size={14} />
          <span>{online ? 'Hmm, I lost the connection. Trying again…' : 'No signal right now. I’ll pick up where we left off.'}</span>
        </p>
      )}

      {tab === 'flow' ? (
        <Flow match={m} me={agentId} />
      ) : (
        <>
          <section className="transcript" aria-live="polite">
            {status.transcript.map((line, i) => (
              <p key={i} className={`bubble ${line.role}`}>{line.text}</p>
            ))}
            {thinking && <p className="bubble typing" aria-label="Pebble is typing">•••</p>}
            <div ref={end} />
          </section>

          {!status.consented ? (
            <Consent shared={status.would_share} onShare={yes => run(call(AGENTS, `/users/${agentId}/consent`, { share: yes }))} />
          ) : m ? (
            <MatchCard
              m={m}
              me={agentId}
              status={status}
              onAccept={accept}
              onStart={() => run(call(API, `/challenges/${m.match_id}/start`, {}))}
              onCancel={() => run(call(API, `/challenges/${m.match_id}/cancel`, {}))}
              onLeave={leave}
            />
          ) : (
            <>
              {status.last_match && (
                <section className="card plain">
                  <CardHead eyebrow="Your last challenge" title="How was it?" sub="Rate it privately and see who'd meet again." />
                  <a className="btn" href={`?match=${status.last_match}&agent=${agentId}`}>Rate it</a>
                </section>
              )}
              <section className="card">
                <CardHead eyebrow="New" title="Start a challenge" />
                <p className="muted">How many people? Groups are 2 to 5.</p>
                <div className="sizes" role="group" aria-label="Group size">
                  {SIZES.map(n => (
                    <button key={n} aria-pressed={size === n} onClick={() => setSize(n)}><b>{n}</b><span>people</span></button>
                  ))}
                </div>
                <button
                  className="btn primary"
                  aria-disabled={!size}
                  onClick={() => size && run(call(AGENTS, `/users/${agentId}/start`, { size }))}
                >
                  {size ? `Start a ${size}-person challenge` : 'Pick a size first'}
                </button>
                <p className="note center"><Icon name="lock" size={14} /><span>Public spots only. Everyone accepts before anyone meets.</span></p>
              </section>
            </>
          )}

          <form className="composer" onSubmit={send}>
            <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Message Pebble…" maxLength={2000} aria-label="Message your agent" />
            <button className="icon-btn" type="submit" aria-label="Send" disabled={thinking || !draft.trim()}><Icon name="send" size={22} /></button>
          </form>
        </>
      )}
      {error && <p className="error">{error}</p>}

      {menu && <Menu agentId={agentId} onClose={() => setMenu(false)} onSwitch={switchPerson} />}
    </main>
  );
}

// "Before I share anything, I need your OK." Only rough info, and only after Share.
function Consent({ shared, onShare }: { shared: Shared; onShare: (yes: boolean) => void }) {
  const rows = [
    ['Rough free time', shared.free],
    ['Area, not exact spot', shared.area],
    ['Interests', shared.interests.join(', ') || "You haven't told me yet"],
    ['Good at', shared.skills.join(', ') || "You haven't told me yet"],
  ];
  return (
    <section className="card">
      <CardHead eyebrow="Before we start" title="OK to share?" />
      <div className="share-rows">
        {rows.map(([k, v]) => (
          <div key={k} className="share-row">
            <PieceIcon />
            <div><span>{k}</span><b>{v}</b></div>
          </div>
        ))}
      </div>
      <p className="note">
        <Icon name="lock" size={16} />
        <span>No name, no exact location, no photo. Others see you as a letter until everyone says yes.</span>
      </p>
      <div className="btn-row">
        <button className="btn" onClick={() => onShare(false)}>Not now</button>
        <button className="btn primary wide" onClick={() => onShare(true)}>Share</button>
      </div>
    </section>
  );
}

type MatchProps = {
  m: Match; me: string; status: Status;
  onAccept: (yes: boolean) => void; onStart: () => void; onCancel: () => void; onLeave: () => void;
};

// The one card for the challenge you're in: invitation, your group filling, your call, accept, locked in.
function MatchCard({ m, me, status, onAccept, onStart, onCancel, onLeave }: MatchProps) {
  const seats = Math.max(m.size ?? m.members.length, m.members.length);
  const full = m.members.length >= seats;
  const joined = m.members.length;
  const seatList = seatsFor(m, me);
  const meta = (
    <div className="meta">
      <div><Icon name="clock" /><span>{when(m.window)}</span></div>
      <div><Icon name="pin" /><span>{m.area} · {seats} people</span></div>
    </div>
  );

  // Invited: the Coordinator asks; nothing happens without your tap.
  if (status.needs_accept && !status.is_host && !full) {
    return (
      <section className="card violet">
        <div className="speaker">
          <div className="avatar violet"><Walrus mood="sorry" size={52} /></div>
          <div>
            <span className="eyebrow violet">Coordinator</span>
            <span>Hi! Sorry to bother you. Someone nearby is starting a challenge. Interested?</span>
          </div>
        </div>
        <h2>A {seats}-person challenge nearby</h2>
        {meta}
        <div className="btn-row">
          <button className="btn" onClick={() => onAccept(false)}>No</button>
          <button className="btn primary wide" onClick={() => onAccept(true)}>Yes</button>
        </div>
      </section>
    );
  }

  if (status.needs_accept) {
    return (
      <section className="card violet group">
        <CardHead eyebrow="Seats filled" tone="violet" title="Your group is full" />
        <Seats seats={seatList} />
        {meta}
        <div className="btn-row">
          <button className="btn" onClick={() => onAccept(false)}>Not now</button>
          <button className="btn primary wide" onClick={() => onAccept(true)}>Accept</button>
        </div>
      </section>
    );
  }

  if (m.state === 'confirmed') {
    return (
      <section className="card violet group">
        <CardHead eyebrow="Locked in" tone="green" title="Everyone's in!" />
        <Seats seats={seatList} mood="cheer" tight />
        {meta}
        <p className="line-ok"><Icon name="checkCircle" size={20} /><span>Everyone's in! Setting up your challenge…</span></p>
        <div className="progress"><div /></div>
      </section>
    );
  }

  if (m.state === 'playing') {
    return (
      <section className="card">
        <CardHead eyebrow="Challenge" title="Opening your challenge…" />
        <a className="btn primary" href={`?match=${m.match_id}&agent=${me}`}>Open challenge</a>
      </section>
    );
  }

  // The host decides: start with whoever accepted, or call it off.
  const accepted = m.accepted.length;
  if (status.is_host && accepted >= START_EARLY && accepted < seats) {
    return (
      <section className="card violet group">
        <CardHead
          eyebrow="Your call"
          tone="violet"
          title={`${accepted} of ${seats} joined.`}
          sub={`Start with ${accepted}, or keep waiting for the rest.`}
        />
        <Seats seats={seatList} />
        <button className="btn primary" onClick={onStart}>Start with {accepted}</button>
        <p className="muted small center">Seats still open are dropped.</p>
        <button className="btn" onClick={onCancel}>Call it off</button>
      </section>
    );
  }

  const others = m.members.filter(p => p !== me);
  const waitingOnOthers = m.accepted.includes(me) && full;
  return (
    <section className="card violet group">
      <div className="split-head">
        <span className="eyebrow violet">Your group</span>
        <b>{joined} of {seats} joined</b>
      </div>
      <Seats seats={seatList} />
      {waitingOnOthers ? (
        <>
          <div className="marks">
            {[me, ...others].map((p, i) => {
              const ok = m.accepted.includes(p);
              return (
                <span key={p} className={`mark${ok ? ' ok' : ''}`}>
                  <Icon name={ok ? 'check' : 'dots'} size={14} />{p === me ? 'You' : String.fromCharCode(64 + i)}
                </span>
              );
            })}
          </div>
          <div className="status"><Icon name="clock" /><span>Waiting for the others…</span></div>
        </>
      ) : (
        <p className="muted center">{m.state === 'inviting' ? 'Inviting the best fits nearby…' : 'Waiting for everyone to accept…'}</p>
      )}
      {status.is_host ? <button className="btn" onClick={onCancel}>Call it off</button> : <button className="link" onClick={onLeave}>Leave challenge</button>}
    </section>
  );
}

// Flow tab: your group as the Coordinator sees it. Every line runs to the walrus, never person to person.
function Flow({ match, me }: { match: Match | null; me: string }) {
  const note = !match
    ? ['No challenge yet.', 'Start one from Chat and watch the Coordinator invite people here.']
    : match.state === 'inviting'
      ? ['Invites are out.', 'The Coordinator sends an invite down each line. Nobody can see anyone else.']
      : match.state === 'pending'
        ? ['Seats filled.', 'Everyone still has to accept. First names appear once they all do.']
        : ['Everyone accepted.', 'Time to meet up and play.'];
  return (
    <>
      <section className="card violet group">
        {match ? <Seats seats={seatsFor(match, me)} mood={match.state === 'confirmed' ? 'cheer' : 'happy'} /> : <Seats seats={[{ state: 'you' }, { state: 'open' }, { state: 'open' }]} />}
        <p className="center"><b>{note[0]}</b> <span className="muted">{note[1]}</span></p>
        <div className="marks">
          <span className="mark ok"><Icon name="check" size={14} />in</span>
          <span className="mark"><Icon name="clock" size={14} />different time</span>
          <span className="mark" style={{ color: 'var(--grey)' }}><Icon name="dots" size={14} />can't make it</span>
        </div>
      </section>
      <div className="grow" />
      <a className="watch" href={`?view=log&agent=${encodeURIComponent(me)}`} target="_blank"><Icon name="live" size={18} />Watch your group live ↗</a>
    </>
  );
}

function Menu({ agentId, onClose, onSwitch }: { agentId: string; onClose: () => void; onSwitch: () => void }) {
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <section className="sheet" role="dialog" aria-label="Menu" onClick={e => e.stopPropagation()}>
        <header className="topbar">
          <h1>Privacy</h1>
          <button className="icon-btn" aria-label="Close" onClick={onClose}><Icon name="close" size={20} /></button>
        </header>
        <p className="muted">Here is how we keep you safe, in plain words.</p>
        <ol className="rules">
          {PRIVACY_RULES.map(([t, body], i) => (
            <li key={t}><span className="n">{i + 1}</span><div><b>{t}</b><span>{body}</span></div></li>
          ))}
        </ol>
        <InstallHint />
        <a className="btn" href={`?view=log&agent=${encodeURIComponent(agentId)}`} target="_blank"><Icon name="live" />Watch your group live ↗</a>
        <button className="btn" onClick={onSwitch}>Switch person</button>
      </section>
    </div>
  );
}
