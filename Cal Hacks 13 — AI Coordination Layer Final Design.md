# Cal Hacks 13 — AI Coordination Layer: Final Design

Oct 23, 2026 · @Khoa Nguyen

## Overview

Your personal agent finds you people to meet. Our coordinator matches you with other people's agents and designs a challenge that strangers can only win together; afterward, you share the moment.

- **Named community:** solo hackers at Cal Hacks (demo), and SJSU transfer students (the "usable on Monday" story).
- **Problem:** making friends after moving or transferring is hard, and swiping apps feel awkward.
- **What stands out:**
  - AI engineers the icebreaker: split roles (riddles / map / camera) force the group to talk.
  - The agents' work is visible: a live negotiation log is on screen.
  - Real users: real hackers play at Cal Hacks, and the video opens with that footage.
  - Measured connection: "X of Y groups wanted to meet again."
  - Safety by design: public spots, groups of 3, human approval, coarse info only.
- **Why agents, not a matching form:** agents work for you in the background, negotiate tradeoffs without group-chat back-and-forth, and design the challenge around the specific people in it.

## Decision log

The team landed here after 15 decisions, from a finance agent to a people-connecting game. Oldest first.

| # | Decision | Why |
| --- | --- | --- |
| 1 | Started with an autonomous payments-fraud agent | Hyphenate's $8k prize; stacked CRS, Microsoft, Factory |
| 2 | Dropped Fetch.ai | Prize not worth it; framework hard to bend |
| 3 | Team wanted big-name tracks (Anthropic, Meta, Atlassian) | Recruiting visibility |
| 4 | Dropped the finance agent | Team didn't want it; Hyphenate, CRS, Nessie went with it |
| 5 | Considered a voice agent for immigrant parents | Not chosen |
| 6 | Strategy: one big sponsor + one niche floor | Best case big win, worst case niche win |
| 7 | Chose the team's idea: agents coordinating meetups via challenges | Fits Meta, Anthropic, Spacetime, Moss |
| 8 | Made challenges cooperative with split roles | Stands out from other meetup apps |
| 9 | Cut the social feed and people-ranking | Scope; ranking people reads as unsafe |
| 10 | Seeded personas + real users; web chat is the personal agent | Reliable demo, real proof, keeps the agent story |
| 11 | Coordinator in Python | Team preference; Claude, Apify, MCP SDKs are Python |
| 12 | Demo game: Three-key sponsor hunt, indoors, button check-in | Easiest to film; sponsors are judges |
| 13 | Hybrid matching: code rules, Claude picks, code validates | AI judgment visible in the log, never breaks live |
| 14 | Apify scrapes sponsor sites for clues; dropped Factory Droid and Mana; Cartesia is stretch | Apify core to the demo; Droid is a coding agent |
| 15 | Team: 1 designer + 3 developers | Designer owns Figma and the negotiation animation spec |

## Architecture

All agents go through one Python coordinator; agents never talk to each other directly. Only the challenge designer and the agents use an LLM — matching is plain code.

