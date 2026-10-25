# System Diagram (for Excalidraw)

Two slides: the main picture (15 s in the video) and a zoom into Whally for judges who want the details.

**To import:** open excalidraw.com → toolbar **More tools** → **Mermaid to Excalidraw** → paste the code inside a
block (without the ```` ```mermaid ```` lines) → **Insert**. Every box and arrow stays editable; add the mascots and
logos afterwards.

## Slide 1: the main picture

```mermaid
flowchart LR
    mobile["Mobile<br>web app"]

    subgraph agents["Personal agents, one per person"]
        pebble["Pebble<br>your agent"]
        a["Agent A"]
        b["Agent B"]
        c["Agent C"]
        mcp["+ any agent<br>via MCP"]
    end

    whally["Whally<br>coordinator"]
    game["Live game, shared board"]

    mobile -->|chat| pebble
    pebble <-->|ask / invite| whally
    a <--> whally
    b <--> whally
    c <--> whally
    mcp <-.-> whally
    whally -->|starts a round| game
    mobile -->|play| game
```

After importing, drag it into your layout: agents fanned behind Pebble, the game box wide along the bottom.

```
┌────────┐       ┌──────────┐┐┐┐                    ┌──────────────┐
│ Mobile │ chat  │  Pebble  ││││  ask / invite      │    Whally    │
│        │──────▶│ A  B  C  ││││◀──────────────────▶│  coordinator │
└───┬────┘       └──────────┘┘┘┘                    └──────┬───────┘
    │ play        + any agent via MCP                      │ starts a round
    ▼                                                      ▼
┌──────────────────────────────────────────────────────────────────┐
│                     Live game, shared board                      │
└──────────────────────────────────────────────────────────────────┘
```

| Box | Logos |
| --- | --- |
| Mobile | Vercel |
| Pebble | Claude, Moss, Apify |
| Whally | Claude, MCP |
| Live game | SpacetimeDB |

**The one rule:** every agent has its own line to Whally. Never draw a line between two agents.

## Slide 2: inside Whally

```mermaid
flowchart LR
    subgraph whally["Whally coordinator"]
        checkin["Check in<br>coarse info only"]
        start["Host starts<br>3–5 seats"]
        rank["Rank people<br>area, free time, skills"]
        pick["Claude picks<br>code checks it"]
        invite["Invite in parallel<br>20 s to answer"]
        accept["Seats filled<br>everyone taps Accept"]
        design["Claude designs<br>the challenge"]
        round["Start the round"]

        checkin --> start --> rank --> pick --> invite
        invite -->|yes| accept
        invite -.->|no / no answer| rank
        accept --> design --> round
    end

    log["Negotiation log<br>live, to the Flow tab"]
    game["Live game<br>SpacetimeDB"]

    whally -.->|every step| log
    round --> game
```

| Step | Logos |
| --- | --- |
| Claude picks | Claude |
| Claude designs the challenge | Claude, Apify |
| Live game | SpacetimeDB |
