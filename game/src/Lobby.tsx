// Log-in and lobby for one person: /?agent=<agent_id>
// Checks in with the coordinator, answers the plan question, accepts the group, then opens the game page.
// Each window keeps its own agent in the URL, so two windows on one laptop are two different people.
import { useEffect, useState } from 'react';

const API = import.meta.env.VITE_COORDINATOR_URL ?? 'http://localhost:8000';
const AGENT = new URLSearchParams(location.search).get('agent') ?? '';

type Agent = { agent_id: string; free: string; skills: string[]; area: string; match_id: string | null };
type Question = { question_id: string; question: string };
type Match = { match_id: string; members: string[]; roles: Record<string, string>; window: string; area: string; state: string; accepted: string[] };

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(API + path, body === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
  return res.json();
}

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

export default function Lobby() {
  return AGENT ? <Home /> : <Login />;
}

function Login() {
  const [name, setName] = useState('');
  const [skill, setSkill] = useState('riddles');
  const [free, setFree] = useState('Sat 14:00-16:00');
  const [area, setArea] = useState('UC Berkeley');
  const [error, setError] = useState('');

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const agent_id = name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-').slice(0, 32);
    api('/agents/checkin', { agent_id, free, area, skills: [skill], interests: ['hackathons'] })
      .then(() => location.assign(`?agent=${agent_id}`))
      .catch(e => setError(String(e.message)));
  };

  return (
    <main className="app">
      <h1>Log in</h1>
      <form className="card" onSubmit={submit}>
        <label className="label">Your name<input value={name} onChange={e => setName(e.target.value)} placeholder="linh" required /></label>
        <label className="label">You're good at
          <select value={skill} onChange={e => setSkill(e.target.value)}>
            <option value="riddles">Riddles</option>
            <option value="map">Finding the way</option>
            <option value="camera">Photos</option>
          </select>
        </label>
        <label className="label">Free<input value={free} onChange={e => setFree(e.target.value)} placeholder="Sat 14:00-16:00" required /></label>
        <label className="label">Area<input value={area} onChange={e => setArea(e.target.value)} required /></label>
        <button className="primary" type="submit">Check in</button>
      </form>
      <p className="muted">Testing on one laptop: log in as a different person in each window, with overlapping free time.</p>
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function Home() {
  const [agent, setAgent] = useState<Agent>();
  const [questions, setQuestions] = useState<Question[]>([]);
  const [match, setMatch] = useState<Match>();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let live = true;
    const tick = async () => {
      try {
        const a = await api<Agent>(`/agents/${AGENT}`);
        const [q, m] = await Promise.all([
          api<Question[]>(`/agents/${AGENT}/questions`),
          a.match_id ? api<Match>(`/matches/${a.match_id}`) : Promise.resolve(undefined),
        ]);
        if (!live) return;
        setAgent(a);
        setQuestions(q);
        setMatch(m);
        if (m?.state === 'playing') location.assign(`?match=${m.match_id}&agent=${AGENT}`);
      } catch (e) {
        if (live) setError(String((e as Error).message));
      }
    };
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  const run = (p: Promise<unknown>) => {
    setError('');
    p.catch(e => setError(String(e.message)));
  };

  const findGroup = () => {
    setBusy(true);
    setNote('');
    run(
      api<Match[]>('/matching/run', {})
        .then(made => setNote(made.length ? '' : 'No group yet. Is someone else logged in with overlapping free time?'))
        .finally(() => setBusy(false))
    );
  };

  if (!agent) {
    return (
      <main className="app">
        {error ? <p className="error">{error} · <a href="?">log in again</a></p> : <p className="muted">Loading…</p>}
      </main>
    );
  }

  return (
    <main className="app">
      <header>
        <h1>Hi {title(AGENT)}</h1>
        <a className="pill" href="?">switch person</a>
      </header>
      <p className="muted">{agent.area} · {agent.free} · {agent.skills.join(', ')}</p>

      {questions.map(q => (
        <section className="card key" key={q.question_id}>
          <p className="label">Your agent asks</p>
          <p>{q.question}</p>
          <div className="row">
            <button className="primary" onClick={() => run(api(`/agents/${AGENT}/reply`, { question_id: q.question_id, answer: 'yes' }))}>Yes</button>
            <button className="secondary" onClick={() => run(api(`/agents/${AGENT}/reply`, { question_id: q.question_id, answer: 'no' }))}>No</button>
          </div>
        </section>
      ))}

      {match ? (
        <section className="card">
          <p className="label">Your group · {match.area} · {match.window}</p>
          <ul className="board">
            {match.members.map(m => (
              <li key={m} className={match.accepted.includes(m) ? 'here' : ''}>
                <span>{title(m)}{m === AGENT ? ' (you)' : ''} · {match.roles[m]}</span>
                <span>{match.accepted.includes(m) ? '✓ in' : '…'}</span>
              </li>
            ))}
          </ul>
          {match.state === 'pending' && !match.accepted.includes(AGENT) && (
            <div className="row">
              <button className="primary" onClick={() => run(api(`/matches/${match.match_id}/accept`, { agent_id: AGENT }))}>Accept</button>
              <button className="secondary" onClick={() => run(api(`/matches/${match.match_id}/accept`, { agent_id: AGENT, accept: false }))}>Decline</button>
            </div>
          )}
          {match.state === 'pending' && match.accepted.includes(AGENT) && <p className="muted">Waiting for the others to accept…</p>}
          {match.state === 'confirmed' && <p className="muted">Everyone's in. Setting up your round…</p>}
        </section>
      ) : (
        questions.length === 0 && (
          <button className="primary" disabled={busy} onClick={findGroup}>
            {busy ? 'Asking the other agents…' : 'Find my group'}
          </button>
        )
      )}

      {note && <p className="muted">{note}</p>}
      {error && <p className="error">{error}</p>}
    </main>
  );
}
