# Design Brief: The Whole App (Mobile)

For: our designer · Phone only (mobile web). There is no big screen; everything happens on the phone.

## The idea in one breath

You start a small group challenge (3 to 5 people), or get invited to someone else's. Your personal AI agent
handles the back-and-forth: the coordinator invites people nearby until every seat is filled. You approve each
step, meet your group, and play a short challenge invented for exactly the people who joined. At the end you
decide, privately, whether you'd meet again.

It should feel like **a friendly matchmaker in your pocket**, not like a dating app, a social feed or a server
dashboard.

> Our demo uses Cal Hacks, but that's only an example. Design for **any** event, place or group challenge.

## Who it's for

- People new to a place who'd like to meet others but won't walk up to strangers: transfer students, solo
  hackers, visitors.
- They're on their phone, often outside or somewhere loud, often with one hand free, and a bit nervous about
  meeting strangers.

## How it should feel

| We want | We avoid |
| --- | --- |
| Warm, playful, a little cheeky | Corporate, robotic, "AI sparkle" overload |
| Safe and in control: nothing happens without my OK | Feeling watched, tracked or judged |
| Light, quick, glanceable | Dense dashboards, tiny graphs, walls of text |
| Like texting a friend who's sorting plans | Swiping on people, profiles, likes, followers |
| Exciting in the game, calm everywhere else | Pressure, countdown anxiety outside the game |

## The journey

Two ways in, one way through:

```
Host:     Hello → OK what's shared → Start a challenge (pick 3–5) → seats fill up ─┐
Invitee:  Hello → OK what's shared → (later) an invitation arrives → Yes ──────────┤
                                                                                   ▼
          Everyone accepts → Meet up & play → Group photo → Done: Meet again? / Share photo?
```

Each moment below needs its own screen or card.

### 1. Hello & onboarding
*Today: a plain form (name, skill, free time, area). Redesign it as a short chat.*

- Your agent introduces itself and asks 3–4 quick things with tap-to-answer chips:
  - **What are you good at?** A few skill chips (puzzles, finding the way, photos, …)
  - **When are you free?** Time chips or a simple time-range picker, never a text field to type `Sat 14:00-16:00`.
  - **Where are you?** A rough area only (campus or neighborhood), never an exact location.
  - **What do you like?** A few interest chips.
- 30 seconds, max. A first name is enough. No profile photo.
- Once you're checked in, others can invite you, even if you never start anything yourself.

### 2. "OK to share?" (consent card)
Before anything leaves the phone, the agent shows **exactly** what it will share, then asks:

```
┌──────────────────────────────┐
│ I'll share only this:        │
│  🕑 Sat afternoon, 2–4pm      │
│  📍 UC Berkeley area          │
│  🧩 Good at puzzles           │
│  ✨ Likes puzzles, hackathons │
│ No name, no exact location.  │
│   [Edit]  [Share]  [Not now] │
└──────────────────────────────┘
```

This card is central to our trust story. Make it feel calm and clear, never legal or scary.

### 3. Home: chat with your agent
The whole app is one conversation with **your agent**. Everything else appears as cards inside it: invitations,
the group card, Accept, the challenge.

- **Agent bubbles:** short, human, one idea per bubble. The agent speaks **for** you ("I said yes for you"),
  never **as** you.
- **Buttons live in bubbles:** Start a challenge, Yes / Different time / No, Accept / Not now.
- **Header:** your agent's avatar and name, a 🔊 sound toggle (off by default), and a small menu
  (privacy, switch person).
- **Two tabs:** **Chat** (this screen) and **Flow** (see 4b).

#### Starting a challenge (host)
- **Start a challenge** card with size chips: **3 · 4 · 5 people**. Never 2: strangers never meet one on one,
  which is part of our safety story. A one-line hint says so.
- After tapping Start, the host is in (no Accept needed) and the group card appears with empty seats filling up.

#### The invitation card (invitee)
When the coordinator invites you, this card appears:

```
┌──────────────────────────────────┐
│ A 4-person challenge nearby      │
│ 🕑 Sat 2–4pm · 📍 UC Berkeley     │
│                                  │
│  [ Yes ]  [ Different time ]  [ No ]   ◔ 20s │
└──────────────────────────────────┘
```

- **Three answers, because the system supports three:**
  - **Yes:** take the seat.
  - **Different time:** opens a small time picker showing only windows inside your free time that still overlap
    the plan by at least an hour (the coordinator rejects anything shorter). If it fits the group, you get the
    seat and the group's time adjusts.
  - **No:** "No worries, I'll tell them."
- **Countdown ring: exactly 20 seconds.** That's how long the coordinator waits; after that it counts as "no"
  and invites the next person. Keep the ring calm (thin, soft color), with no ticking or red flashes. When it
  runs out, show "That one's gone. I'll keep an eye out for the next" instead of an error.
