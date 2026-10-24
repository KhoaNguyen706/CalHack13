// Sign-in and the chat with your personal agent (agents service): /?agent=<agent_id>
// Your agent checks you in only after you tap Share, answers the coordinator's invitations for you, and you approve
// every step here.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useRef, useState } from 'react';
import { AGENTS, API } from './config.ts';
import InstallHint from './InstallHint.tsx';
const SIZES = [2, 3, 4, 5];
const KEY = 'threekey-agent';

type Line = { role: 'agent' | 'user'; text: string };
type Match = {
  match_id: string; members: string[]; accepted: string[]; state: string; window: string; area: string;
  host?: string | null; size?: number | null;
};
type Shared = { free: string; area: string; interests: string[]; skills: string[] };
type Status = {
  agent_id: string; name: string; transcript: Line[]; match: Match | null; is_host: boolean; needs_accept: boolean;
  my_part: string | null; challenge: { title?: string; goal?: string } | null; consented: boolean; would_share: Shared;
};

// Google Identity Services, loaded only on the sign-in page.
type GoogleId = {
  initialize: (o: { client_id: string; callback: (r: { credential: string }) => void }) => void;
  renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
};
declare global {
  interface Window {
    google?: { accounts: { id: GoogleId } };
  }
}

function loadGoogle(): Promise<GoogleId> {
  return new Promise((resolve, reject) => {
    if (window.google) return resolve(window.google.accounts.id);
    const script = document.createElement('script');
    script.src = 'https://accounts.google.com/gsi/client';
    script.async = true;
    script.onload = () => (window.google ? resolve(window.google.accounts.id) : reject(new Error('Google sign-in did not load')));
    script.onerror = () => reject(new Error('Google sign-in did not load'));
    document.head.appendChild(script);
  });
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

const AGENT = new URLSearchParams(location.search).get('agent') ?? remembered(KEY) ?? '';

export default function Lobby() {
  return AGENT ? <Chat agentId={AGENT} /> : <Login />;
}

function Login() {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [googleReady, setGoogleReady] = useState<boolean | null>(null); // null = still checking
  const button = useRef<HTMLDivElement>(null);

  const enter = (s: Status) => {
    remember(KEY, s.agent_id);
    location.assign(`?agent=${s.agent_id}`);
  };

  // Sign in with Google when the agents service has a client id; otherwise only the demo sign-in shows.
  useEffect(() => {
    call<{ google_client_id: string | null }>(AGENTS, '/auth/config')
      .then(async ({ google_client_id }) => {
        if (!google_client_id) return setGoogleReady(false);
        const id = await loadGoogle();
        id.initialize({
          client_id: google_client_id,
          callback: ({ credential }) => {
            setBusy(true);
            call<Status>(AGENTS, '/users/google', { credential }).then(enter).catch(e => setError(String(e.message))).finally(() => setBusy(false));
          },
        });
        if (button.current) id.renderButton(button.current, { theme: 'filled_blue', size: 'large', shape: 'pill', text: 'continue_with', width: 280 });
        setGoogleReady(true);
      })
      .catch(() => setGoogleReady(false));
  }, []);

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
      <section className="card">
        <div ref={button} className="google-button" />
        {googleReady === null && <p className="muted">Loading sign-in…</p>}
        {googleReady && (
          <p className="muted small">
            Krew keeps only your first name. Your email and photo from Google are never stored or shared.
          </p>
        )}
        {busy && <p className="muted">Waking up your agent…</p>}
      </section>
      <details className="card" open={googleReady === false}>
        <summary className="label">No Google account? Try the demo with a first name</summary>
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

  return (
    <main className="app chat">
      <header>
        <h1>Your agent</h1>
        <span className="row">
          <a className="pill" href="?view=log" target="_blank">watch live ↗</a>
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
            {m.host ? `${m.host === agentId ? 'Your' : `${title(m.host)}'s`} ${seats}-person challenge` : 'Your group'} · {m.window}
          </p>
          <ul className="board">
            {m.members.map(p => (
              <li key={p} className={m.accepted.includes(p) ? 'here' : ''}>
                <span>{title(p)}{p === agentId ? ' (you)' : ''}</span>
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
