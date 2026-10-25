# Coordinator

The one Python service every agent talks to. Agents never talk to each other directly.
Gateway → host-led invites (broker + hybrid matching) → approval → challenge hand-off, with every step streamed to the
negotiation log.

```bash
cd coordinator
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn coordinator.main:app --reload             # http://localhost:8000/docs
.venv/Scripts/python -m pytest -q
```

Demo: `POST /demo/seed` → `POST /challenges {"agent_id": "linh", "size": 4}` → `POST /matches/{id}/accept` for each
invitee → watch `/ws/log`.

## Env

| Var | Default | What |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Enables the Claude match pick. Without it, matching uses the top rule score. |
| `MATCH_WITH_CLAUDE` | `1` | `0` forces rule-only matching (handy for rehearsals). |
| `MATCH_MODEL` | `claude-opus-5-5` | Model for the pick. |
| `CHALLENGE_DESIGNER_URL` | none | Agents team's challenge designer. Unset or failing → a built-in round whose hints point to the Apify booth. |
| `REPLY_TIMEOUT_S` | `20` | How long the broker waits for an agent before counting "no". |
| `GROUP_SIZE` | `3` | Only for `/matching/run` (testing). With `POST /challenges` the host picks the size. |
| `COORDINATOR_DB` | `coordinator.db` | SQLite file. Agents and matches survive restarts. |
| `SPACETIME_TOKEN` | none | Token of the identity that published `game/spacetimedb`. Enables the live board. |
| `SPACETIME_URL` | `http://127.0.0.1:3000` | SpacetimeDB server. |
| `SPACETIME_DB` | `threekey` | Game database name. |

## Agent interface (frozen)

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/agents/checkin` | Coarse availability + profile. Unknown fields (names, exact location) are dropped. |
| POST | `/agents/{id}/ask` | Coordinator asks an agent a question; waits for the reply. |
| GET | `/agents/{id}/questions` | Open questions, for agents without a `callback_url` (the web chat polls this). |
| POST | `/agents/{id}/reply` | `{"question_id", "answer": "yes"\|"no"\|"counter", "free"?}` |
| POST | `/challenges` | `{"agent_id", "size": 3-5}`. The host must be checked in and free (409). Returns the match in `inviting`. |
| POST | `/matches/{id}/accept` | `{"agent_id", "accept": true\|false}`. Allowed while `inviting` or `pending`. |
| GET | `/matches/{id}` | State, host, size, members, accepted, invited, declined, window, challenge. |

Extra: `POST /challenges/{id}/start` (start with whoever accepted, at least 3), `POST /challenges/{id}/cancel`,
`POST /matching/run` (testing), `GET /matches`, `GET /agents[/{id}]`, `PUT /matches/{id}/challenge`,
`POST /matches/{id}/state` (`playing` → `done`), `POST /demo/seed`, `POST /demo/reset`, `GET /log`, `WS /ws/log`.

Check-in payload (`callback_url` is optional):

```json
{"agent_id": "linh", "free": "Sat 14:00-16:00", "interests": ["puzzles"], "skills": ["riddles"],
 "group_pref": "small", "area": "UC Berkeley", "callback_url": "https://my-agent/questions"}
