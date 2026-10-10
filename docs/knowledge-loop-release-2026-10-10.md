# Knowledge loop v1 — agents use and grow the brain (2026-10-10)

## Why

Buzz agents completed work every day, but the knowledge brain stayed idle:
`gcor_rag_requests_total` was 0 for over a week and conclusions lived only in
chat. Two causes:

1. **No safe agent read path.** `mcp-postgres-gcor` reaches the legacy proxy
   with one shared stack secret and lets the caller choose `access_level`,
   `agent_id` and `channel_id`. The knowledge-trust overlay therefore removed its
   port, leaving agents without retrieval.
2. **A retrieval bug for agent identities.** For a signed agent the proxy forces
   `agent_id = <agent pubkey>`, and SQL filtered `d.agent_id = $3`. Approved
   channel knowledge is stored with `agent_id NULL`, so an agent could never
   retrieve it even with a valid identity.

The write side already existed: sponsored agents can `!knowledge propose`;
only human owners/admins can approve (`docs/buzz-chat-knowledge.md`).

## What changed

| Area | Change |
| --- | --- |
| `proxy/main.py`, `proxy/graph_retrieval.py` | Agent scope = shared channel knowledge **plus** the agent's own items, never other agents' (`agent_id IS NULL OR agent_id = $agent`). Human/legacy visibility unchanged. |
| `proxy/main.py` | `gcor_rag_requests_total{caller="agent|human|service|legacy"}` with zero series exported, so absent agent use is visible. |
| `agent-tools/gcor-agent/` | New. Node CLI + stdio MCP server (`ask_knowledge`, `search_knowledge`). Signs NIP-98 with the agent's own `BUZZ_PRIVATE_KEY` (injected by Buzz's managed runtime) and calls `gcor-enterprise` (127.0.0.1:5011). Single bundled file, pure JS, Node ≥ 20. |
| `agent-tools/nest/` | `gcor-knowledge` skill + `KNOWLEDGE_PROTOCOL.md`: ask first → work → reply to evidence with `!knowledge propose Title \| finding`; send multi-line messages via `--content -` (fixes literal `\n\n` in agent replies). |
| `scripts/release-knowledge-loop.ps1` | New release + rollback (details below). |
| Grafana `gbuzz-overview` | "Knowledge brain use by caller" panel. |
| Tests | +4 Python (agent shared-knowledge scope with failing-before proof, caller attribution, agents can never approve/reject/archive even with owner role, human owner passes the authority check) and 6 Node client tests. JS-signed proofs verified by the production `nostr_auth.verify_event`. |

No schema migration. No new port. `mcp-postgres-gcor` stays internal and
operator-only (service secret); agents use `gcor-agent`.

## Trust model (unchanged guarantees, now on the agent path)

- Identity = the agent's Buzz key; channel, access level and agent scope come
  from the signed proof plus live membership, never from request fields.
- An agent needs active membership **and** an active human sponsor in the
  channel; membership is re-checked before the response is released.
- Answers cite only approved knowledge whose evidence still exists.
- Agents propose; humans approve. Proofs are single-use, 60 s, bound to URL,
  method and body hash.

## Release

```powershell
cd C:\Gbuzz
.\scripts\release-knowledge-loop.ps1                      # default channel: "Kowledge Brain"
.\scripts\release-knowledge-loop.ps1 -ChannelName 'Kowledge Brain','m365  management'
```

Steps: preflight (Docker, ≥ 8 GB free, compose drift report, Windows↔Docker
clock skew ≤ 20 s) → verified encrypted backup → build
`gbuzz-knowledge-durable:knowledge-loop-20261010` → security gate tests inside
that image (network disabled) → set `GCOR_TRUST_IMAGE`, recreate only
`gcor-proxy`, `gcor-enterprise`, `buzz-knowledge` (auto-rollback if unhealthy)
→ enroll channel(s) and list each agent's sponsor status → install the
protocol into `%USERPROFILE%\.buzz` and register the Claude Code MCP server →
signed probe with a throwaway key must be refused with 403.

Rollback: `.\scripts\release-knowledge-loop.ps1 -Rollback` restores the
previous `GCOR_TRUST_IMAGE` from `backups\knowledge-loop-*\rollback.json`.

## Acceptance (in Buzz)

1. `@Fizz-Ceo what did we conclude about mesh compute on the Windows build?`
   → it runs `gcor-agent ask` first; nothing approved yet, so it investigates.
2. Ask it to propose the conclusion → `!knowledge propose …` as a reply to the
   evidence → Knowledge agent posts a proposal card.
3. Approve (button or the command in the reply).
4. Ask again in a new thread → answer cites the approved document, no re-investigation.
5. Grafana: `caller="agent"` series rises above 0.

## Known gaps / next increments

- **Duplicate managed agents**: the nest lists 6× Fizz-Ceo, 8× Honey-CTO and
  8× Bumble-Tech Lead. Duplicates split context and memory; remove extras in
  Buzz Desktop → Agents.
- **Startup plan drift**: the release reports differences between
  `config/windows-startup.compose-files.json` and the running stack; reconcile
  so a reboot starts the same stack.
- **Continuous synthesis**: proposals are agent- or human-initiated. A
  scheduled per-channel synthesis pass is the next step once agent proposal
  quality is observed.
- **Codex MCP registration** is not automated; Codex agents use the CLI via the
  skill, which needs no registration.
