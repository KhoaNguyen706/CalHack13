// Shared pieces of the Krew design: the avatars (Pebble the seal is your agent, Wally the walrus is the Coordinator,
// and the octopus, jellyfish and crab are the others in your group) with their moods, the group drawing (everyone on
// a puzzle piece around Wally), pen doodles, and the small line icons. Colors come from the CSS tokens in index.css,
// so dark and light both work.
import { useEffect, useId, useState, type CSSProperties } from 'react';

// The puzzle piece every person is drawn as. Others stay a letter until the whole group accepts.
const PIECE = 'M -34 -40 L 34 -40 Q 40 -40 40 -34 L 40 -12 A 14 14 0 1 1 40 12 L 40 34 Q 40 40 34 40 L -34 40 Q -40 40 -40 34 L -40 -34 Q -40 -40 -34 -40 Z';
const SPARK = 'M0 -9 L2.5 -2.5 L9 0 L2.5 2.5 L0 9 L-2.5 2.5 L-9 0 L-2.5 -2.5 Z';
const HEART = 'M 0 9 C -14 0 -11 -12 0 -6 C 11 -12 14 0 0 9 Z';
const INK = '#0C2F2D';
const DROP = '#7DD3FC';

// A tiny tap on phones that can (Android). Elsewhere it's silently nothing.
export function buzz(pattern: number | number[] = 12) {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    // not allowed here; the screen still says it
  }
}

// --- Moods -------------------------------------------------------------------------------------------------------
// Pebble's face says how things are going, so the screen never has to shout it:
//   happy     the default, all good          wave     hello (sign-in)
//   thinking  working on it, waiting         shy      asking before sharing anything about you
//   excited   an invite, a full group        cheer    it worked: locked in, a step passed, done
//   sad       called off, said no            sleepy   connecting, offline, waking up
//   love      you both want to meet again    oops     a photo that didn't count
export type PebbleMood = 'happy' | 'wave' | 'thinking' | 'shy' | 'excited' | 'cheer' | 'sad' | 'sleepy' | 'love' | 'oops';
export type WalrusMood = 'happy' | 'thinking' | 'sorry' | 'cheer';

const FEELS: Record<PebbleMood, string> = {
  happy: 'happy', wave: 'waving hello', thinking: 'thinking', shy: 'a little shy', excited: 'excited', cheer: 'cheering',
  sad: 'sad', sleepy: 'sleepy', love: 'in love with this group', oops: 'oops',
};

type Spot = { x?: number; y?: number };

// Pebble the seal. Only the face changes with the mood; the seal itself stays put, so it always reads as the same
// friend. `scarf` colors another person's seal in the group drawing. Each new mood plays a small hop.
export function Pebble({ mood = 'happy', size = 50, scarf = 'var(--scarf)', decorative = false, x, y }: { mood?: PebbleMood; size?: number; scarf?: string; decorative?: boolean } & Spot) {
  const open = mood === 'excited' || mood === 'cheer' || mood === 'love';
  const blush = mood === 'shy' || mood === 'love' ? 0.85 : 0.4;
  const label = decorative ? { 'aria-hidden': true } : { role: 'img', 'aria-label': `Pebble, your agent, ${FEELS[mood]}` };
  return (
    <svg x={x} y={y} width={size} height={size} viewBox="0 0 200 200" fill="none" className={`pebble ${mood}`} {...label}>
      <g key={mood} className="mood">
        {mood === 'wave' && <g className="flipper-wave"><ellipse cx="175" cy="120" rx="14" ry="27" transform="rotate(40 175 120)" fill="#A9C7C6" /></g>}
        <ellipse cx="100" cy="184" rx="62" ry="46" fill="#BCD6D5" />
        <ellipse cx="44" cy="168" rx="15" ry="26" transform="rotate(28 44 168)" fill="#A9C7C6" />
        {mood !== 'wave' && <ellipse cx="156" cy="168" rx="15" ry="26" transform="rotate(-28 156 168)" fill="#A9C7C6" />}
        <ellipse cx="100" cy="92" rx="62" ry="54" fill="#C9E0DF" />
        <ellipse cx="100" cy="58" rx="34" ry="13" fill="#FFFFFF" opacity="0.16" />
        <ellipse cx="80" cy="56" rx="5" ry="3" fill="#7FA3A3" opacity="0.55" />
        <ellipse cx="118" cy="50" rx="4" ry="3" fill="#7FA3A3" opacity="0.55" />
        <ellipse cx="134" cy="64" rx="3.5" ry="2.5" fill="#7FA3A3" opacity="0.55" />
        <ellipse cx="57" cy="108" rx={mood === 'shy' ? 13 : 10} ry={mood === 'shy' ? 7 : 5.5} fill="#FF9E9E" opacity={blush} />
        <ellipse cx="143" cy="108" rx={mood === 'shy' ? 13 : 10} ry={mood === 'shy' ? 7 : 5.5} fill="#FF9E9E" opacity={blush} />
        {mood === 'shy' && (
          <g stroke="#E0708C" strokeWidth="2.4" strokeLinecap="round">
            <path d="M50 104 l-4 8" /><path d="M57 104 l-4 8" /><path d="M64 104 l-4 8" />
            <path d="M140 104 l-4 8" /><path d="M147 104 l-4 8" /><path d="M154 104 l-4 8" />
          </g>
        )}
        <ellipse cx="100" cy="118" rx="31" ry="21" fill="#EAF5F3" />
        <g fill="#9DBFBE">
          <circle cx="87" cy="115" r="2" /><circle cx="92" cy="120" r="2" /><circle cx="113" cy="115" r="2" /><circle cx="108" cy="120" r="2" />
        </g>
        <g stroke="#8FB3B2" strokeWidth="2.2" strokeLinecap="round">
          <path d="M74 113 L22 104" /><path d="M73 119 L18 121" /><path d="M75 125 L26 138" />
          <path d="M126 113 L178 104" /><path d="M127 119 L182 121" /><path d="M125 125 L174 138" />
        </g>
        <ellipse cx="100" cy="105" rx="9.5" ry="6.5" fill={INK} />
        <ellipse cx="97" cy="103" rx="2.6" ry="1.6" fill="#FFFFFF" opacity="0.55" />
        <path d="M100 111 V117" stroke={INK} strokeWidth="3" strokeLinecap="round" />
        <Mouth mood={mood} open={open} />
        <Eyes mood={mood} />
        {mood === 'sad' && (
          <g stroke={INK} strokeWidth="4" strokeLinecap="round"><path d="M61 70 L84 63" /><path d="M139 70 L116 63" /></g>
        )}
        <path d="M48 141 Q100 168 152 141" stroke={scarf} strokeWidth="15" strokeLinecap="round" />
        <path d="M60 144 Q100 164 140 144" stroke="#000" strokeWidth="3" strokeLinecap="round" opacity="0.12" />
        <rect x="110" y="150" width="19" height="31" rx="9" transform="rotate(-8 119 150)" fill={scarf} />
        <path d="M118 160 L120 175" stroke="#000" strokeWidth="2.5" strokeLinecap="round" opacity="0.15" />
        <Extras mood={mood} scarf={scarf} />
      </g>
    </svg>
  );
}

