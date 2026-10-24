# CalHack13

AI coordination layer: your agent finds you people to meet; the coordinator matches groups of 3 and runs a
cooperative challenge they can only win together. Design: `Cal Hacks 13 — AI Coordination Layer Final Design.md`.

| Folder | What |
| --- | --- |
| `coordinator/` | Python coordinator: gateway, broker, matching, approval, negotiation log, MCP adapter |
| `game/` | SpacetimeDB live game (module + phone game page) |

## Run everything (any OS, Docker)

Needs Docker Desktop (or Docker Engine + Compose).

```bash
cp .env.example .env     # add ANTHROPIC_API_KEY (ask a teammate); .env is never committed
docker compose up --build
```

| What | Where |
| --- | --- |
| Coordinator API | http://localhost:8000/docs |
| Negotiation log | `ws://localhost:8000/ws/log` (or `GET /log`) |
| Game page | http://localhost:5173/?match=&lt;match_id&gt;&agent=&lt;agent_id&gt; |
| SpacetimeDB | http://localhost:3000 |

Test the MVP on one laptop with two people (`test-mvp` branch, which matches pairs, `GROUP_SIZE=2`):

1. Open http://localhost:5173 in two windows. Log in as a different person in each, e.g. `anna` (Riddles) and
   `ben` (Finding the way), with overlapping free time.
2. In one window, tap **Find my group**. Both windows get the plan question; tap **Yes** in each within 20 s.
3. Tap **Accept** in both. Each window opens its game page: tap "We're here" in both, then submit a photo URL.

Don't run `/demo/seed` for this: the seeded bots would get paired with you. `GROUP_SIZE=3 docker compose up` brings back trios.

Try the demo with the seeded personas (needs `GROUP_SIZE=3`):

```bash
curl -X POST localhost:8000/demo/seed
curl -X POST localhost:8000/matching/run          # note the match_id of linh/marcus/priya
curl -X POST localhost:8000/matches/<id>/accept -H 'Content-Type: application/json' -d '{"agent_id":"linh"}'
#  …same for marcus and priya → round goes live
```

Open the game page three times (`agent=linh`, `agent=marcus`, `agent=priya`), tap "We're here" on each,
then submit a photo URL. The coordinator log ends with `challenge complete!`.

- Claude matching: put `ANTHROPIC_API_KEY` in `.env`. Without it, matching uses the rule score.
- Phones on the same Wi-Fi: `SPACETIME_PUBLIC_WS=ws://<laptop-ip>:3000 docker compose up`, then open `http://<laptop-ip>:5173/...`.
- Fresh start: `docker compose down -v`.
- Code changes need a rebuild: `docker compose up --build`.

## Deploy (Maincloud + Railway + Vercel)

**1. SpacetimeDB on Maincloud.** Install the CLI (Windows PowerShell: `iwr https://windows.spacetimedb.com -useb | iex`), then:

```bash
spacetime login                      # GitHub sign-in in the browser
cd game && npm run spacetime:publish # publishes "threekey" to maincloud
spacetime login show --token         # copy: the coordinator needs it to start rounds
```

**2. Coordinator on Railway.** New project → Deploy from GitHub repo → root directory `coordinator`
(it builds the Dockerfile). Add a volume mounted at `/data`, generate a public domain, and set:

| Variable | Value |
| --- | --- |
| `ANTHROPIC_API_KEY` | the team key (never commit it) |
| `SPACETIME_URL` | `https://maincloud.spacetimedb.com` |
| `SPACETIME_DB` | `threekey` |
| `SPACETIME_TOKEN` | output of `spacetime login show --token` |
| `COORDINATOR_DB` | `/data/coordinator.db` |
| `GROUP_SIZE` | `3` for the real demo, `2` to test with two people |

Check `https://<railway-domain>/health` shows `"claude": true, "spacetime": true`. Run one instance only:
open questions live in memory.

**3. Web app on Vercel.** New project → import the repo → root directory `game` (Vite is detected). Set before the
first build (Vite bakes them in; redeploy after changing them):

| Variable | Value |
| --- | --- |
| `VITE_COORDINATOR_URL` | `https://<railway-domain>` |
| `VITE_SPACETIMEDB_HOST` | `wss://maincloud.spacetimedb.com` |
| `VITE_SPACETIMEDB_DB_NAME` | `threekey` |

Open `https://<vercel-domain>` on phones; the live agents screen is `https://<vercel-domain>/?view=log`.
