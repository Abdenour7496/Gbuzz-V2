---
name: gcor-knowledge
description: >
  The team's knowledge brain. Use BEFORE investigating any question (ask what
  is already known) and AFTER finishing work that produced a durable finding,
  decision or procedure (propose it for human approval).
version: 1
---

# Knowledge brain (GCOR)

Knowledge here is created by people and agents working together in Buzz, and
becomes authoritative only when a human channel owner/admin approves it. You
read and propose with **your own Buzz identity** — `BUZZ_PRIVATE_KEY` is
already in your environment. Never pass, print or log it.

The tool: `node "C:/Gbuzz/agent-tools/gcor-agent/dist/gcor-agent.mjs"`
(below: `gcor-agent`). If your runtime exposes MCP tools named
`ask_knowledge` / `search_knowledge`, they are the same thing.

Every command needs the channel UUID from your `[Context]`
(`Channel: <name> (#<uuid>)`). Do not ask the user for it.

## 1. Ask first

Before investigating, check what the team already established:

    gcor-agent ask --channel <uuid> "what did we conclude about <topic>?"

- **Grounded answer with sources** → use it, cite the document IDs in your
  reply, and only re-verify if it looks stale or the stakes are high. If you
  find it is wrong or outdated, say so and propose a correction (step 3).
- **"No approved knowledge covers this yet"** → investigate normally.
- `gcor-agent search --channel <uuid> "<terms>"` explores related material.

## 2. Do the work

Investigate as usual. Keep the messages that contain the real evidence
(command output, decisions, confirmations) — you will reply to them.

## 3. Propose what you learned

When work produces something reusable — a decision, root cause, verified
procedure, configuration fact, or acceptance criterion — **reply to the
message(s) holding the evidence** (`--reply-to <event-id>`) with:

    !knowledge propose <Short title> | <finding: what is true, why, under what conditions, how it was verified>

Send it with stdin so line breaks are real (see "Formatting"). The Knowledge
agent answers with a proposal card; a human approves or rejects it. One
finding per proposal. Up to six replied-to messages become its evidence.

Do **not** propose: transient status ("restarting now"), guesses, anything
the human disputed, secrets/keys/tokens, or personal data.

You may never `!knowledge approve`, `reject` or `archive` — those are refused
for agents by design.

## Formatting messages (important)

`\n` inside a quoted `--content "..."` is sent literally and shows up as
`\n\n` in Buzz. Write multi-line messages through stdin instead:

PowerShell:

    @"
    First paragraph.

    Second paragraph.
    "@ | buzz messages send --channel <uuid> --reply-to <event-id> --content -

bash:

    buzz messages send --channel <uuid> --reply-to <event-id> --content - <<'MSG'
    First paragraph.

    Second paragraph.
    MSG

## Errors

- *Not permitted* → you or your human sponsor are not active members of this
  channel. Tell the owner; do not retry with another channel.
- *Unreachable* → the knowledge service is down; continue without it and say so.
- *Rejected the signature* → host clock skew; report it.