- **The host stays anonymous** on this card ("A 4-person challenge nearby"), like everyone else, until the whole
  group has accepted.

### 4. Agents negotiating: the group card & ▶ Listen
While your group fills up, the chat shows a live mini-picture of **only your group**: you, the coordinator,
and one seat per person (3 to 5 seats).

```
            Coordinator
       /     |      |     \
    You      A      B     ( )
   host ✓   in ✓  thinking  open seat
               ▶ Listen
```

- **Honest shape:** everyone connects to the coordinator; **agents never connect to each other directly**.
  That is how the system really works and it is our privacy story, so please keep it.
- **Seats:** empty seats are visible ("open seat") and fill one by one. "2 of 4 joined" sits under the card.
- **Motion:** a small dot travels a line when a question goes out or an answer comes back.
- **Others stay anonymous:** they're shown as letters (A, B, C, D) until everyone accepts. No names or photos
  before that. There are no fixed roles any more: the challenge is invented for whoever joins.

| State | Look (suggestion) | Label |
| --- | --- | --- |
| Open seat | dashed outline | "open seat" |
| Invited, waiting | soft pulse | "thinking…" |
| Yes / took the seat | green check | "in" |
| Asked for a different time | amber + clock | "checking time…" (never their actual times) |
| No / no answer | greyed, gently fades out, seat reopens | "can't make it" |
| Accepted | solid fill, small bounce | "accepted" |

**When things don't go to plan**
- **Someone backs out after joining:** their seat reopens and the coordinator invites someone else. The group
  doesn't fall apart. Copy: "One person had to drop out. Finding someone else…"
- **Nobody else is free:** "3 of 4 joined. Nobody else nearby is free then." The host gets two buttons:
  **Start with 3** (shown only once at least 3 have accepted) and **Call it off**.
- **The host calls it off:** everyone gets a gentle "That challenge was called off" and is free again.

**▶ Listen** plays back **your agent's private call with the coordinator** as a short voiced scene (4–6 lines,
~20 s). It's a one-on-one phone call, not a group chat: the other agents are never in it and never hear it. Each
line appears as a caption bubble in sync with the voice, and the line between your node and the coordinator
lights up while it plays. It must work fully with the sound off.

> 🎙️ **Coordinator:** "Hi! Sorry to bother you. Someone nearby is starting a 4-person challenge, Saturday 2 to 4. Interested?"
> 🎙️ **Your agent:** "Sounds fun, but my person's only free until 3:30."
> 🎙️ **Coordinator:** "Got it. 2:30 to 3:30 still works for the group. Still good?"
> 🎙️ **Your agent:** "Perfect, we're in. I'll ask my person to confirm."