function Eyes({ mood }: { mood: PebbleMood }) {
  if (mood === 'cheer') {
    return <g stroke={INK} strokeWidth="4.5" strokeLinecap="round"><path d="M63 90 Q74 76 85 90" /><path d="M115 90 Q126 76 137 90" /></g>;
  }
  if (mood === 'sleepy') {
    return <g stroke={INK} strokeWidth="4.5" strokeLinecap="round"><path d="M63 85 Q74 93 85 85" /><path d="M115 85 Q126 93 137 85" /></g>;
  }
  if (mood === 'love') {
    return <g fill="#EF4F7F">{[74, 126].map(x => <g key={x} transform={`translate(${x} 85) scale(1.15)`}><path d={HEART} className="beat" /></g>)}</g>;
  }
  const big = mood === 'excited';
  const look = mood === 'thinking' ? 'translate(3 -4)' : mood === 'shy' ? 'translate(-3 5)' : undefined;
  return (
    <g transform={look}>
      {[74, 126].map(x => (
        <g key={x} className="blink">
          <ellipse cx={x} cy="84" rx={big ? 11.5 : 10} ry={big ? 14 : 12.5} fill={INK} />
          <circle cx={x + 4} cy={big ? 78 : 79} r={big ? 4.8 : 3.8} fill="#FFFFFF" />
          <circle cx={x - 3} cy="89" r={big ? 2.4 : 1.8} fill="#FFFFFF" opacity="0.8" />
        </g>
      ))}
    </g>
  );
}

function Mouth({ mood, open }: { mood: PebbleMood; open: boolean }) {
  const line = { stroke: INK, strokeWidth: 3.5, strokeLinecap: 'round' as const };
  if (open) {
    return <><path d="M86 117 Q100 143 114 117 Z" fill={INK} /><ellipse cx="100" cy="125" rx="7" ry="3.6" fill="#F28B82" /></>;
  }
  if (mood === 'sleepy') return <ellipse cx="100" cy="123" rx="3.6" ry="4.4" fill={INK} />;
  const d = {
    thinking: 'M91 122 Q100 125 109 122',
    shy: 'M93 121 Q100 126 107 121',
    sad: 'M89 125 Q100 117 111 125',
    oops: 'M88 122 Q94 118 100 122 Q106 126 112 122',
  }[mood as string] ?? 'M87 118 Q100 131 113 118';
  return <path d={d} {...line} />;
}

