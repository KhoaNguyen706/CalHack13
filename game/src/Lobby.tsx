// Sign-in and the chat with your personal agent (agents service): /?agent=<agent_id>
// Your agent checks you in only after you tap Share, answers the coordinator's invitations for you, and you approve
// every step here.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useRef, useState } from 'react';
import { AGENTS, API, openedKey } from './config.ts';
import InstallHint from './InstallHint.tsx';
import { buzz, CardHead, Icon, MeetCard, Pebble, PieceIcon, PRIVACY_RULES, Reconnecting, Scribble, Seats, SignScreen, Walrus, type Meet, type PebbleMood, type Seat, type WalrusMood } from './ui.tsx';

// Groups are 2 to 5 (2 for a quick round with one other person). The host can start early once this many accepted;
// the coordinator's MIN_GROUP_SIZE has the final say on both.
const SIZES = [2, 3, 4, 5];
const START_EARLY = 2;
const KEY = 'threekey-agent';

type Line = { role: 'agent' | 'user' | 'update'; text: string };
type Match = {
  match_id: string; members: string[]; accepted: string[]; state: string; window: string; area: string;
  host?: string | null; size?: number | null; invited?: string[]; declined?: string[];
};
type Shared = { free: string; area: string; interests: string[]; skills: string[] };
type Status = {
  agent_id: string; name: string; transcript: Line[]; match: Match | null; is_host: boolean; needs_accept: boolean;
  my_part: string | null; challenge: { title?: string; goal?: string; meet?: Meet | null } | null; consented: boolean; would_share: Shared;
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

// "phong-2" (a second Phong) still reads as Phong: the suffix only keeps ids unique.
function title(id: string) {
  const base = id.replace(/-\d+$/, '');
  return base.charAt(0).toUpperCase() + base.slice(1);
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

// The last answer from your agent, kept for this tab: coming back from the game (or reloading) shows the chat as it
// was straight away instead of a loading screen, and the next poll brings it up to date.
const CACHE = (agent: string) => `krew-status:${agent}`;

function cached(agent: string): Status | undefined {
  try {
    return JSON.parse(sessionStorage.getItem(CACHE(agent)) ?? 'null') ?? undefined;
  } catch {
    return undefined;
  }
}

// How Pebble feels right now, from where you are in the flow (the face in the top bar).
function moodOf(s: Status, typing: boolean, lost: boolean): PebbleMood {
  if (lost) return 'sleepy';
  if (typing) return 'thinking';
  if (!s.consented) return 'shy';
  if (s.needs_accept) return 'excited';
  if (s.match && ['confirmed', 'playing'].includes(s.match.state)) return 'cheer';
  return s.match ? 'thinking' : 'happy';
}

const TOGETHER = ['confirmed', 'playing', 'done'];

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
        <div className="avatar xl"><Pebble mood={error ? 'sad' : 'thinking'} size={164} /></div>
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
        <div className="avatar xl"><Pebble mood="wave" size={164} /></div>
        <h1>Meet <span className="doodle">Pebble<i className="squiggle" /></span>, your agent</h1>
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
  // The Coordinator's order (everyone it invited, then anyone seated without an invite), as on the live screen:
  // the first of them is Agent A everywhere, with the same animal.
  const order = [...new Set([...(m.invited ?? []), ...m.members])].filter(p => p !== me);
  const others: Seat[] = m.members
    .filter(p => p !== me)
    .map(p => ({ state: accepted(p) ? 'acc' : 'in', name: named ? title(p) : undefined, n: order.indexOf(p) }));
  // Invites still out sit on the empty seats, so "asking…" carries the letter the live screen gives them.
  const asking = order.flatMap((p, k) => (m.members.includes(p) || m.declined?.includes(p) ? [] : [k]));
  const open: Seat[] = Array.from({ length: Math.max(0, size - m.members.length) }, (_, j) =>
    m.state === 'inviting' ? { state: 'think', n: asking[j] } : { state: 'open' },
  );
  return [you, ...others, ...open];
}

function Chat({ agentId }: { agentId: string }) {
  const [status, setStatus] = useState<Status | undefined>(() => cached(agentId));
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
  const seen = useRef<number | null>(null); // lines already on screen: only newer ones animate in
  const shownTab = useRef('');

  useEffect(() => {
    let live = true, last = '';
    const tick = () =>
      call<Status>(AGENTS, `/users/${agentId}`)
        .then(s => {
          if (!live) return;
          // Most polls bring nothing new; skipping those keeps animations from being interrupted by re-renders.
          const text = JSON.stringify(s);
          if (text !== last) {
            last = text;
            setStatus(s);
            try {
              sessionStorage.setItem(CACHE(agentId), text);
            } catch {
              // private mode: no instant chat next time, that's all
            }
          }
          setLost(false);
          // Open the challenge on its own only the first time; after that "<" from the game must stay here.
          if (s.match?.state === 'playing' && !remembered(openedKey(s.match.match_id))) {
            location.assign(`?match=${s.match.match_id}&agent=${agentId}`);
          }
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
  const settled = seen.current ?? lines;
  useEffect(() => {
    if (status) seen.current = status.transcript.length;
  });
  // Opening the chat lands on the newest line at once; after that, new lines glide into view.
  // Braces matter: newer browsers return a Promise from scrollIntoView, and React would call it as the cleanup.
  useEffect(() => {
    const first = shownTab.current !== tab;
    shownTab.current = tab;
    end.current?.scrollIntoView({ block: 'end', behavior: first ? 'instant' : 'smooth' });
  }, [lines, thinking, tab]);

  // The card under the chat grows and shrinks (an invite, Accept, the keyboard): keep the newest line in view.
  const chat = !!status && tab === 'chat';
  useEffect(() => {
    const box = end.current?.parentElement;
    if (!chat || !box) return;
    const pin = new ResizeObserver(() => end.current?.scrollIntoView({ block: 'end' }));
    pin.observe(box);
    return () => pin.disconnect();
  }, [chat]);

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
            <div className="avatar xl"><Pebble mood="sleepy" size={164} /></div>
            <h2>Waking up your agent…</h2>
          </section>
        )}
      </main>
    );
  }

  const m = status.match;
  const accept = (yes: boolean) => {
    buzz(yes ? [10, 40, 18] : 8);
    run(call(AGENTS, `/users/${agentId}/accept`, { accept: yes }));
  };
  const leave = () => m && confirm('Leave this challenge?') && run(call(API, `/matches/${m.match_id}/leave`, { agent_id: agentId }));

  return (
    <main className="app chat">
      <header className="topbar">
        <div className="avatar"><Pebble mood={moodOf(status, thinking, lost || !online)} size={50} /></div>
        <div className="who"><b>Pebble</b><span>Your agent{status.name ? ` · ${status.name}` : ''}</span></div>
        <button className="icon-btn" aria-label="Menu" aria-expanded={menu} onClick={() => setMenu(true)}><Icon name="menu" size={22} /></button>
      </header>

      <div className="tabs" role="tablist" aria-label="Views" data-tab={tab}>
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
            {status.transcript.map((line, i) => {
              // Like any messenger: Pebble's face sits by the last of its bubbles in a row. Updates are Pebble too,
              // passing on news (from the Coordinator or about your own taps), so they share the face.
              const lastOfRun = (status.transcript[i + 1]?.role ?? 'user') === 'user';
              return (
                <div key={i} className={`msg ${line.role}${i < settled ? ' settled' : ''}`}>
                  {line.role !== 'user' && (
                    <span className="mini" aria-hidden="true">{lastOfRun && !(thinking && i === lines - 1) && <Pebble decorative size={30} />}</span>
                  )}
                  <p className={`bubble ${line.role}`}>{line.text}</p>
                </div>
              );
            })}
            {thinking && (
              <div className="msg agent">
                <span className="mini" aria-hidden="true"><Pebble decorative mood="thinking" size={30} /></span>
                <p className="bubble typing" aria-label="Pebble is typing"><i /><i /><i /></p>
              </div>
            )}
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
                    <button key={n} aria-pressed={size === n} onClick={() => { buzz(6); setSize(n); }}><b>{n}</b><span>people</span>{size === n && <Scribble />}</button>
                  ))}
                </div>
                <button
                  className="btn primary"
                  aria-disabled={!size}
                  onClick={() => {
                    if (!size) return;
                    buzz(12);
                    run(call(AGENTS, `/users/${agentId}/start`, { size }));
                  }}
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
      <div className="speaker">
        <div className="avatar lg"><Pebble mood="shy" size={84} /></div>
        <CardHead eyebrow="Before we start" title="OK to share?" />
      </div>
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
  const [sign, setSign] = useState(false);
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
        <Seats seats={seatList} mood="happy" />
        {meta}
        <div className="btn-row">
          <button className="btn" onClick={() => onAccept(false)}>Not now</button>
          <button className="btn primary wide" onClick={() => onAccept(true)}>Accept</button>
        </div>
      </section>
    );
  }

  // Everyone's in and the challenge is ready: first, where to meet and how to spot each other.
  if (m.state === 'confirmed' && status.challenge?.meet) {
    return (
      <>
        <p className="line-ok"><Icon name="checkCircle" size={20} /><span>Everyone's in! Go meet your group.</span></p>
        <MeetCard meet={status.challenge.meet} onShow={() => setSign(true)} />
        {sign && <SignScreen meet={status.challenge.meet} onClose={() => setSign(false)} />}
      </>
    );
  }

  if (m.state === 'confirmed') {
    return (
      <section className="card violet group">
        <CardHead eyebrow="Locked in" tone="green" title="It fits! Everyone's in." />
        <Seats seats={seatList} mood="cheer" snap />
        {meta}
        <p className="line-ok"><Icon name="checkCircle" size={20} /><span>Everyone's in! Setting up your challenge…</span></p>
        <div className="progress"><div /></div>
      </section>
    );
  }

  if (m.state === 'playing') {
    return (
      <section className="card">
        <CardHead eyebrow="Challenge" title={remembered(openedKey(m.match_id)) ? 'Your challenge is on' : 'Opening your challenge…'} />
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
        <Seats seats={seatList} mood="thinking" />
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
      <Seats seats={seatList} mood={m.state === 'inviting' ? 'thinking' : 'happy'} />
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
  const together = !!match && TOGETHER.includes(match.state);
  const walrus: WalrusMood = !match ? 'happy' : match.state === 'inviting' ? 'thinking' : together ? 'cheer' : 'happy';
  return (
    <>
      <section className="card violet group">
        {match ? <Seats seats={seatsFor(match, me)} mood={walrus} snap={together} /> : <Seats seats={[{ state: 'you' }, { state: 'open' }, { state: 'open' }]} />}
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
