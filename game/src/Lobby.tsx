// Sign-in and the chat with your personal agent (agents service): /?agent=<agent_id>
// Your agent checks you in only after you tap Share, answers the coordinator's invitations for you, and you approve
// every step here.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useRef, useState } from 'react';
import { AGENTS, API } from './config.ts';
import InstallHint from './InstallHint.tsx';
const SIZES = [2, 3, 4, 5];
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
      {error ? <p className="error">{error} · <a href="/">back to sign-in</a></p> : <p className="muted">Signing you in…</p>}
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
      <h1>Meet your agent</h1>
      <InstallHint />
      <p className="muted">
        Your personal AI agent finds a small group nearby for a quick challenge. You approve every step, and nothing
        about you is shared until you say OK.
      </p>
      {auth && (
        <section className="card">
          <button className="primary" disabled={busy} onClick={() => { setBusy(true); goSignIn(auth.authority, auth.client_id).catch(e => { setBusy(false); setError(String(e.message)); }); }}>
            {busy ? 'Opening sign-in…' : 'Sign in'}
          </button>
          <p className="muted small">
            Secure sign-in by SpacetimeAuth. Krew keeps only your first name; your email and photo are never stored or
            shared.
          </p>
        </section>
      )}
      {auth === undefined && <p className="muted">Loading sign-in…</p>}
      {people.length > 0 && (
        <section className="card">
          <p className="label">Demo · who are you?</p>
          <div className="row wrap">
            {people.map(p => (
              <button key={p.id} className="secondary" disabled={busy} onClick={() => presenter(p.id)}>I'm {p.name}</button>
            ))}
          </div>
          <p className="muted small">Your agent already knows you, like a personal agent you've used for a while.</p>
        </section>
      )}
      <details className="card" open={auth === null}>
        <summary className="label">Just trying it out? Use the demo with a first name</summary>
        <form className="stack" onSubmit={demo}>
          <input value={name} onChange={e => setName(e.target.value)} placeholder="Linh" required maxLength={40} aria-label="Your first name" />
          <p className="muted small">Your agent starts with a sample profile, like one a real personal agent would already have.</p>
          <button className="secondary" type="submit" disabled={busy}>Start the demo</button>
        </form>
      </details>
      <a className="pill" href="?view=log" target="_blank">Watch the agents live ↗</a>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function Chat({ agentId }: { agentId: string }) {
  const [status, setStatus] = useState<Status>();
  const [draft, setDraft] = useState('');
  const [thinking, setThinking] = useState(false);
  const [error, setError] = useState('');
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let live = true;
    const tick = () =>
      call<Status>(AGENTS, `/users/${agentId}`)
        .then(s => {
          if (!live) return;
          setStatus(s);
          setError('');
          if (s.match?.state === 'playing') location.assign(`?match=${s.match.match_id}&agent=${agentId}`);
        })
        .catch(e => live && setError(String(e.message)));
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [agentId]);

  const lines = status?.transcript.length ?? 0;
  useEffect(() => end.current?.scrollIntoView({ block: 'end' }), [lines, thinking]);

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
        {error ? <p className="error">{error} · <button className="link" onClick={switchPerson}>sign in again</button></p> : <p className="muted">Waking up your agent…</p>}
      </main>
    );
  }

  const m = status.match;
  const seats = m ? m.size ?? m.members.length : 0;
  // Others stay anonymous (Person A, B, ...) until everyone has accepted; then the challenge reveals names.
  const named = !!m && ['confirmed', 'playing', 'done'].includes(m.state);
  const others = m ? m.members.filter(p => p !== agentId) : [];
  const who = (p: string) => (p === agentId ? 'You' : named ? title(p) : `Person ${String.fromCharCode(65 + others.indexOf(p))}`);

  return (
    <main className="app chat">
      <header>
        <h1>Your agent</h1>
        <span className="row">
          <a className="pill" href={`?view=log&agent=${encodeURIComponent(agentId)}`} target="_blank">watch live ↗</a>
          <button className="pill" onClick={switchPerson}>switch person</button>
        </span>
      </header>

      <InstallHint />

      <section className="transcript">
        {status.transcript.map((line, i) => (
          <p key={i} className={`bubble ${line.role}`}>{line.text}</p>
        ))}
        {thinking && <p className="bubble agent muted">…</p>}
        <div ref={end} />
      </section>

      {m && (
        <section className="card">
          <p className="label">
            {m.host === agentId ? 'Your' : 'A'} {seats}-person challenge · {m.window}
          </p>
          <ul className="board">
            {m.members.map(p => (
              <li key={p} className={m.accepted.includes(p) ? 'here' : ''}>
                <span>{who(p)}{p === m.host ? ' · host' : ''}</span>
                <span>{m.accepted.includes(p) ? '✓ in' : '…'}</span>
              </li>
            ))}
            {Array.from({ length: Math.max(0, seats - m.members.length) }, (_, i) => (
              <li key={`open-${i}`} className="open"><span>open seat</span><span>inviting…</span></li>
            ))}
          </ul>
          {status.needs_accept && (
            <div className="row">
              <button className="primary" onClick={() => run(call(AGENTS, `/users/${agentId}/accept`, { accept: true }))}>Accept</button>
              <button className="secondary" onClick={() => run(call(AGENTS, `/users/${agentId}/accept`, { accept: false }))}>Not now</button>
            </div>
          )}
          {status.is_host && ['inviting', 'pending'].includes(m.state) && (
            <div className="row">
              {m.accepted.length >= SIZES[0] && m.accepted.length < seats && (
                <button className="primary" onClick={() => run(call(API, `/challenges/${m.match_id}/start`, {}))}>
                  Start now with {m.accepted.length}
                </button>
              )}
              <button className="secondary" onClick={() => run(call(API, `/challenges/${m.match_id}/cancel`, {}))}>Call it off</button>
            </div>
          )}
          {m.state === 'confirmed' && <p className="muted">Everyone's in. Your agent is setting up the challenge…</p>}
          {/* The host's way out before the start is Call it off; an invitation still waiting has Not now. */}
          {!status.needs_accept && !(status.is_host && ['inviting', 'pending'].includes(m.state)) && (
            <button
              className="link"
              onClick={() => confirm('Leave this challenge?') && run(call(API, `/matches/${m.match_id}/leave`, { agent_id: agentId }))}
            >
              Leave challenge
            </button>
          )}
        </section>
      )}

      {!status.consented && (
        <section className="card key">
          <p className="label">Before I find you a group · OK to share?</p>
          <p>I'll share only this with the coordinator, so it can match you:</p>
          <ul className="shared">
            <li>🕑 Free {status.would_share.free}</li>
            <li>📍 {status.would_share.area}</li>
            <li>🧩 Good at: {status.would_share.skills.join(', ') || 'not told me yet'}</li>
            <li>✨ Into: {status.would_share.interests.join(', ') || 'not told me yet'}</li>
          </ul>
          <p className="muted small">No name, email or exact location. Everything else you tell me stays with me.</p>
          <div className="row">
            <button className="primary" onClick={() => run(call(AGENTS, `/users/${agentId}/consent`, { share: true }))}>Share</button>
            <button className="secondary" onClick={() => run(call(AGENTS, `/users/${agentId}/consent`, { share: false }))}>Not now</button>
          </div>
        </section>
      )}

      {!m && status.last_match && (
        <section className="card">
          <p className="label">Your last challenge</p>
          <a className="pill" href={`?match=${status.last_match}&agent=${agentId}`}>How was it? Rate it and see who'd meet again →</a>
        </section>
      )}

      {!m && status.consented && (
        <section className="card">
          <p className="label">Start a challenge · how many people?</p>
          <div className="row">
            {SIZES.map(n => (
              <button key={n} className="primary" onClick={() => run(call(AGENTS, `/users/${agentId}/start`, { size: n }))}>{n}</button>
            ))}
          </div>
        </section>
      )}

      <form className="composer" onSubmit={send}>
        <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Message your agent…" maxLength={2000} />
        <button className="primary" type="submit" disabled={thinking || !draft.trim()}>Send</button>
      </form>
      {error && <p className="error">{error}</p>}
    </main>
  );
}
