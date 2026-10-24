# Agents and AI

We build the coordination layer, not personal agents. In real life, each person's own personal agent (Muse,
Grok bot, Dots) already knows them and plugs into the coordinator. For the demo, this service stands in for
those agents and adds the AI parts of the layer:

- **Sample personal agents.** A chatbot per real person, loaded with a **personal context**: fake personal
  information a real personal agent would already have (`seeds/personal_contexts.json`). It answers the
  coordinator's invitations from that context and its memories.
- **Challenge designer.** Claude invents an outdoor challenge for whoever joined: photo hunts, checkpoint runs,
  "find this spot" with a scraped reference photo, a question at a sponsor booth. Every step is proven by one photo.
- **Photo checks.** Claude looks at each photo and says whether it passes the step.
- **Place info (Apify)** and **memory (Moss)**.

```bash
cd agents
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                               # add your keys
.venv/bin/python -m uvicorn agents.main:app --port 8100 --reload   # http://localhost:8100/docs
.venv/bin/python -m pytest -q
```

Or the whole stack with Docker from the repo root (`docker compose up --build`); see the root README.
Run the coordinator next to it, pointed at the designer:

```bash
cd coordinator
CHALLENGE_DESIGNER_URL=http://localhost:8100/challenge .venv/bin/python -m uvicorn coordinator.main:app --port 8000
```

## The demo flow

1. Each person signs in (SpacetimeDB) and gets a sample personal context, picked or the next free one. Their
   agent checks in right away, like a real personal agent, so others can invite them.
2. One of them starts a challenge and picks the size (3-5; never fewer than 3 strangers), in the chat or with Start.
3. The coordinator invites other people's agents. Each agent answers from its context ("can do 90 minutes");
   invited people tap Accept. The host hears "Jess joined (2 of 4)".
4. When everyone's in, Claude invents the challenge; each phone shows the steps and that person's part.
5. The group plays; each step's photo goes to the photo check. Then the end screen: rate, meet again?, share?

Fewer real people than you want for a rehearsal? `POST /contexts/seed?shift_to_now=true` checks the unclaimed
contexts in as stand-in agents, which accept on their own.

Start calls the coordinator's `POST /challenges` (start a challenge for N people, invite the rest). Against an older
coordinator without that route, it falls back to `/matching/run`.

## Env

| Var | Default | What |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Claude for agents, chat, designer and photo checks. Without it: rule answers, a built-in color hunt, photos always pass. |
| `APIFY_TOKEN` | none | Scrapes place info and reference photos. Without it: built-in hackathon facts. |
| `MOSS_PROJECT_ID`, `MOSS_PROJECT_KEY` | none | Semantic memory recall. Without them: newest memories from SQLite. |
| `MOSS_INDEX` | `agent-memory` | Use a fresh name for the demo, so test memories don't carry over. |
| `COORDINATOR_URL` | `http://localhost:8000` | |
| `AGENTS_PUBLIC_URL` | `http://localhost:8100` | Where the coordinator reaches us (callbacks). Set to the deployed URL. |
| `EVENT_NAME`, `EVENT_LOCATION`, `EVENT_CITY` | Cal Hacks 13, Palace of Fine Arts, San Francisco | For scraping and challenges. |
| `EVENT_AREA` | `Palace of Fine Arts` | Area every agent checks in with. The coordinator only groups one area. |
| `EVENT_TZ` | `America/Los_Angeles` | "Free from now" windows and `shift_to_now`. |
| `MEETING_POINT` | none | Where every new group meets first, e.g. `the rotunda steps, facing the lagoon`. Unset: Claude picks a spot from the place facts and verified booths, else the venue's main entrance. |
| `AGENT_MODEL`, `DESIGNER_MODEL` | `claude-opus-5-5` | Agents and chat; the challenge designer. |
| `AGENTS_WITH_CLAUDE` (or `AGENT_WITH_CLAUDE`) | `1` | `0` forces rule decisions and the built-in challenge (rehearsals). |
| `STAND_IN_AUTO_ACCEPT` | `1` | Stand-in agents accept invitations after a short, staggered delay. |

## For the frontend