```

**How an agent gets asked.** With `callback_url`, the coordinator POSTs `{"question_id", "agent_id", "question", "plan"}`
there; answer in the response body (`{"answer": "yes"}`) or later via `/reply`. Without it, poll `/questions`.
Seeded personas loaded by `/demo/seed` with no `callback_url` answer "yes" on their own.

Match states: `inviting` (seats open) → `pending` (all seats filled, waiting for Accepts) → `confirmed` (N/N accepted)
→ `playing` → `done`, or `cancelled`. Groups from `/matching/run` start at `pending`.

## Host-led challenges

1. **Start:** `POST /challenges` seats the host (already accepted) and returns right away. Invites run in the background.
2. **Candidates:** checked in, free (not in a group, not answering another question), same area, at least 1 h shared
   with everyone already in, never invited to this match before. Nobody who wants a `large` group joins a group of 3.
3. **Rank:** shared interests with the group (max 2, ×2), skills the group lacks (max 2, ×3), plus overlap hours.
4. **Claude picks** the `size - 1` (or however many seats are open) invitees from the top of the ranking using
   `matching_rules.md`; code checks the pick (right count, all shortlisted). Invalid or unreachable → top scores.
5. **Invite** them in parallel. Question: `"Linh wants a 4-person challenge in UC Berkeley, Sat 2:00–4:00pm. Want to
   join?"`, plan `{"host", "size", "area", "window", "activity"}` (no roles). A yes takes a seat; a counter takes a
   seat if the group still shares an hour (the group's window shrinks to fit). No, a timeout or a counter that breaks
   the overlap → `declined`, and the next best is invited.
6. **Full:** state `pending`. Confirmed once everyone has accepted.
7. **An invitee declines:** their seat opens, the state goes back to `inviting` and the next candidate is invited.
   The host declining cancels the challenge.
8. **Nobody left:** the log says `2 of 4 joined`. `POST /challenges/{id}/start` starts with whoever accepted (at least
   3), or `POST /challenges/{id}/cancel` releases everyone.

## Group matching for testing (`POST /matching/run`)

1. **Code filters:** same area, ≥ 1 h overlap on the same day, nobody wants a `large` group.
2. **Code scores** every group of 3: role fit ×10 (riddles / map / camera, from skills + interests),
   +2 per shared interest (max 2), + overlap hours. Ties go to the longer overlap.
3. **Claude picks** one of the top 5 using `group_rules.md`, assigning roles and a one-line reason for the log.
4. **Code validates** the pick (shortlisted group, one distinct role each). Invalid, refused or unreachable → top score.
5. **Broker** asks all 3 agents in parallel. A `counter` updates that agent's window and the overlap is re-checked.
   Anyone saying no is skipped for this run, and the next best group is tried.

## Negotiation log (`WS /ws/log`)

On connect: the full history, then live events. Each message:

```json
{"seq": 12, "ts": 1761264000.1, "kind": "approval", "text": "Linh ✓  Marcus ✓  Priya ✓  → CONFIRMED", "data": {"match_id": "a1b2c3d4"}}
```

`kind` is one of `coordinator`, `broker`, `match`, `approval`, `challenge`, `game`. Render as `[kind] text`;
`data` carries ids for the animation (members, roles, answer, match_id, accepted; for host-led challenges also
host, invited, joined, left). Broker lines end with the agent's own note: `[broker] Diego: no — busy then`.

Host-led example:

```
[challenge] Linh wants a 4-person challenge · UC Berkeley · Sat 2:00–4:00pm
[match] inviting Marcus, Priya, Diego
[broker] Diego: no — busy then
[match] inviting Hana
[match] all 4 seats filled · waiting for 1/4 approvals
[approval] Linh ✓  Marcus ✓  Priya ✓  Hana ✓  → CONFIRMED
```

## Challenge designer hand-off

On confirm, the coordinator POSTs `{"match": <Match>, "members": [{"agent_id", "role", "skills", "interests"}]}` (`role`
is `"player"` for host-led groups) to
`CHALLENGE_DESIGNER_URL` and expects the game template back (`{"group": [...], "round": {"type": "spot_and_pose",
"keys": {<agent_id>: <word>, ...}, "answer", "unlock", "timer_min"}}`). `round.keys` must have one key per member. Other
round fields (`title`, `goal`, `steps`, `pose`, `pieces`, …) pass through untouched. When someone leaves, their
key moves to `round.shared`, so the rest can still solve it.
The designer can instead push it with `PUT /matches/{id}/challenge`.

## Live game hand-off (SpacetimeDB)

After the challenge is attached, the coordinator calls the `start_round` reducer on the game database and the match
goes `playing`. It polls the board every 1.5 s, logs each `[game] <name>: we're here ✓`, and marks the match `done`
when the photo is in. Without `SPACETIME_TOKEN`, or if SpacetimeDB is down, the match stays `confirmed`.
See `game/README.md` for the module.

## Any agent joins over MCP

The coordinator serves an MCP endpoint at `/mcp` (streamable HTTP), so any MCP agent can join the live event: add
`https://<coordinator>/mcp` as a custom connector in Claude, then ask it to find you a group.

Tools: `event` (the area and a free window starting now), `checkin` (returns your agent id), `start_challenge`,
`questions`, `reply`, `my_match`, `accept`. Outside agents are guests: each acts only for the id `checkin` gave it
(never for people in the app), sees others as "Agent A, B…" until everyone accepts, and gets only its own part of the
challenge. At most 30 guests at a time. Set `APP_URL` (the web app) so `my_match` hands back `match_url` as soon as a guest is in a group: the page
shows the group filling up with Accept / Not now, then turns into the game.
`checkin` takes an optional `via` (the agent's own app), so the log reads `Khoa checked in via Meta Muse`.

**Meta Muse** has no menu for MCP servers; it builds integrations from a description. Tell it: *"Set up Krew from
https://&lt;coordinator&gt;/connect/muse"*. That page is the setup it needs: the `/mcp` URL and the steps.

Standalone, next to any coordinator: `COORDINATOR_URL=http://localhost:8000 python -m coordinator.mcp_server`
(stdio, or `--http 8765`).
