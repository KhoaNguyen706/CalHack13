// Shared pieces of the Krew design: Pebble (your agent), the walrus (the Coordinator), puzzle-piece seats around
// the walrus, and the small line icons. Colors come from the CSS tokens in index.css, so dark and light both work.

// The puzzle piece every person is drawn as. Others stay a letter on a piece until the whole group accepts.
const PIECE = 'M -34 -40 L 34 -40 Q 40 -40 40 -34 L 40 -12 A 14 14 0 1 1 40 12 L 40 34 Q 40 40 34 40 L -34 40 Q -40 40 -40 34 L -40 -34 Q -40 -40 -34 -40 Z';

export type PebbleMood = 'happy' | 'thinking' | 'cheer';
export type WalrusMood = 'happy' | 'sorry' | 'cheer';

// Pebble the seal, your agent, in your color scarf.
export function Pebble({ mood = 'happy', size = 50 }: { mood?: PebbleMood; size?: number }) {
  const scarf = ['var(--teal)', 'var(--teal-2)', 'var(--teal-3)'];
  return (
    <svg width={size} height={size} viewBox="0 0 200 200" fill="none" role="img" aria-label="Pebble, your agent" className="pebble">
      <ellipse cx="100" cy="184" rx="62" ry="46" fill="#BCD6D5" />
      <ellipse cx="44" cy="168" rx="15" ry="26" transform="rotate(28 44 168)" fill="#A9C7C6" />
      <ellipse cx="156" cy="168" rx="15" ry="26" transform="rotate(-28 156 168)" fill="#A9C7C6" />
      <ellipse cx="100" cy="92" rx="62" ry="54" fill="#C9E0DF" />
      <ellipse cx="100" cy="58" rx="34" ry="13" fill="#FFFFFF" opacity="0.16" />
      <ellipse cx="80" cy="56" rx="5" ry="3" fill="#7FA3A3" opacity="0.55" />
      <ellipse cx="118" cy="50" rx="4" ry="3" fill="#7FA3A3" opacity="0.55" />
      <ellipse cx="134" cy="64" rx="3.5" ry="2.5" fill="#7FA3A3" opacity="0.55" />
      <ellipse cx="57" cy="108" rx="10" ry="5.5" fill="#FF9E9E" opacity="0.4" />
      <ellipse cx="143" cy="108" rx="10" ry="5.5" fill="#FF9E9E" opacity="0.4" />
      <ellipse cx="100" cy="118" rx="31" ry="21" fill="#EAF5F3" />
      <g fill="#9DBFBE">
        <circle cx="87" cy="115" r="2" /><circle cx="92" cy="120" r="2" /><circle cx="113" cy="115" r="2" /><circle cx="108" cy="120" r="2" />
      </g>
      <g stroke="#8FB3B2" strokeWidth="2.2" strokeLinecap="round">
        <path d="M74 113 L22 104" /><path d="M73 119 L18 121" /><path d="M75 125 L26 138" />
        <path d="M126 113 L178 104" /><path d="M127 119 L182 121" /><path d="M125 125 L174 138" />
      </g>
      <ellipse cx="100" cy="105" rx="9.5" ry="6.5" fill="#0C2F2D" />
      <ellipse cx="97" cy="103" rx="2.6" ry="1.6" fill="#FFFFFF" opacity="0.55" />
      <path d="M100 111 V117" stroke="#0C2F2D" strokeWidth="3" strokeLinecap="round" />
      {mood === 'happy' && <path d="M87 118 Q100 131 113 118" stroke="#0C2F2D" strokeWidth="3.5" strokeLinecap="round" />}
      {mood === 'thinking' && <path d="M91 122 Q100 125 109 122" stroke="#0C2F2D" strokeWidth="3.5" strokeLinecap="round" />}
      {mood === 'cheer' ? (
        <>
          <path d="M86 117 Q100 143 114 117 Z" fill="#0C2F2D" />
          <ellipse cx="100" cy="125" rx="7" ry="3.6" fill="#F28B82" />
          <g stroke="#0C2F2D" strokeWidth="4.5" strokeLinecap="round">
            <path d="M63 90 Q74 76 85 90" /><path d="M115 90 Q126 76 137 90" />
          </g>
        </>
      ) : (
        <g transform={mood === 'thinking' ? 'translate(3 -4)' : undefined}>
          {[74, 126].map(x => (
            <g key={x} className="blink">
              <ellipse cx={x} cy="84" rx="10" ry="12.5" fill="#0C2F2D" />
              <circle cx={x + 4} cy="79" r="3.8" fill="#FFFFFF" />
              <circle cx={x - 3} cy="89" r="1.8" fill="#FFFFFF" opacity="0.8" />
            </g>
          ))}
        </g>
      )}
      <path d="M48 141 Q100 168 152 141" stroke={scarf[0]} strokeWidth="15" strokeLinecap="round" />
      <path d="M60 144 Q100 164 140 144" stroke={scarf[1]} strokeWidth="3" strokeLinecap="round" opacity="0.6" />
      <rect x="110" y="150" width="19" height="31" rx="9" transform="rotate(-8 119 150)" fill={scarf[1]} />
      <path d="M118 160 L120 175" stroke={scarf[2]} strokeWidth="2.5" strokeLinecap="round" />
      {mood === 'thinking' && (
        <g fill={scarf[0]}><circle cx="150" cy="34" r="3" /><circle cx="163" cy="23" r="4.5" /><circle cx="178" cy="12" r="6" /></g>
      )}
      {mood === 'cheer' && (
        <g fill={scarf[0]}>
          {[[28, 38, 1.1], [168, 30, 0.9], [178, 86, 0.6], [16, 92, 0.55]].map(([x, y, s]) => (
            <path key={x} d="M0 -9 L2.5 -2.5 L9 0 L2.5 2.5 L0 9 L-2.5 2.5 L-9 0 L-2.5 -2.5 Z" transform={`translate(${x} ${y}) scale(${s})`} />
          ))}
        </g>
      )}
    </svg>
  );
}

