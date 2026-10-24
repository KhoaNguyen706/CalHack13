# Agents

The personal agents behind the coordinator. Every agent speaks the coordinator's frozen interface
(`coordinator/README.md`); this service never talks to another agent directly.

```bash
cd agents
python -m venv .venv
source .venv/bin/activate                   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn agents.main:app --port 8100   # http://localhost:8100/docs
python -m pytest -q
```

Demo with live agents, instead of the coordinator's auto-yes `/demo/seed`:

```bash
curl -X POST localhost:8100/personas/checkin-all   # 20 personas check in with a callback_url
curl -X POST localhost:8000/matching/run           # the log shows real yes / no / counter replies
# accept ×3 → the coordinator calls POST /challenge (CHALLENGE_DESIGNER_URL) for the round
```

## Env

| Var | Default | What |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | none | Lets Claude decide for each persona. Without it, agents decide with rules. |
| `AGENT_WITH_CLAUDE` | `1` | `0` forces rule decisions (handy for rehearsals). |
| `AGENT_MODEL` | `claude-opus-5-5` | Model for persona decisions. |
| `DESIGNER_MODEL` | `claude-opus-5-5` | Model for the challenge designer. |
| `COORDINATOR_URL` | `http://localhost:8000` | Where personas check in. |
| `AGENTS_PUBLIC_URL` | `http://localhost:8100` | How the coordinator reaches this service for callbacks. |

## Persona agents

`personas/<agent_id>.json` holds the coarse check-in fields plus three agent-side fields that never reach the
coordinator:

| Field | What |
| --- | --- |
| `style` | One line on how this person decides. Claude reads it. |
| `max_minutes` | Longest block they'll commit to. Longer plans get a counter. |
| `open_to_invites` | `false` → always "no". |

`POST /personas/{id}/ask` is the coordinator's callback. Claude (low effort, 12 s cap) decides yes / no / counter
from the profile and style; code then checks it. A counter must sit inside the person's free time and still overlap
the plan by an hour, and nobody says yes while not taking plans. A failed check, a refusal, an error or a timeout
falls back to the rule decision:

1. Not open to invites → no.
2. Less than an hour of the plan fits → no.
3. Fits, but longer than `max_minutes` → counter with the first `max_minutes`.
4. Only part of the plan fits → counter with that part.
5. Otherwise → yes.

With the seeded mix and rule decisions, one matching run forms 4 groups: Marcus, Bao and Kenji counter with
shorter windows, and Tomas declines, which drops the Jordan / Nia / Tomas group.

## Challenge designer

`POST /challenge` is the coordinator's `CHALLENGE_DESIGNER_URL` (set by default in `docker-compose.yml`). It takes
`{"match", "members": [{agent_id, role, skills, interests}]}` and returns the `split_riddle` template, plus a
`designer` field saying who wrote it (`claude` or `fallback`) and why.

Claude picks a booth from `data/booths.json` in the match's area, then writes one key per role, using the sponsor
info in `data/sponsors.json`:

| Role | Key |
| --- | --- |
| `riddles` | What the sponsor's tool does |
| `map` | Where the booth is |
| `camera` | `Ask them: …`, the question for the sponsor before the group photo |

Code then checks the round:
- the booth is known
- there is one key per member, each different and at most 160 characters
- no key holds the sponsor's name, aliases or booth id
- the camera key starts with "Ask them"
- `timer_min` is between 5 and 20

A broken round goes back to Claude once with the problems listed. A second failure, an error or the 40 s budget
running out gives the hand-written round (`hint` / `location` / `question` in `booths.json`) at the least-used
booth. Booth use is counted, so groups playing at the same time get different booths: if another group takes
Claude's booth while it is still writing, that counts as a broken round and Claude picks again.

### Before the demo

- Walk the venue and fix each booth's `location`, then set `"verified": true`. Only Apify ("near the snacks") is
  confirmed. The Anthropic, SpacetimeDB and Cartesia locations are placeholders.
- `data/sponsors.json` is hand-written for now. The Apify Actor will regenerate it.
- No booths are listed for SJSU yet. Groups there fall back to the full list, so add SJSU spots before showing that
  story.