function Extras({ mood, scarf }: { mood: PebbleMood; scarf: string }) {
  switch (mood) {
    case 'thinking':
      return <g fill={scarf} className="thought"><circle cx="150" cy="34" r="3" /><circle cx="163" cy="23" r="4.5" /><circle cx="178" cy="12" r="6" /></g>;
    case 'cheer':
      return (
        <g fill={scarf}>
          {[[28, 38, 1.1], [168, 30, 0.9], [178, 86, 0.6], [16, 92, 0.55]].map(([x, y, s], i) => (
            <g key={x} transform={`translate(${x} ${y}) scale(${s})`}><path d={SPARK} className="twinkle" style={{ '--i': i } as CSSProperties} /></g>
          ))}
        </g>
      );
    case 'excited':
      return (
        <g stroke={scarf} strokeWidth="4.5" strokeLinecap="round" className="shake">
          <path d="M36 28 L47 41" /><path d="M22 50 L37 54" /><path d="M164 28 L153 41" /><path d="M178 50 L163 54" />
        </g>
      );
    case 'sad':
      return <path d="M136 98 C136 98 130 106 130 110 A6 6 0 0 0 142 110 C142 106 136 98 136 98 Z" fill={DROP} className="drip" />;
    case 'oops':
      return <path d="M160 40 C160 40 153 50 153 55 A7 7 0 0 0 167 55 C167 50 160 40 160 40 Z" fill={DROP} className="drip" />;
    case 'sleepy':
      return (
        <g stroke={scarf} strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" className="zzz">
          <path d="M146 42 h13 l-13 15 h13" /><path d="M168 16 h9 l-9 10 h9" />
        </g>
      );
    case 'love':
      return (
        <g fill="#F472B6" className="float">
          <g transform="translate(166 40)"><path d={HEART} transform="scale(0.6)" /></g>
          <g transform="translate(30 46)"><path d={HEART} transform="scale(0.45)" /></g>
        </g>
      );
    case 'wave':
      return <g stroke={scarf} strokeWidth="4" strokeLinecap="round" className="wave-lines"><path d="M184 92 Q192 104 186 116" /><path d="M192 82 Q204 100 194 122" /></g>;
    default:
      return null;
  }
}

// The other avatars on the sheet. Same face rules as Pebble (dark eyes with a glint, a small mouth, blush), drawn
// for each body: where the eyes and mouth sit, and how big the eyes are.
export type Animal = 'seal' | 'octopus' | 'jelly' | 'crab';
type FaceAt = { l: [number, number]; r: [number, number]; eye: number; mouth: [number, number]; brows: boolean };

const FACE_AT: Record<Exclude<Animal, 'seal'>, FaceAt> = {
  octopus: { l: [80, 100], r: [120, 100], eye: 10, mouth: [100, 117], brows: true },
  jelly: { l: [80, 92], r: [120, 92], eye: 9.5, mouth: [100, 108], brows: true },
  crab: { l: [78, 76], r: [122, 76], eye: 8, mouth: [100, 117], brows: false },
};

function Face({ at, mood }: { at: FaceAt; mood: PebbleMood }) {
  const { l: [lx, ly], r: [rx, ry], eye: e, mouth: [mx, my] } = at;
  const stroke = { stroke: INK, strokeWidth: 4, strokeLinecap: 'round' as const };
  const eyes =
    mood === 'cheer' ? (
      <g {...stroke}><path d={`M${lx - e} ${ly + 3} Q${lx} ${ly - 10} ${lx + e} ${ly + 3}`} /><path d={`M${rx - e} ${ry + 3} Q${rx} ${ry - 10} ${rx + e} ${ry + 3}`} /></g>
    ) : mood === 'sleepy' ? (
      <g {...stroke}><path d={`M${lx - e} ${ly - 1} Q${lx} ${ly + 7} ${lx + e} ${ly - 1}`} /><path d={`M${rx - e} ${ry - 1} Q${rx} ${ry + 7} ${rx + e} ${ry - 1}`} /></g>
    ) : mood === 'love' ? (
      <g fill="#EF4F7F">{[[lx, ly], [rx, ry]].map(([x, y]) => <g key={x} transform={`translate(${x} ${y}) scale(${e / 9})`}><path d={HEART} className="beat" /></g>)}</g>
    ) : (
      <g transform={mood === 'thinking' ? 'translate(3 -4)' : mood === 'shy' ? 'translate(-3 4)' : undefined}>
        {[[lx, ly], [rx, ry]].map(([x, y]) => {
          const r = mood === 'excited' ? e * 1.2 : e;
          return (
            <g key={x} className="blink">
              <circle cx={x} cy={y} r={r} fill={INK} />
              <circle cx={x + r * 0.35} cy={y - r * 0.4} r={r * 0.36} fill="#FFFFFF" />
              <circle cx={x - r * 0.3} cy={y + r * 0.45} r={r * 0.16} fill="#FFFFFF" opacity="0.8" />
            </g>
          );
        })}
      </g>
    );
  const open = mood === 'excited' || mood === 'cheer' || mood === 'love';
  const mouth = open ? (
    <><path d={`M${mx - 12} ${my - 1} Q${mx} ${my + 22} ${mx + 12} ${my - 1} Z`} fill={INK} /><ellipse cx={mx} cy={my + 8} rx="5.5" ry="3" fill="#F28B82" /></>
  ) : mood === 'sleepy' ? (
    <ellipse cx={mx} cy={my + 4} rx="3.2" ry="4" fill={INK} />
  ) : (
    <path
      {...stroke}
      strokeWidth={3.5}
      d={{
        sad: `M${mx - 10} ${my + 7} Q${mx} ${my - 1} ${mx + 10} ${my + 7}`,
        thinking: `M${mx - 8} ${my + 3} Q${mx} ${my + 5} ${mx + 8} ${my + 3}`,
        shy: `M${mx - 6} ${my + 2} Q${mx} ${my + 6} ${mx + 6} ${my + 2}`,
        oops: `M${mx - 11} ${my + 3} Q${mx - 5.5} ${my - 1} ${mx} ${my + 3} Q${mx + 5.5} ${my + 7} ${mx + 11} ${my + 3}`,
      }[mood as string] ?? `M${mx - 11} ${my} Q${mx} ${my + 11} ${mx + 11} ${my}`}
    />
  );
  return (
    <>
      {eyes}
      {mouth}
      {mood === 'sad' && at.brows && (
        <g {...stroke}><path d={`M${lx - 11} ${ly - 15} L${lx + 9} ${ly - 21}`} /><path d={`M${rx + 11} ${ry - 15} L${rx - 9} ${ry - 21}`} /></g>
      )}
    </>
  );
}

