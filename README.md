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

- Claude matching: set `ANTHROPIC_API_KEY` in your shell before `docker compose up`. Without it, matching uses the rule score.
- Phones on the same Wi-Fi: `SPACETIME_PUBLIC_WS=ws://<laptop-ip>:3000 docker compose up`, then open `http://<laptop-ip>:5173/...`.
- Fresh start: `docker compose down -v`.
- Code changes need a rebuild: `docker compose up --build`.
