# Coordinator improvements: host-led challenges

For: Khoa (coordinator). From: the Agents and AI side.

**Status:** done. Points 1-10 and 12 are in the coordinator (see `coordinator/README.md`, "Host-led challenges").
Point 11 (showing photo-challenge steps on the live board) is still open.

**What changes.** The person who starts a challenge picks the group size, 3 to 5 people. The coordinator
then invites other people's agents until the group is full. This replaces "split the pool into groups of 3".
The challenge itself is now invented by the AI for whoever joins (photo hunts, checkpoint runs), so the fixed
riddles / map / camera roles go away.

The agents service is already built against the contract below. Until `POST /challenges` exists, it falls back
to `POST /matching/run`, so nothing is blocked.

## Changes

1. **New `POST /challenges`**, body `{"agent_id": "linh", "size": 4}`. `size` is 3-5 (422 otherwise): strangers
   never meet one on one, which is part of the safety pitch. The host must be checked in and not already in a
   group (409 otherwise). Create the Match right away with the host as the only member, already accepted, with
   `host`, `size` and state `inviting`, and return it. Invitations run in the background.
2. **Inviting.** Candidates: checked in, free, same area, at least 1 h overlap with the host. Rank them and invite
   the best `size - 1` in parallel through the broker.
   - Question: `"Linh wants a 4-person challenge at Palace of Fine Arts, Sat 2–4pm. Want to join?"`
   - Plan: `{"host", "size", "area", "window", "activity": "creative outdoor challenge"}`. No roles.
   - Agent says yes: add them to `members` (not accepted yet).
   - No, timeout, or a counter that breaks the overlap: invite the next best.
   - Stop when members == size or candidates run out.
3. **Drop the fixed 3 roles in matching.** The challenge designer writes each player's part, so `Match.roles` can
   be `{}`. Rank by fit with the host and with each other: shared and complementary interests and skills, plus
   time overlap. Claude picks the invitees from a shortlist; `matching_rules.md` needs rewriting for that.
4. **Match model.** Add `host: str`, `size: int`, `invited: list[str]`, `declined: list[str]`. States:
   `inviting` → `pending` (all seats filled, waiting for Accepts) → `confirmed` → `playing` → `done`, or
   `cancelled`.
5. **Approval.** The host counts as accepted. An invitee who declines is removed and replaced by the next
   candidate (state back to `inviting`), instead of cancelling the whole group. Confirm when every member has
   accepted and members == size. Log `N/N`, not `3/3`.
6. **Not enough people (nice to have).** When candidates run out, log "2 of 4 joined".
   `POST /challenges/{id}/start` starts with whoever accepted, if that's at least 3.
   `POST /challenges/{id}/cancel` releases everyone.
7. **Log lines for the demo screen.**
   `[challenge] Linh wants a 4-person challenge` → `[match] inviting Marcus, Priya, Diego` →
   `[broker] Diego: no — busy then` → `[match] inviting Kenji` →
   `[approval] Linh ✓ Marcus ✓ Priya ✓ Kenji ✓ → CONFIRMED`.
   Please put the reply's `note` on the broker line. Agents already send a short reason
   ("loves puzzles", "prefers smaller groups"), but the log doesn't show it yet.
8. **Challenge hand-off stays the same.** On confirm, still POST `{match, members}` to `CHALLENGE_DESIGNER_URL`.
   The designer keeps returning `round.keys` with one entry per member, so `set_challenge` validation still
   passes. The new fields inside `round` (`type: "photo_challenge"`, `title`, `goal`, `steps`, `reference_photo`,
   …) just pass through.
9. **MCP.** Add a `start_challenge(agent_id, size)` tool. `/matching/run` can stay for testing, but the app
   won't use it.

## Since main got the live game board

10. **`start_game` assumes roles.** It sends `"role": match.roles[m]` for every seat, which raises a `KeyError`
    once roles are `{}` (point 3). Use `match.roles.get(m, "player")`, or drop `role` from the seat.
11. **Photo challenges on the board.** Our designer now returns `type: "photo_challenge"`. The board still works
    as is: each seat's key is that player's part, "We're here" unlocks, and the final group photo ends it
    (`answer` is the challenge title, so the log line reads well). What it doesn't show yet are the steps:
    they're in `challenge.round.steps`, and each step's photo is checked by the agents service
    (`POST :8100/challenges/{match_id}/check`). Later, the board could take the steps in `start_round` and
    track which ones passed.
12. **`GROUP_SIZE`** only matters for `/matching/run`; with `POST /challenges` the host picks the size.

## What the agents service relies on

| Where | What it reads or sends |
| --- | --- |
| `POST /challenges` | Sends `{"agent_id", "size"}`. A plain 404 `Not Found` (route missing) or 405 triggers the fallback to `/matching/run`. |
| `GET /matches`, `GET /matches/{id}` | `match_id`, `state`, `members`, `accepted` (host included), `host`, `size`, `window`, `challenge`. `accepted` may be in any order. |
| `POST /matches/{id}/accept` | `{"agent_id", "accept"}`, unchanged. Stand-in agents (sample contexts with no real person) accept in `inviting` and `pending`. |
| Question to `callback_url` | `plan.host` and `plan.size`, used for "Linh's 4-person challenge" in the invitee's chat. `plan.window` for the time. |

## Smaller notes

- We now call the seeded people **personal contexts**: fake personal information that a real personal agent
  (Muse, Grok bot, Dots) would already have. The agents service seeds them with callbacks
  (`POST :8001/contexts/seed`), so `/demo/seed` isn't needed for the demo.
- Real people's agents check in at sign-in (like a real personal agent would), so they can be invited before
  they start anything themselves.