const TENTACLES = [66, 86, 106, 126, 144];

// Octopus (purple top hat), jellyfish (pink bow) and crab (teal glasses), from the avatar sheet.
export function Critter({ animal, mood = 'happy', size = 50, decorative = false, x, y }: { animal: Exclude<Animal, 'seal'>; mood?: PebbleMood; size?: number; decorative?: boolean } & Spot) {
  const name = { octopus: 'Octopus', jelly: 'Jellyfish', crab: 'Crab' }[animal];
  const label = decorative ? { 'aria-hidden': true } : { role: 'img', 'aria-label': `${name}, ${FEELS[mood]}` };
  const blush = mood === 'shy' || mood === 'love' ? 0.9 : 0.6;
  return (
    <svg x={x} y={y} width={size} height={size} viewBox="0 0 200 200" fill="none" className={`critter ${animal} ${mood}`} {...label}>
      <g key={mood} className="mood">
        {animal === 'octopus' && (
          <>
            <g stroke="#E8B4CD" strokeWidth="13" strokeLinecap="round">
              <path d="M66 140 C50 154 40 170 50 184 C56 192 68 188 64 178" />
              <path d="M84 150 C78 166 76 182 88 190" />
              <path d="M100 152 C100 168 96 182 104 192" />
              <path d="M116 150 C122 166 124 182 112 190" />
              <path d="M134 140 C150 154 160 170 150 184 C144 192 132 188 136 178" />
            </g>
            <circle cx="100" cy="100" r="58" fill="#F7D6E6" />
            <ellipse cx="80" cy="74" rx="15" ry="7" fill="#FFFFFF" opacity="0.35" transform="rotate(-20 80 74)" />
            <path d="M72 50 L80 14 Q81 9 87 9 L122 12 Q128 13 128 19 L130 50 Z" fill="#3D3466" />
            <path d="M73 42 L129 42 L130 50 L72 50 Z" fill="#2DD4BF" />
            <ellipse cx="100" cy="51" rx="52" ry="10" fill="#3D3466" transform="rotate(-5 100 51)" />
            <ellipse cx="64" cy="118" rx="10" ry="6" fill="#F99FB6" opacity={blush} />
            <ellipse cx="136" cy="118" rx="10" ry="6" fill="#F99FB6" opacity={blush} />
          </>
        )}
        {animal === 'jelly' && (
          <>
            <g stroke="#BBD7FA" strokeWidth="7" strokeLinecap="round">
              {TENTACLES.map(t => <path key={t} d={`M${t} 128 c -9 14 8 26 0 42 c -4 8 0 14 4 18`} />)}
            </g>
            <path d="M28 118 C22 62 58 30 100 30 C142 30 178 62 172 118 C162 126 152 118 142 126 C130 118 116 128 100 124 C84 128 70 118 58 126 C48 118 38 126 28 118 Z" fill="#BFDBFE" />
            <ellipse cx="78" cy="50" rx="14" ry="6" fill="#FFFFFF" opacity="0.4" transform="rotate(-22 78 50)" />
            <ellipse cx="54" cy="74" rx="4.5" ry="7.5" fill="#8DAEEC" transform="rotate(20 54 74)" />
            <circle cx="70" cy="58" r="3.4" fill="#8DAEEC" /><circle cx="108" cy="44" r="3" fill="#8DAEEC" />
            <ellipse cx="128" cy="36" rx="13" ry="10" fill="#F59393" transform="rotate(-18 128 36)" />
            <ellipse cx="155" cy="34" rx="13" ry="10" fill="#F59393" transform="rotate(16 155 34)" />
            <circle cx="141" cy="36" r="6.5" fill="#D7353F" />
            <ellipse cx="66" cy="106" rx="8" ry="5" fill="#F9A8C9" opacity={blush * 0.7} />
            <ellipse cx="134" cy="106" rx="8" ry="5" fill="#F9A8C9" opacity={blush * 0.7} />
          </>
        )}
        {animal === 'crab' && (
          <>
            <g stroke="#F2B49E" strokeWidth="9" strokeLinecap="round">
              <path d="M52 138 L28 156" /><path d="M60 150 L42 178" /><path d="M148 138 L172 156" /><path d="M140 150 L158 178" />
              <path d="M58 100 L42 74" /><path d="M142 100 L158 74" />
            </g>
            <path d="M18 66 C10 50 22 34 38 38 L36 53 L51 49 C55 64 40 79 18 66 Z" fill="#F8CBB9" />
            <path d="M182 66 C190 50 178 34 162 38 L164 53 L149 49 C145 64 160 79 182 66 Z" fill="#F8CBB9" />
            <ellipse cx="100" cy="116" rx="66" ry="44" fill="#F8CBB9" />
            <ellipse cx="80" cy="92" rx="17" ry="6" fill="#FFFFFF" opacity="0.3" transform="rotate(-10 80 92)" />
            <g stroke="#2DD4BF" strokeWidth="5" strokeLinecap="round">
              <path d="M94 74 Q100 68 106 74" /><path d="M62 70 L52 62" /><path d="M138 70 L148 62" />
            </g>
            <circle cx="78" cy="76" r="16" fill="#2F4A4A" stroke="#2DD4BF" strokeWidth="5" />
            <circle cx="122" cy="76" r="16" fill="#2F4A4A" stroke="#2DD4BF" strokeWidth="5" />
            <ellipse cx="68" cy="122" rx="10" ry="6" fill="#F59E9E" opacity={blush} />
            <ellipse cx="132" cy="122" rx="10" ry="6" fill="#F59E9E" opacity={blush} />
          </>
        )}
        <Face at={FACE_AT[animal]} mood={mood} />
        <Extras mood={mood} scarf="var(--scarf)" />
      </g>
    </svg>
  );
}

