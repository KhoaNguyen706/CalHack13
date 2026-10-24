# Coordinator

The one Python service every agent talks to. Agents never talk to each other directly.
Gateway → broker → hybrid matching → approval → challenge hand-off, with every step streamed to the negotiation log.

```bash
cd coordinator
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # macOS/Linux: .venv/bin/pip
.venv/Scripts/python -m uvicorn coordinator.main:app --reload             # http://localhost:8000/docs
.venv/Scripts/python -m pytest -q
```

Demo in four calls: `POST /demo/seed` → `POST /matching/run` → `POST /matches/{id}/accept` ×3 → watch `/ws/log`.

## Env

| Var | Default | What |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Enables the Claude match pick. Without it, matching uses the top rule score. |
| `MATCH_WITH_CLAUDE` | `1` | `0` forces rule-only matching (handy for rehearsals). |
| `MATCH_MODEL` | `claude-opus-5-5` | Model for the pick. |
| `CHALLENGE_DESIGNER_URL` | none | Agents team's challenge designer. Unset or failing → built-in Three-key demo round. |
| `REPLY_TIMEOUT_S` | `20` | How long the broker waits for an agent before counting "no". |
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
| POST | `/matches/{id}/accept` | `{"agent_id", "accept": true\|false}`. A decline returns everyone to the pool. |
| GET | `/matches/{id}` | Status, members, roles, challenge. |

Extra: `POST /matching/run`, `GET /matches`, `GET /agents[/{id}]`, `PUT /matches/{id}/challenge`,
`POST /matches/{id}/state` (`playing` → `done`), `POST /demo/seed`, `POST /demo/reset`, `GET /log`, `WS /ws/log`.

Check-in payload (`callback_url` is optional):

```json
{"agent_id": "linh", "free": "Sat 14:00-16:00", "interests": ["puzzles"], "skills": ["riddles"],
 "group_pref": "small", "area": "UC Berkeley", "callback_url": "https://my-agent/questions"}
```

**How an agent gets asked.** With `callback_url`, the coordinator POSTs `{"question_id", "agent_id", "question", "plan"}`
there; answer in the response body (`{"answer": "yes"}`) or later via `/reply`. Without it, poll `/questions`.
Seeded personas loaded by `/demo/seed` with no `callback_url` answer "yes" on their own.

Match states: `pending` → `confirmed` (3/3 accept) → `playing` → `done`, or `cancelled` on a decline.

## Matching (`coordinator/matching.py`)

1. **Code filters:** same area, ≥ 1 h overlap on the same day, nobody wants a `large` group.
2. **Code scores** every group of 3: role fit ×10 (riddles / map / camera, from skills + interests),
   +2 per shared interest (max 2), + overlap hours. Ties go to the longer overlap.
3. **Claude picks** one of the top 5 using `matching_rules.md`, assigning roles and a one-line reason for the log.
4. **Code validates** the pick (shortlisted group, one distinct role each). Invalid, refused or unreachable → top score.
5. **Broker** asks all 3 agents in parallel. A `counter` updates that agent's window and the overlap is re-checked.
   Anyone saying no is skipped for this run, and the next best group is tried.

## Negotiation log (`WS /ws/log`)

On connect: the full history, then live events. Each message:

```json
{"seq": 12, "ts": 1761264000.1, "kind": "approval", "text": "Linh ✓  Marcus ✓  Priya ✓  → CONFIRMED", "data": {"match_id": "a1b2c3d4"}}
```

`kind` is one of `coordinator`, `broker`, `match`, `approval`, `challenge`, `game`. Render as `[kind] text`;
`data` carries ids for the animation (members, roles, answer, match_id, accepted).

## Challenge designer hand-off

On confirm, the coordinator POSTs `{"match": <Match>, "members": [{"agent_id", "role", "skills", "interests"}]}` to
`CHALLENGE_DESIGNER_URL` and expects the game template back (`{"group": [...], "round": {"type": "split_riddle",
"keys": {<agent_id>: <key>, ...}, "answer", "unlock", "timer_min"}}`). `round.keys` must have one key per member.
The designer can instead push it with `PUT /matches/{id}/challenge`.

## Live game hand-off (SpacetimeDB)

After the challenge is attached, the coordinator calls the `start_round` reducer on the game database and the match
goes `playing`. It polls the board every 1.5 s, logs each `[game] <name>: we're here ✓`, and marks the match `done`
when the photo is in. Without `SPACETIME_TOKEN`, or if SpacetimeDB is down, the match stays `confirmed`.
See `game/README.md` for the module.

## MCP adapter (stretch)

`COORDINATOR_URL=http://localhost:8000 python -m coordinator.mcp_server` (stdio, or `--http 8765`).
Tools: `checkin`, `questions`, `reply`, `my_match`, `accept`. It wraps the HTTP API, so any MCP agent can join.