| Method | Endpoint | Body | What |
| --- | --- | --- | --- |
| GET | `/contexts` | | Sample personal contexts for a picker: `id`, `interests`, `skills`, `style`, `bio`, `taken_by`. |
| POST | `/users/login` | `{"identity", "name", "context_id"?}` | After SpacetimeDB sign-in. Same identity, same agent. Returns status. |
| GET | `/users/{id}` | | Poll every 1-2 s. `transcript`, `match`, `is_host`, `needs_accept`, `challenge`, `my_part`, `progress`. |
| POST | `/chat/{id}` | `{"message"}` | One chat turn. Returns `reply` + status. |
| POST | `/users/{id}/start` | `{"size": 3-5}` (default 3) | Start a challenge; the coordinator invites the rest. |
| POST | `/users/{id}/accept` | `{"accept": true\|false}` | Answer an invitation. |
| POST | `/challenges/{match_id}/check` | `{"step": "s1", "agent_id", "image_url"}` or `{..., "image_base64", "media_type"}` | Photo check: `passed`, `reason`, and the whole progress. Keep images under 5 MB. |
| GET | `/challenges/{match_id}/progress` | | `steps` with `passed`, `done`. |
| POST | `/feedback` | `{"match_id", "agent_id", "rating", "meet_again", "share_ok", "comment"}` | End screen. The rating becomes a memory. |
| GET | `/feedback/{match_id}?agent_id=` | | `waiting_for`, `mutual_meet_again`, `my_matches` (only if you said yes), `share_ok` (all said OK). |
| GET | `/stats` | | `"X of Y groups wanted to meet again"`. |

Status `challenge` is what every player sees: `title`, `goal`, `connection_hook`, `steps` (`id`, `task`,
`check`), `reference_photo`, `timer_min`. `my_part` is that person's own job; other players' parts stay hidden.
The same parts reach the SpacetimeDB board as each seat's key. Transcript lines have `role`: `user`, `agent`
or `update` (invitations, joins, steps passed).

The coordinator calls `POST /agents/{id}/question` (the `callback_url` of every agent we run) and
`POST /challenge` (`CHALLENGE_DESIGNER_URL`). Also: `POST /contexts/seed`, `GET /place`, `POST /place/refresh`,
`GET /health`. The first agents service's paths still work: `GET /personas`, `POST /personas/checkin-all`
(= `/contexts/seed`), `POST /personas/{id}/ask`.

## How it works

- **Personal agents** (`personal.py`). Invited to "Linh's 4-person challenge, Sat 2–4pm", Claude answers yes /
  no / counter from the personal context (profile, `style`, memories) with a short reason for the log. Code then
  checks it: nobody says yes while not taking plans, and a counter must sit inside the person's free time and
  still overlap the plan by an hour. A broken answer, a refusal, an error or 12 s without one → the rules:
  1. Not open to invites → no.
  2. Less than an hour of the plan fits → no.
  3. Fits, but longer than `max_minutes` → counter with the first `max_minutes`.
  4. Only part of the plan fits → counter with that part.
  5. Otherwise → yes.
- **Personal contexts** (`seeds/personal_contexts.json`). Coarse check-in fields plus agent-side ones that never
  reach the coordinator: `bio`, `style` (how this person decides), `max_minutes` (longest block they'll commit
  to), `open_to_invites`, and seed `memories`. Tomas isn't taking plans; Marcus, Omar and Dev keep it to 90
  minutes; Kenji and Bao to an hour.
- **Memory** (`memory.py`). Every memory is in SQLite; Moss finds the relevant ones. End-screen ratings become
  memories, so the next answer changes. Moss writes run in a worker process (they block for seconds otherwise).
- **Sample chatbot** (`chat.py`). Claude with tools: `start_challenge(size)`, `respond_to_invite`,
  `my_challenge`, `save_profile`. Coordinator news reaches it as `[Update: …]` lines in the next turn.
- **Challenge designer** (`designer.py`). Claude invents the challenge from the players' first names and
  interests, the place facts, the scraped photos and the sponsor booths, in the style of our examples (find this
  spot, checkpoint run, color hunt, personal theme hunt, …). Code checks one part per player, 1-6 steps with a
  photo check each, and that a reference photo really was scraped. Anything wrong → a built-in color hunt.
- **Sponsor booths** (`data/booths.json`, `data/sponsors.json`, `sponsors.py`). What each sponsor does and where
  its booth is. Claude only sends people to booths marked `"verified": true`.
- **Photo checks** (`vision.py`). Photos are downloaded and sent inline. Generous about angles and blur, strict
  about strangers' faces. A photo that can't be opened fails; without Claude, or if Claude errors, it counts.
- **Place info** (`place.py`). Apify's RAG Web Browser searches the place, the event and this month; Claude
  boils the pages down to facts, and page photos become reference photos. Cached per hour in `cache/`.
- **Watcher** (`service.py`). Every 2 s: stand-ins accept invitations; real people get chat updates.

### Before the demo

- Walk the venue and fix each booth's `location` in `data/booths.json`, then set `"verified": true`. Only Apify
  ("near the snacks") is confirmed; the Anthropic, SpacetimeDB and Cartesia locations are placeholders.
- `data/sponsors.json` is hand-written for now.
- Set `MOSS_INDEX` to a fresh name.

Not done yet: Cartesia voices (stretch), and publishing our own Apify Actor for the Best Actor prize
(we call `apify/rag-web-browser`).