// Any avatar by animal: the seal is Pebble.
export function Buddy({ animal, ...rest }: { animal: Animal; mood?: PebbleMood; size?: number; scarf?: string; decorative?: boolean } & Spot) {
  if (animal === 'seal') return <Pebble {...rest} />;
  const { scarf: _scarf, ...critter } = rest;
  return <Critter animal={animal} {...critter} />;
}

// Wally the walrus, the Coordinator, as in the mascot art (traced at 1024 px, drawn at 120 so the mood animations
// share units with the other avatars). `x`/`y` place it when nested inside another svg.
const ART = 'scale(0.1171875)'; // 120 / 1024
export function Walrus({ mood = 'happy', size = 76, x, y }: { mood?: WalrusMood; size?: number } & Spot) {
  const ink = '#35317C', body = '#C9C1FF', muzzle = '#E5E1FF', brow = '#6E7AE6';
  return (
    <svg x={x} y={y} width={size} height={size} viewBox="0 0 120 120" fill="none" role="img" aria-label={`Wally the walrus, the Coordinator${mood === 'happy' ? '' : `, ${mood === 'cheer' ? 'cheering' : mood}`}`} className={`walrus ${mood}`}>
      <g key={mood} className="mood">
        <g transform={ART}>
          <path d="M512 102 C712 102 812 228 872 372 C924 498 944 590 936 690 C922 858 764 944 512 944 C260 944 102 858 88 690 C80 590 100 498 152 372 C212 228 312 102 512 102 Z" fill={body} stroke={ink} strokeWidth="22" strokeLinejoin="round" />
          <path d="M380 212 Q446 172 512 198 Q578 172 644 212" stroke={brow} strokeWidth="24" strokeLinecap="round" strokeLinejoin="round" />
          <g fill={brow}><circle cx="401" cy="272" r="15" /><circle cx="512" cy="263" r="19" /><circle cx="623" cy="272" r="15" /></g>
          {mood === 'cheer' ? (
            <g stroke={ink} strokeWidth="24" strokeLinecap="round"><path d="M298 414 Q341 352 384 414" /><path d="M640 414 Q683 352 726 414" /></g>
          ) : (
            <g transform={mood === 'thinking' ? 'translate(-8 -14)' : undefined}>
              {[341, 683].map(cx => (
                <g key={cx} className="blink"><circle cx={cx} cy="400" r="44" fill={ink} /><circle cx={cx + 14} cy="384" r="14" fill="#FFFFFF" /></g>
              ))}
            </g>
          )}
          {mood === 'sorry' && <g stroke={ink} strokeWidth="22" strokeLinecap="round"><path d="M282 330 L382 300" /><path d="M742 330 L642 300" /></g>}
          <path d="M352 700 L440 700 L428 890 Q425 918 396 918 Q367 918 364 890 Z" fill="#FFFFFF" stroke={ink} strokeWidth="20" strokeLinejoin="round" />
          <path d="M584 700 L672 700 L660 890 Q657 918 628 918 Q599 918 596 890 Z" fill="#FFFFFF" stroke={ink} strokeWidth="20" strokeLinejoin="round" />
          {/* One outline round both cheeks: outlined circles first, then their fills on top hide the inner edges. */}
          <circle cx="420" cy="588" r="148" fill={muzzle} stroke={ink} strokeWidth="22" />
          <circle cx="604" cy="588" r="148" fill={muzzle} stroke={ink} strokeWidth="22" />
          <circle cx="420" cy="588" r="137" fill={muzzle} /><circle cx="604" cy="588" r="137" fill={muzzle} />
          <path d="M432 452 Q512 428 592 452 C586 506 556 540 512 540 C468 540 438 506 432 452 Z" fill={ink} />
          <g fill={ink}>
            <circle cx="350" cy="580" r="13" /><circle cx="384" cy="640" r="13" /><circle cx="444" cy="648" r="10" />
            <circle cx="674" cy="580" r="13" /><circle cx="640" cy="640" r="13" /><circle cx="580" cy="648" r="10" />
          </g>
          {mood === 'thinking' && <g fill={brow} className="thought"><circle cx="800" cy="150" r="18" /><circle cx="862" cy="94" r="24" /><circle cx="934" cy="42" r="30" /></g>}
          {mood === 'cheer' && (
            <g stroke={brow} strokeWidth="22" strokeLinecap="round" className="shake">
              <path d="M110 214 L168 254" /><path d="M66 324 L134 334" /><path d="M914 214 L856 254" /><path d="M958 324 L890 334" />
            </g>
          )}
        </g>
        {mood === 'sorry' && (
          <g className="drip">
            <path transform={ART} d="M846 424 C846 424 818 466 818 488 A28 28 0 0 0 874 488 C874 466 846 424 846 424 Z" fill="#38BDF8" />
          </g>
        )}
      </g>
    </svg>
  );
}

