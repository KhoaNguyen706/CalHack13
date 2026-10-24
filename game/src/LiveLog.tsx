// Negotiation screen. Two modes:
// - /?view=log&agent=<id> (from your chat): only your agent, the coordinator, and the agents considered for your
//   challenge, from the coordinator's per-person feed. Others are "Agent A, B…" until everyone accepts; anyone who
//   says no or leaves drops off, so the picture ends up being your group.
// - /?view=log (a presenter screen): everyone, from the full event stream (ws /ws/log).
import { useEffect, useRef, useState } from 'react';
import { API } from './config.ts';
import { Icon } from './ui.tsx';

type LogEvent = { seq: number; ts: number; kind: string; text: string; data: Record<string, any> };
type Status = 'pool' | 'asked' | 'yes' | 'no' | 'grouped' | 'accepted' | 'here' | 'done';
type Group = { members: string[]; state: 'proposed' | 'confirmed' | 'playing' | 'done' | 'cancelled' };
type Pulse = { id: number; agent: string; out: boolean };
type Board = { agents: Record<string, Status>; groups: Record<string, Group>; considering: string[]; pickedBy: string; reason: string };
type Feed = { match_id: string | null; labels: Record<string, string>; events: LogEvent[] };

const ME = new URLSearchParams(location.search).get('agent');
const GONE_AFTER_S = 6; // a "no" stays grey this long, then drops off

const EMPTY: Board = { agents: {}, groups: {}, considering: [], pickedBy: '', reason: '' };
const STATUS_LABEL: Record<Status, string> = {
  pool: 'in the pool', asked: 'asked', yes: 'said yes', no: 'said no', grouped: 'in a group',
  accepted: 'accepted', here: "here ✓", done: 'done 🎉',
};
// Room for ~20 agents on the ring before their names collide.
const SIZE = 600, MID = SIZE / 2, RING = 236;

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

// "phong-2" (a second Phong) still reads as Phong: the suffix only keeps ids unique.
function title(id: string) {
  const base = id.replace(/-\d+$/, '');
  return base.charAt(0).toUpperCase() + base.slice(1);
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
  const [labels, setLabels] = useState<Record<string, string>>({});
  const feedBox = useRef<HTMLElement>(null);

  const animate = (fresh: Pulse[]) => {
    if (!fresh.length) return;
    setPulses(p => [...p, ...fresh]);
    setTimeout(() => setPulses(p => p.filter(x => !fresh.includes(x))), 1200);
  };

  // Your group only: rebuild the picture from your feed on every update (names replace "Agent A" once everyone
  // accepts), and animate only what's new.
  useEffect(() => {
    if (!ME) return;
    let live = true, last = 0;
    const tick = () =>
      fetch(`${API}/agents/${encodeURIComponent(ME)}/feed`)
        .then(r => (r.ok ? r.json() : null))
        .then((f: Feed | null) => {
          if (!live || !f) return;
          setOnline(true);
          let b = EMPTY;
          const fresh: Pulse[] = [], noAt: Record<string, number> = {};
          for (const e of f.events) {
            const [next, p] = apply(b, e);
            b = next;
            if (e.kind === 'broker' && e.data.answer === 'no') noAt[e.data.agent_id] = e.ts;
            if (e.seq > last && Date.now() / 1000 - e.ts < 5) fresh.push(...p);
          }
          const now = Date.now() / 1000;
          const stays = ([id, st]: [string, Status]) =>
            id === 'you' || (st !== 'pool' && !(st === 'no' && now - (noAt[id] ?? 0) > GONE_AFTER_S));
          setBoard({ ...b, agents: Object.fromEntries(Object.entries(b.agents).filter(stays)) });
          setFeed(f.events.slice(-200));
          setLabels(f.labels);
          if (f.events.length) last = f.events[f.events.length - 1].seq;
          animate(fresh);
        })
        .catch(() => live && setOnline(false));
    tick();
    const id = setInterval(tick, 1500);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (ME) return;
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
        if (Date.now() / 1000 - e.ts < 3) animate(fresh);
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

  // Keep the newest line in view by scrolling the log box itself, not the whole page (it jumps on phones).
  useEffect(() => {
    const box = feedBox.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [feed]);

  const ids = Object.keys(board.agents);
  const label = (id: string) => labels[id] ?? title(id);
  const initials = (id: string) => (label(id).startsWith('Agent ') ? label(id).slice(6) : label(id).slice(0, 3));
  const pos = (id: string) => {
    const i = ids.indexOf(id), a = (i / Math.max(ids.length, 1)) * 2 * Math.PI - Math.PI / 2;
    return { x: MID + RING * Math.cos(a), y: MID + RING * Math.sin(a) };
  };

  return (
    <main className="app wide">
      <header className="log-top">
        <div className="brand">
          <a className="icon-btn" href={backHref()} aria-label="Back to your agent"><Icon name="back" size={22} /></a>
          <Icon name="live" size={40} />
          <h1>{ME ? 'Your group, live' : 'Negotiation log'}</h1>
          <span className={`live-pill${online ? ' on' : ''}`}>{online ? 'live' : 'reconnecting…'}</span>
        </div>
        <span className="muted">Every line runs through the Coordinator</span>
      </header>

      <div className="log-split">
        <section className="card violet">
          <svg viewBox={`0 0 ${SIZE} ${SIZE}`} className="net" role="img" aria-label="Agents around the coordinator">
            {ids.map(id => {
              const p = pos(id);
              return <line key={id} x1={MID} y1={MID} x2={p.x} y2={p.y} className="spoke" />;
            })}
            {/* No lines between agents: every agent talks to the coordinator only. A group shows in its nodes' state. */}
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
                  <text x={p.x} y={p.y + 5}>{initials(id)}</text>
                  <text x={p.x} y={p.y + 42} className="name">{label(id)}</text>
                  <text x={p.x} y={p.y + 56} className="sub">{STATUS_LABEL[s]}</text>
                </g>
              );
            })}
          </svg>
          {ids.length === 0 && <p className="muted">{ME ? 'Nothing yet. Start a challenge or wait for an invite.' : 'No one checked in yet. Log in from another window.'}</p>}
          {ME && <p className="muted small">Only your agent, the coordinator, and the agents considered for your group. Others stay anonymous until everyone accepts.</p>}
          {board.reason && <p className="muted"><b>Why this group:</b> {board.reason}</p>}
        </section>

        <section className="card feed" ref={feedBox}>
          {feed.length === 0 && <p className="muted">Waiting for the first agent…</p>}
          {feed.map(e => (
            <p key={e.seq} className="event">
              <span className={`tag ${e.kind}`}>[{e.kind}]</span><span>{e.text}</span>
            </p>
          ))}
        </section>
      </div>
    </main>
  );
}
