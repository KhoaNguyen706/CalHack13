# Live game (SpacetimeDB)

Shared clue board for the Three-key hunt. The coordinator starts a round when a group is confirmed;
each phone opens `/?match=<match_id>&agent=<agent_id>`, sees **only its own key**, taps "We're here",
and all 3 boards update instantly. Once everyone is here, the photo step unlocks; the photo ends the round.

```
spacetimedb/src/index.ts   module: tables, reducers, my_key view (TypeScript)
src/App.tsx                phone game page (React + Vite), the starting point for the Next.js game page
src/module_bindings/       generated client bindings (npm run spacetime:generate after module changes)
```

## Run locally

```bash
spacetime start                                   # local server on :3000 (leave running)
cd game/spacetimedb && npm install && cd ..
npm install
npm run spacetime:publish:local                   # database "threekey"
npm run dev                                       # http://localhost:5173/?match=<id>&agent=linh
```

Then start the coordinator with the **publisher's** token, since only that identity may start rounds:

```bash
cd coordinator
SPACETIME_TOKEN=$(spacetime login show --token | grep -o 'eyJ[^ ]*') .venv/Scripts/python -m uvicorn coordinator.main:app
```

Module tests: `cd game/spacetimedb && npx vitest run`.

## Data model

| Table | Public | What |
| --- | --- | --- |
| `round` | yes | `match_id`, `kind`, `timer_min`, `started_at`, `state` (`playing` → `unlocked` → `done`), `answer` (revealed when done) |
| `seat` | yes | one per player: `agent_id`, `role`, `player` (identity of the phone that joined), `checked_in` |
| `photo` | yes | group photo URL for the round |
| `seat_key`, `round_secret`, `coordinator` | no | the keys, the answer, and who may start rounds |
| `my_key` (view) | per phone | the key for the seat this phone holds, and nothing else |

| Reducer | Who | What |
| --- | --- | --- |
| `start_round(matchId, kind, timerMin, answer, seats)` | coordinator | (re)starts a round; called over HTTP by the Python coordinator |
| `join(matchId, agentId)` | phone | claim a seat; first phone wins, the same phone can rejoin |
| `check_in(matchId)` | phone | "We're here"; the last one unlocks the photo step |
| `submit_photo(matchId, url)` | phone | needs `unlocked`; sets `done` and reveals the answer |

The coordinator polls `round` and `seat` and mirrors check-ins and completion onto the negotiation log
(`[game] Linh: we're here ✓` … `[game] photo in · challenge complete!`), then marks the match `done`.

Photo upload itself (Supabase Storage / Cloudinary) belongs to the web app; `submit_photo` takes the resulting URL.

## Deploy (Maincloud)

`spacetime login`, then `npm run spacetime:publish`, and point the web app at it with
`VITE_SPACETIMEDB_HOST=wss://maincloud.spacetimedb.com` and `VITE_SPACETIMEDB_DB_NAME=threekey`.
The coordinator needs `SPACETIME_URL=https://maincloud.spacetimedb.com` and the token of the identity that published.