// --- The group drawing -------------------------------------------------------------------------------------------

// Seat states, as the component sheet names them. Status is always color + icon + word, never color alone.
export type SeatState = 'you' | 'open' | 'think' | 'in' | 'time' | 'no' | 'acc';
const WORDS: Record<SeatState, string> = {
  you: 'You', open: 'open seat', think: 'asking…', in: 'in', time: 'checking time…', no: "can't make it", acc: 'accepted',
};
const FACES: Record<SeatState, PebbleMood> = { you: 'happy', open: 'happy', think: 'thinking', in: 'happy', time: 'thinking', no: 'sad', acc: 'cheer' };
// Everyone else in the group is another animal from the sheet, so the drawing tells people apart without naming them.
export const CREW: { animal: Animal; scarf?: string }[] = [{ animal: 'octopus' }, { animal: 'jelly' }, { animal: 'crab' }, { animal: 'seal', scarf: '#F9A8D4' }];
const STEP = 54, ROW = 50; // pieces side by side once they snap, under Wally

// `n`: where this person comes in the Coordinator's order (0 = Agent A). The letter and the animal both follow it,
// so someone looks the same on the group card, the live screen and after their name shows.
export type Seat = { state: SeatState; name?: string; n?: number };

// The animal for the nth other person, the same on every screen. You are always Pebble.
export const crewAt = (n: number) => CREW[Math.max(0, n) % CREW.length];

