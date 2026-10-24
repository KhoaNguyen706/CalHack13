// Sign-in and the chat with your personal agent (agents service): /?agent=<agent_id>
// Your agent checks you in, answers the coordinator's invitations for you, and you approve every step here.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useRef, useState } from 'react';

// Same address as the page (Vite proxies these), unless a deploy points elsewhere.
const AGENTS = import.meta.env.VITE_AGENTS_URL || `${location.origin}/agents-api`;
const API = import.meta.env.VITE_COORDINATOR_URL || `${location.origin}/api`;
const SIZES = [2, 3, 4, 5];
const KEY = 'threekey-agent';

type Line = { role: 'agent' | 'user'; text: string };
type Match = {
  match_id: string; members: string[]; accepted: string[]; state: string; window: string; area: string;
  host?: string | null; size?: number | null;
};
type Status = {
  agent_id: string; name: string; transcript: Line[]; match: Match | null; is_host: boolean; needs_accept: boolean;
  my_part: string | null; challenge: { title?: string; goal?: string } | null;
};

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

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    // A fresh identity per sign-in, so a second window becomes a second person.
    const identity = crypto.randomUUID();
    call<Status>(AGENTS, '/users/login', { identity, name: name.trim() })
      .then(s => {
        remember(KEY, s.agent_id);
        location.assign(`?agent=${s.agent_id}`);
      })
      .catch(e => setError(String(e.message)))
      .finally(() => setBusy(false));
  };

  return (
    <main className="app">
      <h1>Meet your agent</h1>
      <p className="muted">
        Your personal AI agent finds a small group nearby for a quick challenge. It only shares your rough free time
        and interests, and you approve every step.
      </p>
      <form className="card" onSubmit={submit}>
        <label className="label">Your first name<input value={name} onChange={e => setName(e.target.value)} placeholder="Linh" required maxLength={40} /></label>
        <button className="primary" type="submit" disabled={busy}>{busy ? 'Waking up your agent…' : 'Start'}</button>
      </form>
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
        </section>
      )}

      {!m && (
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