// The Coordinator: a friendly walrus. Violet, always. `x`/`y` place it when nested inside another svg.
export function Walrus({ mood = 'happy', size = 76, x, y }: { mood?: WalrusMood; size?: number; x?: number; y?: number }) {
  const ink = '#2E2252', body = '#EFEAFF', muzzle = '#FFFFFF', glow = 'var(--violet)';
  return (
    <svg x={x} y={y} width={size} height={size} viewBox="0 0 120 120" fill="none" role="img" aria-label="The Coordinator, a friendly walrus">
      <path d="M 60 12 C 92 12 112 34 112 62 C 112 92 90 106 60 106 C 30 106 8 92 8 62 C 8 34 28 12 60 12 Z" fill={body} stroke={ink} strokeWidth="3.2" strokeLinejoin="round" />
      <path d="M 44 24 Q 52 19 60 22" stroke={glow} strokeWidth="2.6" strokeLinecap="round" />
      <path d="M 60 22 Q 68 19 76 24" stroke={glow} strokeWidth="2.6" strokeLinecap="round" />
      <circle cx="60" cy="30" r="2.2" fill={glow} /><circle cx="48" cy="31" r="1.8" fill={glow} /><circle cx="72" cy="31" r="1.8" fill={glow} />
      {mood === 'cheer' ? (
        <g stroke={ink} strokeWidth="3.4" strokeLinecap="round"><path d="M 36 54 Q 42 45 48 54" /><path d="M 72 54 Q 78 45 84 54" /></g>
      ) : (
        <g>
          {[42, 78].map(cx => (
            <g key={cx} className="blink"><circle cx={cx} cy="52" r="5" fill={ink} /><circle cx={cx + 1.6} cy="50.2" r="1.6" fill={body} /></g>
          ))}
        </g>
      )}
      {mood === 'sorry' && (
        <>
          <g stroke={ink} strokeWidth="3" strokeLinecap="round"><path d="M 33 44 L 49 38" /><path d="M 87 44 L 71 38" /></g>
          <path d="M 100 30 C 100 30 95 37 95 41 A 5 5 0 0 0 105 41 C 105 37 100 30 100 30 Z" fill="#38BDF8" />
        </>
      )}
      <circle cx="50" cy="76" r="17" fill={muzzle} stroke={ink} strokeWidth="3" />
      <circle cx="70" cy="76" r="17" fill={muzzle} stroke={ink} strokeWidth="3" />
      <circle cx="50" cy="76" r="14.4" fill={muzzle} /><circle cx="70" cy="76" r="14.4" fill={muzzle} />
      <path d="M 53 62 Q 60 58 67 62 Q 66 69 60 69 Q 54 69 53 62 Z" fill={ink} />
      <g fill={ink}><circle cx="42" cy="75" r="1.6" /><circle cx="46" cy="81" r="1.6" /><circle cx="78" cy="75" r="1.6" /><circle cx="74" cy="81" r="1.6" /></g>
      <path d="M 60 69 L 60 77" stroke={ink} strokeWidth="2.6" strokeLinecap="round" />
      {mood === 'happy' && <path d="M 52 80 Q 60 87 68 80" stroke={ink} strokeWidth="2.8" strokeLinecap="round" />}
      {mood === 'sorry' && <path d="M 53 83 Q 60 79 67 83" stroke={ink} strokeWidth="2.8" strokeLinecap="round" />}
      {mood === 'cheer' && <path d="M 52 80 Q 60 80 68 80 Q 66 90 60 90 Q 54 90 52 80 Z" fill={ink} />}
      <path d="M 47 90 L 45 111" stroke={ink} strokeWidth="11" strokeLinecap="round" />
      <path d="M 73 90 L 75 111" stroke={ink} strokeWidth="11" strokeLinecap="round" />
      <path d="M 47 90 L 45 110" stroke="#FFFFFF" strokeWidth="6" strokeLinecap="round" />
      <path d="M 73 90 L 75 110" stroke="#FFFFFF" strokeWidth="6" strokeLinecap="round" />
      {mood === 'cheer' && (
        <g stroke={glow} strokeWidth="3" strokeLinecap="round">
          <path d="M 10 22 L 18 28" /><path d="M 6 38 L 15 40" /><path d="M 110 22 L 102 28" /><path d="M 114 38 L 105 40" />
        </g>
      )}
    </svg>
  );
}

