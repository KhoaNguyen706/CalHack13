# Krew

Cal Hacks 13 project (repo: CalHack13).

AI coordination layer: your agent finds you people to meet; you start a challenge for 3 to 5 people and the
coordinator invites other people's agents until it's full, then runs a cooperative challenge they can only win
together. Design: `Cal Hacks 13 — AI Coordination Layer Final Design.md`.

| Folder | What |
| --- | --- |
| `coordinator/` | Python coordinator: gateway, broker, matching, approval, negotiation log, MCP adapter |
| `agents/` | Sample personal agents (chatbot + personal contexts), the AI challenge designer, photo checks |
| `game/` | SpacetimeDB live game (module + phone game page) |

## Run everything (any OS, Docker)

Needs Docker Desktop (or Docker Engine + Compose).

```bash
cp .env.example .env     # add ANTHROPIC_API_KEY (+ APIFY_TOKEN, MOSS_* if you have them); .env is never committed
docker compose up --build
```

| What | Where |
| --- | --- |
| Coordinator API | http://localhost:8000/docs |
| Negotiation log | `ws://localhost:8000/ws/log` (or `GET /log`) |
| Agents API | http://localhost:8100/docs |
| Game page | http://localhost:5173/?match=&lt;match_id&gt;&agent=&lt;agent_id&gt; |
| SpacetimeDB | http://localhost:3000 |

Test the MVP on one laptop with two people: start with `GROUP_SIZE=2 docker compose up --build` so it matches pairs.

1. Open http://localhost:5173 in two windows. Log in as a different person in each, e.g. `anna` (Riddles) and
   `ben` (Finding the way), with overlapping free time.
2. In one window, tap **Find my group**. Both windows get the plan question; tap **Yes** in each within 20 s.
3. Tap **Accept** in both. Each window opens its game page: tap "We're here" in both, then submit a photo URL.

Don't seed personas for this: the seeded bots would get paired with you.

Try a host-led challenge with the seeded personas (they answer yes on their own):

```bash
curl -X POST localhost:8000/demo/seed
curl -X POST localhost:8000/challenges -H 'Content-Type: application/json' -d '{"agent_id":"linh","size":4}'
# the log shows the invites; then accept for each invitee:
# curl -X POST localhost:8000/matches/<id>/accept -H 'Content-Type: application/json' -d '{"agent_id":"marcus"}'
```

Or split the whole pool into groups with `/matching/run` (testing, default `GROUP_SIZE=3`):

```bash
curl -X POST localhost:8100/personas/checkin-all  # live persona agents (or localhost:8000/demo/seed: auto-yes)
curl -X POST localhost:8000/matching/run          # note the match_id of linh/marcus/priya
# The personas' agents accept on their own a few seconds later → round goes live.
# To accept by hand instead, run with STAND_IN_AUTO_ACCEPT=0 and:
# curl -X POST localhost:8000/matches/<id>/accept -H 'Content-Type: application/json' -d '{"agent_id":"linh"}'
```

Open the game page three times (`agent=linh`, `agent=marcus`, `agent=priya`), tap "We're here" on each,
then submit a photo URL. The coordinator log ends with `challenge complete!`.

- Claude matching: put `ANTHROPIC_API_KEY` in `.env`. Without it, matching uses the rule score.
- Phones: the page reaches the coordinator (`/api`) and SpacetimeDB (`/stdb`) through its own address, so only port
  5173 matters. Same Wi-Fi: open `http://<laptop-ip>:5173`. Event Wi-Fi usually blocks that; use a public link instead:
  `docker compose --profile tunnel up -d`, then `docker compose logs tunnel | grep trycloudflare` for the https URL.
  Anyone with the link can use the app (and `/api/demo/reset`), so stop it when done: `docker compose stop tunnel`.
- Fresh start: `docker compose down -v`.
- Code changes need a rebuild: `docker compose up --build`.

## Deploy (Maincloud + Heroku + Vercel)

**1. SpacetimeDB on Maincloud.** Install the CLI (Windows PowerShell: `iwr https://windows.spacetimedb.com -useb | iex`), then:

```bash
spacetime login                      # GitHub sign-in in the browser
cd game && npm run spacetime:publish # publishes "threekey" to maincloud
spacetime login show --token         # copy: the coordinator needs it to start rounds
```

**2. Coordinator on Heroku.** Create an app (e.g. `calhacks-coordinator`). Deploy tab → connect the GitHub repo,
branch `main`. Settings → Buildpacks: add `https://github.com/lstoll/heroku-buildpack-monorepo`, then `heroku/python`
(in that order). Resources: one **Basic** dyno (Eco sleeps, and the coordinator keeps open invites in memory: run one
dyno only). Config Vars:

| Variable | Value |
| --- | --- |
| `APP_BASE` | `coordinator` (the monorepo buildpack builds this folder) |
| `ANTHROPIC_API_KEY` | the team key (never commit it) |
| `SPACETIME_URL` | `https://maincloud.spacetimedb.com` |
| `SPACETIME_DB` | `threekey` |
| `SPACETIME_TOKEN` | output of `spacetime login show --token` (the identity that published `threekey`) |
| `CHALLENGE_DESIGNER_URL` | `https://<agents-app>.herokuapp.com/challenge` (step 3). Unset → the built-in demo round. |
| `GROUP_SIZE` | optional: `3`, or `2` to test `/matching/run` with two people |

Deploy branch, then check `https://<coordinator-app>.herokuapp.com/health` shows `"claude": true, "spacetime": true`.

**3. Agents service on Heroku.** A second app (e.g. `calhacks-agents`), same repo, same two buildpacks, one Basic
dyno. Config Vars:

| Variable | Value |
| --- | --- |
| `APP_BASE` | `agents` |
| `ANTHROPIC_API_KEY` | the team key |
| `COORDINATOR_URL` | `https://<coordinator-app>.herokuapp.com` |
| `AGENTS_PUBLIC_URL` | `https://<agents-app>.herokuapp.com` (the coordinator calls agents back here) |
| `APIFY_TOKEN`, `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY` | optional |

Seed the demo people: `curl -X POST "https://<agents-app>.herokuapp.com/contexts/seed?shift_to_now=true"`.

Heroku's disk is wiped when a dyno restarts (at least once a day), so both SQLite files start empty again: seed
again after a restart. Hosts with volumes (Railway: mount `/data`, set `COORDINATOR_DB=/data/coordinator.db` and
`AGENTS_DB=/data/agents.db`) keep them; the Dockerfiles work there unchanged.

**4. Web app on Vercel.** New project → import the repo → root directory `game` (Vite is detected). Set before the
first build (Vite bakes them in; redeploy after changing them):

| Variable | Value |
| --- | --- |
| `VITE_COORDINATOR_URL` | `https://<coordinator-app>.herokuapp.com` |
| `VITE_SPACETIMEDB_HOST` | `wss://maincloud.spacetimedb.com` |
| `VITE_SPACETIMEDB_DB_NAME` | `threekey` |

Open `https://<vercel-domain>` on phones; the live agents screen is `https://<vercel-domain>/?view=log`.
