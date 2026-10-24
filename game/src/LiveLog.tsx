// Negotiation screen: /?view=log
// Draws the coordinator's live event stream (ws /ws/log): agents around the coordinator, questions going
// out, answers coming back, groups forming and checking in. Open it in a third window while two people play.
import { useEffect, useRef, useState } from 'react';
import { API } from './config.ts';

type LogEvent = { seq: number; ts: number; kind: string; text: string; data: Record<string, any> };
type Status = 'pool' | 'asked' | 'yes' | 'no' | 'grouped' | 'accepted' | 'here' | 'done';
type Group = { members: string[]; state: 'proposed' | 'confirmed' | 'playing' | 'done' | 'cancelled' };
type Pulse = { id: number; agent: string; out: boolean };
type Board = { agents: Record<string, Status>; groups: Record<string, Group>; considering: string[]; pickedBy: string; reason: string };

const EMPTY: Board = { agents: {}, groups: {}, considering: [], pickedBy: '', reason: '' };
const STATUS_LABEL: Record<Status, string> = {
  pool: 'in the pool', asked: 'asked', yes: 'said yes', no: 'said no', grouped: 'in a group',
  accepted: 'accepted', here: "here ✓", done: 'done 🎉',
};
const SIZE = 440, MID = SIZE / 2, RING = 160;

// Back to the chat of the person who opened this screen: their agent comes in the link (each window is its own
// person), else the one this browser remembers (the lobby's key). Without either, back to sign-in.
function backHref() {
  let agent = new URLSearchParams(location.search).get('agent');
  try {
    agent ??= localStorage.getItem('threekey-agent');
  } catch {
    // private mode
  }
  return agent ? `/?agent=${encodeURIComponent(agent)}` : '/';
}

function title(id: string) {
  return id.charAt(0).toUpperCase() + id.slice(1);
}

// One event in, the next board out. Pulses are the agents this event should animate (out = coordinator → agent).
function apply(b: Board, e: LogEvent): [Board, Pulse[]] {
  const d = e.data ?? {};
  const set = (ids: string[], s: Status) => {
    const agents = { ...b.agents };
    for (const id of ids) agents[id] = s;
    return agents;
  };
  if (e.text === 'demo reset') return [EMPTY, []];
  if (e.kind === 'coordinator' && d.agent_id) return [{ ...b, agents: { ...b.agents, [d.agent_id]: 'pool' } }, [{ id: e.seq, agent: d.agent_id, out: false }]];
  // Host-led challenges: the host's group grows as invitees say yes, and shrinks when one backs out.
  if (d.host && d.match_id) {
    const g: Group = b.groups[d.match_id] ?? { members: [d.host], state: 'proposed' };
    const withMembers = (members: string[], agents: Board['agents']) => ({ ...b, agents, groups: { ...b.groups, [d.match_id]: { ...g, members } } });
    if (d.invited) {
      return [{ ...withMembers(g.members, set(d.invited, 'asked')), considering: d.invited }, d.invited.map((m: string, i: number) => ({ id: e.seq * 10 + i, agent: m, out: true }))];
    }
    if (d.joined) return [withMembers([...g.members, d.joined], { ...b.agents, [d.joined]: 'grouped' }), [{ id: e.seq, agent: d.joined, out: false }]];
    if (d.left) return [withMembers(g.members.filter(m => m !== d.left), { ...b.agents, [d.left]: 'pool' }), []];
    return b.groups[d.match_id] ? [b, []] : [withMembers(g.members, { ...b.agents, [d.host]: 'grouped' }), []];
  }
  if (e.kind === 'match' && d.members) {
    return [{ ...b, agents: set(d.members, 'asked'), considering: d.members }, d.members.map((m: string, i: number) => ({ id: e.seq * 10 + i, agent: m, out: true }))];
  }
  if (e.kind === 'match' && d.picked_by) return [{ ...b, pickedBy: d.picked_by, reason: d.reason ?? '' }, []];
  if (e.kind === 'broker' && d.agent_id) {
    const s = d.answer === 'yes' ? 'yes' : 'no';
    return [{ ...b, agents: { ...b.agents, [d.agent_id]: s } }, [{ id: e.seq, agent: d.agent_id, out: false }]];
  }
  if (e.kind === 'broker') return [{ ...b, agents: set(b.considering.filter(m => b.agents[m] !== 'no'), 'pool'), considering: [] }, []];
  if (e.kind === 'match' && d.match_id) {
    const members = b.considering;
    return [{ ...b, agents: set(members, 'grouped'), groups: { ...b.groups, [d.match_id]: { members, state: 'proposed' } }, considering: [] }, []];
  }
  const g = d.match_id ? b.groups[d.match_id] : undefined;
  if (!g) return [b, []];
  const withGroup = (patch: Partial<Group>, agents = b.agents) => ({ ...b, agents, groups: { ...b.groups, [d.match_id]: { ...g, ...patch } } });
  if (e.kind === 'approval' && d.accepted) {
    return [withGroup(e.text.includes('CONFIRMED') ? { state: 'confirmed' } : {}, set(d.accepted, 'accepted')), d.accepted.map((m: string, i: number) => ({ id: e.seq * 10 + i, agent: m, out: false }))];
  }
  if (e.kind === 'approval') return [withGroup({ state: 'cancelled' }, set(g.members, 'pool')), []];
  if (e.kind === 'challenge') return [b, g.members.map((m, i) => ({ id: e.seq * 10 + i, agent: m, out: true }))];
  if (e.kind === 'game' && d.agent_id) return [withGroup({}, { ...b.agents, [d.agent_id]: 'here' }), [{ id: e.seq, agent: d.agent_id, out: false }]];
  if (e.kind === 'game' && e.text.includes('playing')) return [withGroup({ state: 'playing' }), []];
  if (e.kind === 'game' && e.text.includes('complete')) return [withGroup({ state: 'done' }, set(g.members, 'done')), []];
  return [b, []];
}