| Component | Tech | Responsibility |
| --- | --- | --- |
| Personal agents | Claude + Moss memory | LLM + persona info. 15–20 seeded personas, plus real users via web chat. |
| Agent gateway | FastAPI + Pydantic | Receives check-ins; strips to coarse fields (no names, no exact location). |
| Broker | asyncio | Asks candidate agents in parallel whether a plan fits; collects replies. |
| Matching engine | Plain Python | Groups of 3: time overlap, same area, compatible prefs, complementary skills. |
| Approval | FastAPI | Group is pending until all 3 accept, then confirmed. |
| Negotiation log | WebSocket | Streams every step live to the log screen (the demo screen). |
| Challenge designer | Claude + Apify | Real public spots nearby; cooperative hunt with split roles + icebreaker. |
| Live game | SpacetimeDB | Shared clue board; one person's solve unlocks the next step for everyone. |
| Web app | Mobile web | Chat page, game page, end page (meet again? + share with everyone's OK). |

Open question: confirm which languages SpacetimeDB supports for modules and clients. Likely setup: the coordinator writes game state, the web app subscribes directly.

## Tech stack

Python backend, Next.js frontend, Claude everywhere an LLM is needed. Skipped on purpose: auth, GPS, Postgres/Redis, a native app.

| Layer | Pick | Notes |
| --- | --- | --- |
| Coordinator | Python, FastAPI, Pydantic | Pydantic models are the frozen interface contract |
| Broker | asyncio + httpx | Asks candidate agents in parallel |
| Matching | Python filter + Claude pick + Python validation | Rules in `matching_rules.md`; fallback to top score |
| Negotiation log | FastAPI WebSocket | Event stream that drives the frontend animation |
| Data | SQLite + JSON seeds | Personas, matches, booth list |
| Personal agents | Claude API + persona JSON | LLM + info |
| Agent memory | Moss SDK | Check language and API at their booth |
| Challenge designer | Claude API, structured JSON output | Fills the game template |
| Clue content | Apify | Scrapes sponsor sites; published as an Actor |
| Live game | SpacetimeDB, TypeScript client | Shared clue board and check-ins |
| Frontend | Next.js, Tailwind, mobile-first | Chat, game, log, end pages |
| Animation | Framer Motion + SVG | Negotiation animation |
| Photos | Supabase Storage or Cloudinary | Upload → URL |
| Share | Web Share API | Native share sheet; posts only with all 3 OKs |
| Hosting | Vercel (web), Railway or Render (coordinator) | Deploy hello-world tonight |
| Stretch | FastMCP; Cartesia voices | Real agents join via MCP; agents speak their keys |
| Design | Figma | Screens and animation spec |
| Notes | Notability | Free track; notes from minute one |

## Agent interface

Freeze this contract Saturday morning. Seeded personas, the web chat agent and future real agents (Muse, Grok bot, Dots) all speak it, so four people can build in parallel.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | /agents/checkin | Agent sends coarse availability and profile |
| POST | /agents/{id}/ask | Coordinator asks an agent a question |
| POST | /agents/{id}/reply | Agent answers yes / no / counter |
| POST | /matches/{id}/accept | Person accepts the proposed group |
| GET | /matches/{id} | Status, members, roles, challenge |

Check-in payload:

```json
{
  "agent_id": "linh",
  "free": "Sat 14:00-16:00",
  "interests": ["puzzles"],
  "skills": ["riddles"],
  "group_pref": "small",
  "area": "UC Berkeley"
}
```

Match states: `pending` → `confirmed` (all 3 accept) → `playing` → `done`. A decline returns the others to the pool.

## Flow and matching

Six steps, from check-in to "meet again?". Cut for the hackathon: verified email, decline timeouts, multiple challenge types.

1. **Onboard:** a quick chat with your agent (interests, free time, skills), saved to Moss. Seeded personas skip this.
2. **Check in:** the agent posts coarse availability to the gateway.
3. **Match:** the broker asks candidate agents if the plan fits; the engine forms a group of 3 and marks it pending.
4. **Accept:** each person taps Accept; the group becomes confirmed.
5. **Play:** Apify finds public spots, Claude writes the split-role hunt, SpacetimeDB runs the live clue board.
6. **Meet again?:** private ratings and mutual opt-in; a share button posts the group photo once all 3 approve.

Matching rules (plain Python):

- Filter: same area, at least 1 hour of overlapping free time, compatible group preference.
- Score each candidate group of 3 on skill complementarity: one puzzle person, one navigator, one photographer scores highest.
- Pick the best-scoring group; tie-break on longest time overlap.
- At 20 personas, brute force over all groups of 3 is fine (1,140 combinations).

## Game design

The demo game is one round, "Three-key sponsor hunt": about 10 minutes, indoors at the venue. Each person holds one key, so nobody can solve it alone.

**Design rules:**

- Split info: every player holds a piece the others need.
- Light teamwork: talking and walking, never hard puzzles.
- Public, safe spots only; targets are roles, objects or places, never people picked by appearance.
- Ends in a group photo, which becomes the share card (posted only if all 3 approve).

**Demo round:**

| Player | Role | What their phone shows |
| --- | --- | --- |
| Linh | Key 1 | "This sponsor's tool helps you scrape the web." |
| Marcus | Key 2 | "Their booth is near the snacks." |
| Priya | Key 3 + ask | "Ask them: what's the coolest thing someone built with your tool?" |

1. Each person reads their key out loud.
2. Together they work out the booth (Apify, near the snacks) and walk there.
3. All 3 tap "We're here" — no GPS needed.
4. Key 3 holder asks the sponsor the question.
5. Group photo with the sponsor → upload → "Challenge complete!"
6. End screen: rate the challenge, "Meet again?", "OK to share the photo?"

**Game engine (one template, Claude fills the content):**

```json
{
  "group": ["linh", "marcus", "priya"],
  "round": {
    "type": "split_riddle",
    "keys": {
      "linh": "This sponsor's tool helps you scrape the web.",
      "marcus": "Their booth is near the snacks.",
      "priya": "Ask them: what's the coolest thing someone built with your tool?"
    },
    "answer": "apify_booth",
    "unlock": "all_checked_in + photo",
    "timer_min": 15
  }
}
```

The engine supports one round type (`split_riddle`) and one unlock rule. Claude writes the keys from the group's profiles and the seeded booth list.

**Later round types (same engine, for the "usable on Monday" pitch):**

- `icebreaker`: each person guesses which group member a fact belongs to.
- Outdoor landmark hunt: same split keys, with a GPS checkpoint and Apify spots.
- `pose`: one person directs a themed group photo.

**Before Saturday:** walk the venue, note 3–4 booth locations, and add them to the seed data.

## Demo plan

The demo runs on seeded agents for reliability, then proves it with real hackers playing on Saturday.

**Cast** (recruit Saturday morning):

- **Linh:** likes puzzles, free 2–4pm.
- **Marcus:** knows campus well, free 1–4pm.
- **Priya:** into photography, free 2–5pm. Joins through her own agent via MCP (stretch) to show the layer.

**Negotiation log on screen:**

```
[coordinator] 20 agents checked in · area: UC Berkeley
[match] overlap window: Sat 2:00–4:00pm
[match] roles → Linh: riddles · Marcus: map · Priya: camera
[match] group proposed · waiting for 3/3 approvals
[approval] Linh ✓  Marcus ✓  Priya ✓  → CONFIRMED
[challenge] secret booth chosen · 3 keys sent · timer 15 min
```

**Challenge:** the Three-key sponsor hunt (see Game design). Each person reads one key, they find the booth together, tap "We're here", ask the sponsor the question, and take the group photo.

**2-minute video:**

| Time | Show |
| --- | --- |
| 0:00–0:15 | Real footage: three strangers high-fiving at a sponsor booth. "Two hours ago they'd never met." |
| 0:15–0:30 | Problem: solo hackers and transfer students struggle to meet people. |
| 0:30–1:00 | Live: agents check in, negotiation log runs, everyone taps Accept. |
| 1:00–1:30 | Footage of the hunt: reading keys aloud, finding the booth, asking the sponsor. |
| 1:30–1:45 | Result: "X of Y groups wanted to meet again." |
| 1:45–2:00 | One architecture slide: coordination layer, MCP, rule-based matching, safety. |

## Tracks, team and plan

**Tracks:**

| Track | Tier | What covers it | Proof for judges |
| --- | --- | --- | --- |
| Meta | Big | AI that brings strangers together | Real hackers who met; "X of Y want to meet again" |
| Anthropic | Big | Claude runs agents, match pick, challenge design | Named community; validated matching, approval, consent to share |
| Spacetime | Niche core | Live clue board and check-ins | 3 phones update instantly on "We're here" |
| Moss | Niche core | Agent memory: profiles, matches, ratings | A second match improves from feedback |
| Apify (+ Best Actor) | Niche core | Scrapes sponsor sites for clue content | Clues written from scraped data; public Actor link |
| Cartesia | If ahead | Agents speak their keys in distinct voices | Voice is part of the round |
| People-First, Most Creative, Hacker's Choice, Most Technically Complex | General | Connection, split-key design, real players, architecture | Same demo |
| Notability | Free | Planning notes from sketch to final | Shared notebook |

Each track gets its own README section saying where and how its tool is used.

**Team split:**

| Person | Owns | Details |
| --- | --- | --- |
| Miki | Figma screens, negotiation animation spec, share card, demo slide | Designer tab |
| Khoa&#32; | Coordinator: gateway, broker, hybrid matching, approval, WebSocket events, MCP stretch | Coordinator tab |
| Phong + Jessice | Agents and AI: personas, web chat agent, Moss, challenge designer, Apify, Cartesia stretch | Agents tab |
| Phong/Jessica/ Khoa | Frontend: Next.js pages, negotiation animation, SpacetimeDB board, photo upload, share | Frontend tab |

Everyone helps recruit players Saturday morning and film the demo Sunday.

**Timeline:**

- [ ] Sat morning: freeze the interface, seed personas, recruit real hackers.
- [ ] Sat night: end-to-end works with seeded agents.
- [ ] Sun early: real hackers play; film it. Add the MCP adapter if ahead.
- [ ] Sun before judging: polish, video, a README per track.

**Stretch: real agent adapters (Muse, Grok bot, Dots):** expose the coordinator as an MCP server (FastMCP) with `checkin`, `reply`, `accept` tools. Fallback is a thin adapter per app that maps its API onto the endpoints above. One working adapter is enough for the demo.

**Risks:**

- "Why agents?" — have the one-sentence answer ready (Overview).
- Safety questions — public spots, groups of 3, approval, consent before sharing.
- Nobody shows up to play — recruit early; seeded personas are the backup.
- SpacetimeDB language support — check docs before Saturday.
- Scope — one challenge type, working live, beats several half-built ones.