// The badge on a piece: ✓ in / accepted, clock for checking time, minus for can't make it.
function Badge({ state }: { state: SeatState }) {
  if (!['in', 'acc', 'time', 'no'].includes(state)) return null;
  return (
    <g transform="translate(20 -21)" className={`badge ${state}`}>
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

const px = (n: number) => `${n}px`;

// The group: everyone's avatar on a puzzle piece around Wally, each on its own line to the Coordinator, never to
// each other. Invites travel out along the lines and every "yes" travels back; a line draws itself the moment
// someone is connected. Once everyone's in (`snap`), the pieces fly together and click into one row.
export function Seats({ seats, mood = 'happy', tight = false, snap = false }: { seats: Seat[]; mood?: WalrusMood; tight?: boolean; snap?: boolean }) {
  const clip = useId();
  const n = seats.length;
  const rx = tight ? 104 : 128, ry = tight ? 72 : 86;
  // Seats without a place in the order (an open seat) take the next letters after everyone who has one.
  let other = Math.max(-1, ...seats.map(s => s.n ?? -1)) + 1;
  const placed = seats.map((seat, i) => {
    const a = ((90 + (i * 360) / n) * Math.PI) / 180;
    const x = Math.round(rx * Math.cos(a)), y = Math.round(ry * Math.sin(a));
    const cx = Math.round(x / 2 - y * 0.2), cy = Math.round(y / 2 + x * 0.2);
    const me = seat.state === 'you' || seat.name === 'You';
    const k = me ? 0 : seat.n ?? other++;
    const display = me ? 'You' : seat.name || String.fromCharCode(65 + k); // A, B, C… as the Coordinator letters them
    // Points along the curve (t = ¼, ½, ¾, 1) for messages to travel on; the curve itself is a quadratic from the walrus.
    const at = (t: number) => [Math.round(2 * (1 - t) * t * cx + t * t * x), Math.round(2 * (1 - t) * t * cy + t * t * y)];
    const path: Record<string, string> = {};
    [0.25, 0.5, 0.75, 1].forEach((t, k) => {
      const [qx, qy] = at(t);
      path[`--q${k + 1}x`] = px(qx);
      path[`--q${k + 1}y`] = px(qy);
    });
    const rot = Math.round((Math.atan2(-y, -x) * 180) / Math.PI);
    return {
      seat, x, y, display, me, rot, path,
      // Snapped: side by side, tab to the left so each piece plugs into the one before it.
      row: { x: Math.round((i - (n - 1) / 2) * STEP), y: ROW, rot: rot >= 0 ? 180 : -180 },
      who: me ? { animal: 'seal' as Animal } : crewAt(k),
      line: `M 0 0 Q ${cx} ${cy} ${x} ${y}`,
      // A second, lighter pass of the pen, a little off the first: lines look drawn, not plotted.
      sketch: `M 2 1 Q ${cx + 5} ${cy - 4} ${x - 1} ${y + 2}`,
      label: me ? 'You' : `${display} · ${WORDS[seat.state]}`,
    };
  });
  const yeses = seats.filter(s => s.state === 'acc').length;
  const summary = placed.map(p => (p.me ? 'You' : `${p.display} ${WORDS[p.seat.state]}`)).join(', ');
  return (
    <svg viewBox="-172 -120 344 264" className={`seats${snap ? ' snapped' : ''}`} role="img" aria-label={`Group of ${n} around the Coordinator. ${summary}`}>
      <defs><clipPath id={clip}><circle r="19" /></clipPath></defs>
      {!snap && placed.map((p, i) => (
        <g key={`l${i}`} className={`link ${p.seat.state}`} style={{ '--i': i } as CSSProperties}>
          <path d={p.line} pathLength={1} className="ink" />
          <path d={p.sketch} pathLength={1} className="pencil" />
        </g>
      ))}
      {!snap && placed.map((p, i) => {
        const kind = p.seat.state === 'think' ? 'out' : p.seat.state === 'time' ? 'back' : p.seat.state === 'acc' ? 'yes' : null;
        return kind && <circle key={`m${i}-${kind}`} r="4.5" className={`note ${kind}`} style={{ ...p.path, '--i': i } as CSSProperties} />;
      })}
      <g className="hub" style={{ transform: snap ? 'translate(0px, -70px) scale(0.72)' : 'none' }}>
        <g key={yeses} className="hop">
          <circle r="50" className="core" />
          <circle r="46" className="core-ring" />
          <Walrus mood={mood} size={76} x={-38} y={-38} />
        </g>
      </g>
      {placed.map((p, i) => {
        const at = snap ? p.row : { x: p.x, y: p.y, rot: p.rot };
        const vars = { transform: `translate(${at.x}px, ${at.y}px)`, '--fx': px(p.x), '--fy': px(p.y), '--rot': `${p.rot}deg`, '--i': i } as CSSProperties;
        return (
          <g key={`s${i}`} className={`seat ${p.seat.state}`} style={vars}>
            <g className="pop">
              <g className="turn" style={{ transform: `rotate(${at.rot}deg)` }}>
                <path d={PIECE} transform="scale(0.62)" className="base" />
                <path d={PIECE} transform="scale(0.62)" className="shape" />
              </g>
              {p.seat.state === 'open' ? (
                <path d="M -6 0 H 6 M 0 -6 V 6" className="plus" />
              ) : (
                <g clipPath={`url(#${clip})`} className="face">
                  <circle r="19" className="porthole" />
                  <Buddy decorative {...p.who} mood={snap ? 'cheer' : FACES[p.seat.state]} size={46} x={-23} y={-21} />
                </g>
              )}
            </g>
            <Badge state={p.seat.state} />
            <text y={!snap && p.y < -20 ? -34 : 44} className="word">{snap ? p.display : p.label}</text>
          </g>
        );
      })}
      {snap && placed.slice(1).map((_, i) => (
        <g key={`j${i}`} transform={`translate(${Math.round((i + 0.5 - (n - 1) / 2) * STEP)} ${ROW - 44})`}>
          <path d={SPARK} className="joint" style={{ '--i': i } as CSSProperties} />
        </g>
      ))}
    </svg>
  );
}

// --- Pen doodles ---------------------------------------------------------------------------------------------------

// A quick pen circle around whatever you just picked, drawn on as you tap.
export function Scribble() {
  return (
    <svg className="scribble" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
      <path d="M 56 7 C 24 3 5 24 7 51 C 9 80 33 95 58 93 C 85 90 97 66 93 41 C 89 18 68 6 42 10 C 35 11 29 14 24 18" pathLength={1} />
    </svg>
  );
}

const BITS = [
  <path d={SPARK} />,
  <path d="M -7 3 Q -3.5 -5 0 2 Q 3.5 9 7 1" fill="none" stroke="currentColor" strokeWidth="2.6" strokeLinecap="round" />,
  <circle r="4.5" />,
  <path d="M -5 4.5 L 0 -5.5 L 5 4.5 Z" />,
  <path d={HEART} transform="scale(0.55)" />,
];
const CONFETTI = ['var(--teal)', '#F9A8D4', '#FCD34D', 'var(--violet)', '#7DD3FC'];

// Paper bits for the big moments. Plays once, then gets out of the way.
export function Confetti() {
  const [on, setOn] = useState(true);
  useEffect(() => {
    const t = setTimeout(() => setOn(false), 3200);
    return () => clearTimeout(t);
  }, []);
  if (!on) return null;
  return (
    <div className="confetti" aria-hidden="true">
      {Array.from({ length: 22 }, (_, i) => (
        <svg
          key={i}
          viewBox="-9 -9 18 18"
          fill="currentColor"
          style={{
            '--x': `${(i * 41 + 7) % 100}%`, '--d': `${(i % 7) * 90}ms`, '--s': `${((i * 29) % 60) - 30}px`,
            '--r': `${(i % 2 ? 1 : -1) * (160 + ((i * 37) % 200))}deg`, color: CONFETTI[i % CONFETTI.length],
          } as CSSProperties}
        >
          {BITS[i % BITS.length]}
        </svg>
      ))}
    </div>
  );
}

// --- Small parts -------------------------------------------------------------------------------------------------

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

// A handwritten label + title, the header every card starts with.
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
      <div className="avatar xl"><Pebble mood={offline ? 'sleepy' : 'thinking'} size={164} /></div>
      <h2>{offline ? 'No signal right now.' : 'Hmm, I lost the connection. Trying again…'}</h2>
      <p className="muted">{offline ? 'I’ll keep trying and pick up where we left off. Your plans are saved.' : 'Nothing was lost. You don’t need to do anything.'}</p>
      <div className="progress"><div /></div>
      <button className="btn primary" onClick={onRetry}>Try now</button>
    </section>
  );
}