// Seat states, as the component sheet names them. Status is always color + icon + word, never color alone.
export type SeatState = 'you' | 'open' | 'think' | 'in' | 'time' | 'no' | 'acc';
const WORDS: Record<SeatState, string> = {
  you: 'You', open: 'open seat', think: 'thinking…', in: 'in', time: 'checking time…', no: "can't make it", acc: 'accepted',
};
const LETTERS = ['A', 'B', 'C', 'D', 'E'];

export type Seat = { state: SeatState; name?: string };

// The badge on a piece: ✓ in / accepted, clock for checking time, minus for can't make it.
function Badge({ state }: { state: SeatState }) {
  if (!['in', 'acc', 'time', 'no'].includes(state)) return null;
  return (
    <g transform="translate(17 -17)" className={`badge ${state}`}>
      <circle r="10" />
      {(state === 'in' || state === 'acc') && <path d="M -4.5 0.5 L -1.5 3.5 L 4.5 -3.5" />}
      {state === 'time' && <><circle r="5" className="hollow" /><path d="M 0 -2.6 L 0 0.6 L 2.3 2" /></>}
      {state === 'no' && <path d="M -4.5 0 L 4.5 0" />}
    </g>
  );
}

// A bare puzzle-piece outline, the bullet for "what I'd share" rows.
export function PieceIcon({ off = false }: { off?: boolean }) {
  return (
    <svg aria-hidden="true" width="28" height="28" viewBox="-48 -48 96 96" fill="none" className={`piece-icon${off ? ' off' : ''}`}>
      <path d={PIECE} strokeWidth="7" strokeLinejoin="round" />
    </svg>
  );
}

// The group: one seat per person around the walrus, each on its own line to the Coordinator, never to each other.
export function Seats({ seats, mood = 'happy', tight = false }: { seats: Seat[]; mood?: WalrusMood; tight?: boolean }) {
  const n = seats.length;
  const rx = tight ? 92 : 118, ry = tight ? 56 : 66;
  let other = 0;
  const placed = seats.map((seat, i) => {
    const a = ((90 + (i * 360) / n) * Math.PI) / 180;
    const x = Math.round(rx * Math.cos(a)), y = Math.round(ry * Math.sin(a));
    const display = seat.state === 'you' ? 'You' : seat.name || LETTERS[other++];
    return {
      seat, x, y, display,
      rot: Math.round((Math.atan2(-y, -x) * 180) / Math.PI),
      line: `M 0 0 Q ${Math.round(x / 2 - y * 0.2)} ${Math.round(y / 2 + x * 0.2)} ${x} ${y}`,
      label: seat.state === 'you' ? 'You' : `${display} · ${WORDS[seat.state]}`,
    };
  });
  const summary = placed.map(p => (p.seat.state === 'you' ? 'You' : `${p.display} ${WORDS[p.seat.state]}`)).join(', ');
  return (
    <svg viewBox="-170 -104 340 236" className="seats" role="img" aria-label={`Group of ${n} seats around the Coordinator. ${summary}`}>
      <circle r="50" className="core" />
      <circle r="46" className="core-ring" />
      {placed.map((p, i) => <path key={`l${i}`} d={p.line} className={`link ${p.seat.state}`} />)}
      {placed.map((p, i) => (
        <g key={`s${i}`} className={`seat ${p.seat.state}`} style={{ transform: `translate(${p.x}px, ${p.y}px)` }}>
          <g className="pop">
            <g transform={`rotate(${p.rot})`}><path d={PIECE} transform="scale(0.5)" className="shape" /></g>
            {p.seat.state === 'open'
              ? <path d="M -6 0 H 6 M 0 -6 V 6" className="plus" />
              : <text y={p.display === 'You' ? 5 : 7} className={p.display === 'You' ? 'you' : ''}>{p.display === 'You' ? 'You' : p.display.charAt(0)}</text>}
          </g>
          <Badge state={p.seat.state} />
          <text y={p.y < -20 ? -30 : 38} className="word">{p.label}</text>
        </g>
      ))}
      <Walrus mood={mood} size={76} x={-38} y={-38} />
    </svg>
  );
}