export default function LiveLog() {
  const [board, setBoard] = useState<Board>(EMPTY);
  const [feed, setFeed] = useState<LogEvent[]>([]);
  const [pulses, setPulses] = useState<Pulse[]>([]);
  const [online, setOnline] = useState(false);
  const feedEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let ws: WebSocket, retry: number, stopped = false;
    const connect = () => {
      ws = new WebSocket(API.replace(/^http/, 'ws') + '/ws/log');
      ws.onopen = () => {
        setOnline(true);
        setBoard(EMPTY); // the server replays its history on every connect
        setFeed([]);
      };
      ws.onmessage = msg => {
        const e: LogEvent = JSON.parse(msg.data);
        let fresh: Pulse[] = [];
        setBoard(b => {
          const [next, p] = apply(b, e);
          fresh = p;
          return next;
        });
        setFeed(f => (e.text === 'demo reset' ? [e] : [...f, e].slice(-200)));
        // Only animate live events, not the history replay.
        if (fresh.length && Date.now() / 1000 - e.ts < 3) {
          setPulses(p => [...p, ...fresh]);
          setTimeout(() => setPulses(p => p.filter(x => !fresh.includes(x))), 1200);
        }
      };
      ws.onclose = () => {
        setOnline(false);
        if (!stopped) retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      stopped = true;
      clearTimeout(retry);
      // Detach first: a late close/message from this socket must not touch the next mount's state.
      ws.onopen = ws.onmessage = ws.onclose = null;
      ws.close();
    };
  }, []);

  useEffect(() => {
    feedEnd.current?.scrollIntoView({ block: 'end' });
  }, [feed]);

  const ids = Object.keys(board.agents);
  const pos = (id: string) => {
    const i = ids.indexOf(id), a = (i / Math.max(ids.length, 1)) * 2 * Math.PI - Math.PI / 2;
    return { x: MID + RING * Math.cos(a), y: MID + RING * Math.sin(a) };
  };

  return (
    <main className="app wide">
      <header>
        <h1>Agents, live</h1>
        <span className="row">
          <a className="pill" href={backHref()}>← back</a>
          <span className={`pill ${online ? 'live' : ''}`}>{online ? '● live' : 'reconnecting…'}</span>
        </span>
      </header>

      <div className="split">
        <section className="card">
          <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="net" role="img" aria-label="Agents around the coordinator">
            {ids.map(id => {
              const p = pos(id);
              return <line key={id} x1={MID} y1={MID} x2={p.x} y2={p.y} className="spoke" />;
            })}
            {Object.entries(board.groups).filter(([, g]) => g.state !== 'cancelled').map(([mid, g]) =>
              g.members.slice(1).map((m, i) => {
                const a = pos(g.members[i]), c = pos(m);
                return <line key={mid + m} x1={a.x} y1={a.y} x2={c.x} y2={c.y} className={`bond ${g.state}`} />;
              })
            )}
            {pulses.map(p => {
              const a = pos(p.agent);
              const path = p.out ? `M${MID},${MID} L${a.x},${a.y}` : `M${a.x},${a.y} L${MID},${MID}`;
              return (
                <circle key={p.id} r="6" className={p.out ? 'pulse out' : 'pulse'}>
                  <animateMotion dur="0.9s" path={path} fill="freeze" />
                </circle>
              );
            })}
            <g className="hub">
              <circle cx={MID} cy={MID} r="46" />
              <text x={MID} y={MID - 4}>Coordinator</text>
              <text x={MID} y={MID + 14} className="sub">{board.pickedBy ? `picked by ${board.pickedBy}` : 'waiting'}</text>
            </g>
            {ids.map(id => {
              const p = pos(id), s = board.agents[id];
              return (
                <g key={id} className={`node ${s}`}>
                  <circle cx={p.x} cy={p.y} r="24" />
                  <text x={p.x} y={p.y + 5}>{title(id).slice(0, 2)}</text>
                  <text x={p.x} y={p.y + 42} className="name">{title(id)}</text>
                  <text x={p.x} y={p.y + 56} className="sub">{STATUS_LABEL[s]}</text>
                </g>
              );
            })}
          </svg>
          {ids.length === 0 && <p className="muted">No one checked in yet. Log in from another window.</p>}
          {board.reason && <p className="muted"><b>Why this group:</b> {board.reason}</p>}
        </section>

        <section className="card feed">
          <p className="label">Negotiation log</p>
          {feed.map(e => (
            <p key={e.seq} className="event">
              <span className={`tag ${e.kind}`}>{e.kind}</span> {e.text}
            </p>
          ))}
          <div ref={feedEnd} />
        </section>
      </div>
    </main>
  );
}
