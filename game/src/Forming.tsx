// Before the round starts: your group filling up, live. An agent from outside the app (Meta Muse, Claude, …) hands
// its person this link as soon as they're in a group, so they see who's in and tap Accept here themselves.
import { useEffect, useState } from 'react';
import { API } from './config.ts';
import { CardHead, Icon } from './ui.tsx';

type Match = { state: string; members: string[]; accepted: string[]; size?: number | null; area: string; window: string };

// "phong-2" (a second Phong) still reads as Phong.
function title(id: string) {
  const base = id.replace(/-\d+$/, '');
  return base.charAt(0).toUpperCase() + base.slice(1);
}

export default function Forming({ matchId, agentId, heading }: { matchId: string; agentId: string; heading: string }) {
  const [m, setM] = useState<Match | null>();
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    let live = true;
    const tick = () =>
      fetch(`${API}/matches/${matchId}`)
        .then(r => (r.ok ? r.json() : null))
        .then(v => live && setM(v))
        .catch(() => {});
    tick();
    const id = setInterval(tick, 2000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [matchId]);

  const answer = async (accept: boolean) => {
    setSending(true);
    setError('');
    try {
      const res = await fetch(`${API}/matches/${matchId}/accept`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ agent_id: agentId, accept }),
      });
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `${res.status} ${res.statusText}`);
      setM(await res.json());
    } catch (e) {
      setError(String((e as Error)?.message ?? e));
    } finally {
      setSending(false);
    }
  };

  const inGroup = !!m && m.state !== 'cancelled' && m.members.includes(agentId);
  return (
    <main className="app">
      <header className="topbar"><h1>{heading}</h1></header>
      {m === undefined ? (
        <section className="conn"><h2>Loading your group…</h2></section>
      ) : !m || !inGroup ? (
        <section className="card plain center">
          <CardHead eyebrow="Not this time" tone="muted" title="This group didn't happen." />
          <p className="muted">Your agent will keep looking.</p>
        </section>
      ) : (
        <GroupCard m={m} me={agentId} sending={sending} onAnswer={answer} />
      )}
      {error && <p className="error">{error}</p>}
    </main>
  );
}

function GroupCard({ m, me, sending, onAnswer }: { m: Match; me: string; sending: boolean; onAnswer: (yes: boolean) => void }) {
  const size = Math.max(m.size ?? m.members.length, m.members.length);
  // Others stay "Agent A, B…" until everyone has accepted, like everywhere else in the app.
  const named = ['confirmed', 'playing', 'done'].includes(m.state);
  const others = m.members.filter(a => a !== me);
  const toAnswer = !m.accepted.includes(me) && (m.state === 'inviting' || m.state === 'pending');
  const seat = (key: string, label: string, isIn: boolean, note: string) => (
    <span key={key} className={`mark big${isIn ? ' ok' : ''}`}>
      <Icon name={isIn ? 'check' : 'clock'} size={16} />
      {label}
      <small>· {note}</small>
    </span>
  );
  return (
    <section className="card">
      <CardHead eyebrow="Your group" title={`${m.accepted.length} of ${size} in`} sub={`${m.area} · ${m.window}`} />
      <div className="marks left">
        {seat(me, 'You', m.accepted.includes(me), m.accepted.includes(me) ? 'in' : 'your call')}
        {others.map((a, i) =>
          seat(a, named ? title(a) : `Agent ${String.fromCharCode(65 + i)}`, m.accepted.includes(a), m.accepted.includes(a) ? 'in' : 'deciding'),
        )}
        {Array.from({ length: size - m.members.length }, (_, i) => seat(`open-${i}`, 'Open seat', false, 'inviting'))}
      </div>
      {toAnswer ? (
        <div className="btn-row">
          <button className="btn" disabled={sending} onClick={() => onAnswer(false)}>Not now</button>
          <button className="btn primary wide" disabled={sending} onClick={() => onAnswer(true)}>Accept</button>
        </div>
      ) : (
        <div className="status big">
          <Icon name="clock" size={20} />
          <span>{named ? "Everyone's in! Setting up your challenge…" : "You're in. Waiting for the others…"}</span>
        </div>
      )}
    </section>
  );
}