// Line icons, 24×24, stroke = currentColor.
const ICONS = {
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  pin: <><path d="M12 3C7.5 3 5 6.5 5 9.5c0 4.5 7 11 7 11s7-6.5 7-11C19 6.5 16.5 3 12 3z" /><circle cx="12" cy="9.5" r="2.4" /></>,
  lock: <><rect x="5" y="11" width="14" height="9" rx="2" /><path d="M8 11V8a4 4 0 018 0v3" /></>,
  check: <path d="M5 12.5l4.5 4.5L19 7.5" />,
  checkCircle: <><circle cx="12" cy="12" r="9" /><path d="M8 12.5l3 3 5-6" /></>,
  dots: <path d="M6 12h.01M12 12h.01M18 12h.01" />,
  back: <path d="M15 5l-7 7 7 7" />,
  menu: <path d="M4 7h16M4 12h16M4 17h16" />,
  close: <path d="M6 6l12 12M18 6L6 18" />,
  send: <path d="M5 12h13M13 6l6 6-6 6" />,
  camera: <><path d="M3 7.5A1.5 1.5 0 014.5 6h2l1.4-2h6.2L15.5 6h2A1.5 1.5 0 0119 7.5v9a1.5 1.5 0 01-1.5 1.5h-13A1.5 1.5 0 013 16.5v-9z" /><circle cx="11" cy="12" r="3.2" /></>,
  live: <><circle cx="9" cy="10" r="5" /><circle cx="17" cy="10" r="5" /><circle cx="13" cy="17" r="5" /></>,
  star: <path d="M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.8z" />,
};

export function Icon({ name, size = 18, className }: { name: keyof typeof ICONS; size?: number; className?: string }) {
  return (
    <svg aria-hidden="true" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" className={className}>
      {ICONS[name]}
    </svg>
  );
}

// Eyebrow + title, the header every card in the design starts with.
export function CardHead({ eyebrow, title, tone = 'accent', sub }: { eyebrow: string; title?: string; tone?: 'accent' | 'violet' | 'green' | 'amber' | 'muted'; sub?: string }) {
  return (
    <div className="card-head">
      <p className={`eyebrow ${tone}`}>{eyebrow}</p>
      {title && <h2>{title}</h2>}
      {sub && <p className="muted">{sub}</p>}
    </div>
  );
}

// "Hmm, I lost the connection" / "No signal right now": shown instead of a raw error while polling fails.
export function Reconnecting({ offline, onRetry }: { offline: boolean; onRetry: () => void }) {
  return (
    <section className="conn">
      <div className="avatar xl"><Pebble mood="thinking" size={164} /></div>
      <h2>{offline ? 'No signal right now.' : 'Hmm, I lost the connection. Trying again…'}</h2>
      <p className="muted">{offline ? 'I’ll keep trying and pick up where we left off. Your plans are saved.' : 'Nothing was lost. You don’t need to do anything.'}</p>
      <div className="progress"><div /></div>
      <button className="btn primary" onClick={onRetry}>Try now</button>
    </section>
  );
}

// The privacy rules, in plain words (menu → Privacy).
export const PRIVACY_RULES = [
  ['Agents only talk to the Coordinator', 'Your agent never chats with anyone else’s agent.'],
  ['Others stay letters until everyone says yes', 'First names appear only once the whole group accepts.'],
  ['You never see anyone else’s words or times', 'You only see the kind of message: yes, no, new time?'],
  ['Only rough info', 'Free time as a window, place as an area. Never an exact location.'],
  ['Nothing happens without your tap', 'Sharing, joining, accepting and the photo are your call. I never accept for you.'],
  ['Small groups, public places', 'Groups are 2 to 5, and challenges happen only in public spots.'],
];