// Where a new group meets first, and the sign that lets strangers find each other in a crowd. `address`, `lat`
// and `lng` place it on a map (lat/lng are missing when the agents service couldn't look the place up).
export type Meet = { spot: string; sign: string; emoji: string; color: string; address?: string; lat?: number | null; lng?: number | null };

// Directions in the phone's own map app: Apple Maps on iPhone/iPad, the map-app picker on Android (geo:), Google
// Maps in a desktop browser. Coordinates when we have them, else the address text.
export function directionsUrl(meet: Meet) {
  const pin = meet.lat != null && meet.lng != null ? `${meet.lat},${meet.lng}` : '';
  const where = encodeURIComponent(meet.address || meet.spot);
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) {
    return `https://maps.apple.com/?daddr=${pin || where}&q=${where}`;
  }
  if (/Android/.test(ua)) return pin ? `geo:${pin}?q=${pin}(${where})` : `geo:0,0?q=${where}`;
  return `https://www.google.com/maps/dir/?api=1&destination=${pin || where}`;
}

// A small OpenStreetMap with a pin (their official embed: no key). Pan and zoom right in the card.
function MeetMap({ lat, lng, label }: { lat: number; lng: number; label: string }) {
  const d = 0.004; // about 400 m each way: the venue and the streets around it
  const src = `https://www.openstreetmap.org/export/embed.html?bbox=${lng - d},${lat - d},${lng + d},${lat + d}&layer=mapnik&marker=${lat},${lng}`;
  return <iframe className="meet-map" src={src} title={`Map of ${label}`} loading="lazy" referrerPolicy="no-referrer" />;
}

export function MeetCard({ meet, onShow }: { meet: Meet; onShow: () => void }) {
  const directions = directionsUrl(meet);
  return (
    <section className="card meet">
      <CardHead eyebrow="Meet here first" title={meet.spot} />
      {meet.address && <p className="meet-address"><Icon name="pin" size={16} /><span>{meet.address}</span></p>}
      {meet.lat != null && meet.lng != null && <MeetMap lat={meet.lat} lng={meet.lng} label={meet.address || meet.spot} />}
      <div className="sign-row">
        <span className="sign-chip" style={{ background: meet.color }}>{meet.emoji}</span>
        <div>
          <b>{meet.sign}</b>
          <span>Your group's sign. Look for phones showing it, and hold yours up too.</span>
        </div>
      </div>
      <div className="btn-row">
        <a className="btn" href={directions} target={directions.startsWith('http') ? '_blank' : undefined} rel="noopener">
          <Icon name="pin" size={18} />Directions
        </a>
        <button className="btn primary wide" onClick={onShow}>Show my sign</button>
      </div>
    </section>
  );
}

// The sign, full screen and bright, to hold up. Keeps the screen awake while it's open; tap anywhere to close.
export function SignScreen({ meet, onClose }: { meet: Meet; onClose: () => void }) {
  useEffect(() => {
    let lock: { release: () => Promise<void> } | undefined;
    const nav = navigator as Navigator & { wakeLock?: { request: (t: 'screen') => Promise<{ release: () => Promise<void> }> } };
    nav.wakeLock?.request('screen').then(l => (lock = l)).catch(() => {});
    return () => {
      lock?.release().catch(() => {});
    };
  }, []);
  return (
    <button className="sign-screen" style={{ background: meet.color }} onClick={onClose} aria-label={`${meet.sign}. Tap to close`}>
      <span className="sign-emoji" aria-hidden="true">{meet.emoji}</span>
      <b>{meet.sign}</b>
      <span>Meet at {meet.spot}</span>
      <small>Tap to close</small>
    </button>
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