The coordinator only passes on **the result** ("2:30 to 3:30 works for the group"), never what another agent said
or why. On the group card, the other seats show their state (in / thinking / can't make it), but you never hear
their calls.

### The one rule the design must keep

**Agents only ever talk to the coordinator.** Every conversation is your agent ↔ coordinator, one-on-one. On
every screen:

- **No direct links:** never draw a line, bubble or chat thread straight between two agents.
- **No overheard words:** never show or play another agent's own words.
- **Coordinator-only replies:** your agent only reports what the coordinator told it: "The coordinator moved the
  time to 2:30–4. That still works for you, so I said yes." Not "B wanted 2:30."

### 4b. The Flow tab: watch the whole negotiation
Next to **Chat**, a second tab, **Flow**, shows step by step how your group is being put together. Chat is
"what my agent tells me"; Flow is "what's happening behind it".

**Layout: a vertical timeline (sequence diagram).** One column per party, time running down, one arrow per
message. On a phone, this reads better than a hub picture because it shows the **order**: who was asked first,
who answered when.

Example from the host's phone (a 3-person challenge):

```
 You           Coordinator          A             B
  │                 │               │             │
  │──starts (3)────▶│               │             │   2:00
  │                 │─────asks─────▶│             │   2:01
  │                 │─────asks──────┼────────────▶│   2:01
  │                 │◀─────yes──────│             │   2:02
  │                 │◀──new time?───┼─────────────│   2:02
  │◀─"2:30–4 ok?"───│               │             │   2:02
  │──"yes"─────────▶│               │             │   2:02
  │                 │◀─────yes──────┼─────────────│   2:03
  ├─────────────────┼── all 3 seats filled ───────┤   2:03
  │                 │───Accept?────▶│             │
  │                 │───Accept?─────┼────────────▶│
```

- **Every arrow starts or ends at the Coordinator column.** An arrow to B passes *over* A's line (┼); it never
  stops there. Never draw an arrow from A to B: that never happens. Messages fan out (Coordinator → A, → B) and
  come back in whatever order people answer.
- **Your own column shows real words:** your messages carry their content ("until 3:30"), because they're yours.
- **Other columns show the type only:** *asks · yes · no · new time? · accepted*, color-coded the same as the group
  card. Never their times, notes or reasons.
- **People are anonymous:** others appear as A, B, C, D until everyone accepts.
- **Live:** new arrows slide in at the bottom as they happen, each with a small timestamp. Tap an arrow to see a
  plain-words caption ("The coordinator asked B if Saturday 2–4 works").
- **Replay:** a ▶ button replays the whole flow at a calm pace. When ▶ Listen is playing in Chat, the matching
  arrows in your column light up.
- **Milestones:** small dividers across all columns: *seats filled*, *everyone accepted*, *challenge started*,
  *done*.
- **Who drops off:** the coordinator may invite more people than end up in your group. People who said no or didn't
  answer fade to grey and drop off, so the final columns are your group.

#### Where the Flow data comes from (engineering note)

The privacy rules above **must be enforced on the server, not just hidden on the phone.** Anything sent to the
phone can be read by anyone who opens the browser tools.

- Today's live feed (`/ws/log`, drawn by `?view=log`) is the **operator's** demo view. It contains real names,
  everyone's reply notes and counter-offer times. **The phone app must never use it.**
- The Flow tab, the group card and ▶ Listen read a **per-person feed** the coordinator filters for one person:
  - only events about **that person's** group (and invitations sent to them);
  - their own messages in full (their times, their notes);
  - everyone else as a letter (A, B, C…), with the message type only (*asks / yes / no / new time? / joined /
    left / accepted*): no names, times, notes or reasons;
  - first names only after the whole group has accepted.
- The same applies to the invitation itself: the question and plan an invitee receives must not name the host
  until everyone has accepted.
- The **Everyone view** (Extras) needs its own anonymous feed for the same reason: dots and counts only.

#### The "AI brain" look and motion
We want the Flow tab to feel alive, like watching a neural network think. Energy pulses race out from the
coordinator, agents light up when they're called, and answers flow back as light. Think glowing synapses, not a
spreadsheet.

**The coordinator is the brain.** It sits at the top (or center) as a glowing core. When it's working, its
inside shows a small neural-net pattern (dots and threads flickering), like a mini brain thinking. This is where
the "machine learning" feel lives, so make it rich.

**Agents are neurons on the edge.** Each one is a node connected to the core by its own curved, synapse-like
line, and **only** to the core. A real neural net has neurons wired to each other, but ours must not: the
"brain wiring" happens inside the coordinator, never between agents.

The beats of one round, as motion:

| Moment | Animation |
| --- | --- |
| The host starts a challenge | The host's node glows, sends a pulse up its line, and the core "breathes in" (soft swell). Empty seat-nodes appear as faint outlines. |
| Coordinator thinks / picks who to invite | The neural pattern inside the core lights up and flickers faster, for 1–2 s. |
| Coordinator invites people | Bright energy pulses shoot down the lines to A, B, C, in parallel or one after another. Each node wakes up with a ripple ring when the pulse hits. |
| Someone is deciding | Their node shimmers or breathes gently, in time with the 20 s window. |
| Answer comes back | A pulse travels back up to the core, colored by the answer: green = yes, amber = new time, grey = no. Answers return in whatever order they arrive. |
| "No" or no answer | The node dims and its line fades out, like a neuron going quiet. The seat outline reappears and a new invite pulse goes out. |
| All seats filled | The core flashes; the group's lines brighten and thicken; the others fade away. |
| Everyone accepts | One bright pulse from each node into the core, then a burst. |

**Two views, same motion**
- **Brain view (default):** this animated core-and-neurons picture, with only your group's seats plus anyone
  the coordinator is currently inviting.
- **Timeline view (toggle):** the step-by-step list above, for people who want to read exactly what happened.
  Each pulse in the brain view adds one arrow to the timeline.

**Style notes**
- **Dark mode is the hero here:** a deep background makes glows and pulses pop. Light mode uses softer color
  trails instead of glow.
- **Pulse colors follow the answer colors** everywhere, so the brain view, the timeline and the chat all agree.
- **Keep it smooth on phones:** a handful of nodes, 60 fps, no heavy particle storms. Pausing the motion pauses
  the replay too.
- **Reduced motion:** pulses become fades and node states change without travel.
- **Sound (when 🔊 is on):** a soft "whoosh" per pulse and a chime when all seats fill. Very quiet, optional.

### 5. Seats filled → Accept
- **Card:** the time, a rough place, the group size, and the others as letters.
- **Accept / Not now:** always the person's own tap; the agent never accepts for them. (The host is already in.)
- **Waiting:** shows who has accepted so far (✓ ✓ ⋯ ⋯), then "Everyone's in! Setting up your challenge…"
- **Decline or no group:** use "I'll keep looking" tone, never "you were rejected".

### 6. The challenge
*Today: a "your part" card, a "who's here" list, a "We're here" button, a photo-URL field. Keep the structure,
make it fun.*

The AI invents the challenge for exactly the people who joined, e.g. a photo hunt ("find something green for
each teammate, then take a group photo"). Each person gets **their own part**, and nobody can finish alone.

- **Your part:** the hero of the screen. Big type, the challenge title and goal above it, and a "Tell your group
  your part" hint. A 🔊 button reads it aloud.
- **Steps (photo challenges):** a short checklist; each step gets a ✓ when its photo is checked.
- **Timer:** a pill in the corner (e.g. "12 min left"). It's playful, not stressful.
- **Who's here:** 3–5 people with *not joined → on the way → ✓ here*. First names appear now.
- **"We're here":** one big thumb-reach button. Once everyone taps it, celebrate the unlock.
- **Group photo:** open the camera directly (replace today's "paste a photo link" field) and take one shot.

### 7. Done
- **Celebrate:** "Challenge complete! 🎉"
- **Rate it:** private and quick (1–5 or emoji).
- **Meet again?** A private Yes / No. Others only learn about a mutual yes.
- **Share the photo?** It's only posted when everyone says OK, so show who agreed (✓ ✓ ⋯).

### Extras
- **Everyone view** (a toggle inside Flow): the full ring of everyone checked in around the coordinator,
  anonymous dots and counts only, from its own anonymous feed (not today's `?view=log`, which shows names).
  It's for the curious and for filming the demo.
- **Privacy page:** one screen in plain words: what's shared, what never is, delete my data.
- **Errors / offline:** friendly and short, e.g. "Hmm, lost the connection. Trying again…"

## Characters & voices

| Who | Personality | Voice direction | Visual cue |
| --- | --- | --- | --- |
| **Coordinator** | Upbeat host, polite, a bit apologetic for interrupting | Warm, clear | Neutral "host" mark |
| **Your agent** | Loyal, protects your time, slightly witty | Close, friendly | Your color + avatar |
| **Other agents** | No voice in your app: you never hear their calls | n/a | A letter (A, B, C, D) on a neutral shape |

## Visual direction (suggestions, not rules)

- **Style:** friendly and rounded with soft cards, plus one bold accent color for actions and success moments.
- **Colors:** green = yes / in, amber = different time / waiting, grey = no. Pair every color with an icon or a
  word.
- **Type:** big and legible. "Your part" should be readable at arm's length outdoors.
- **Motion:** small and meaningful (dots on lines, check bounces, an unlock celebration). Respect reduced motion.
- **Modes:** light and dark.

## Practical constraints

- **Screens:** 360–430 px wide, with main buttons in thumb reach and one primary action per screen.
- **Outdoors:** readable in bright sunlight.
- **Timing:** invitations expire after **20 seconds** (the coordinator's wait). Design the ring and the "missed
  it" state around that.
- **Sound off by default:** captions for every voice line. Browsers block autoplay until a tap anyway.
- **Accessibility:** good contrast, never color-only meaning, works with large text.
- **Privacy rules the UI must keep:** rough info only; others anonymous until all accept; nothing shared or
  posted without the person's tap. These are enforced by the server's per-person feed, not only by hiding
  things in the UI.

## Copy tone cheatsheet

| Instead of | Say |
| --- | --- |
| "Find my group" | "Start a challenge" |
| "Match request rejected" | "One person can't make it. Inviting someone else…" |
| "Invitation timed out" | "That one's gone. I'll keep an eye out for the next." |
| "Profile data transmitted" | "I shared your rough free time and interests." |
| "Negotiation complete" | "Done! Your group is full." |
| "Submit photo URL" | "Take the group photo 📸" |
| "Error 500" | "Hmm, I lost the connection. Trying again…" |

## Screens we'd love back

1. Hello + onboarding chat (chips, time picker)
2. "OK to share?" consent card
3. Home chat: idle, with "Start a challenge" (size chips 3 · 4 · 5)
4. Invitation card: Yes / Different time / No with the 20 s ring, the time picker, and the "missed it" state
5. Group card: seats filling, someone checking a new time, someone dropping out and the seat reopening
6. Host: "3 of 4 joined" with Start with 3 / Call it off
7. Flow tab: brain view (core thinking, pulses out, answers back, seats filled) and timeline view
8. ▶ Listen playing (sound on and sound off)
9. Seats filled → Accept → waiting for others
10. Challenge: title, your part, steps, timer, who's here, "We're here"
11. Unlocked → camera / group photo
12. Done: rating, Meet again?, Share photo?
13. Privacy page, error state, "called off"

Plus: light + dark versions, motion notes for the group card, the brain-view pulses and the unlock, and icons for
the coordinator and your agent.
