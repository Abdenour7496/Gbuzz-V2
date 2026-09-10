# Knowledge collaboration inside Buzz

The Knowledge agent connects ordinary Buzz messages to GCOR proposals, human review and approved answers. Use Buzz Desktop as usual; no separate knowledge portal or new employee identity is required. This adapter uses the deployed Buzz 0.2.1 protocol without replacing the relay or Desktop application.

## Use in Buzz

The initial enabled channel is `m365  management` (its existing name; no Microsoft source connection is involved).

Send these as ordinary messages, rather than client slash commands:

| Message | Result |
| --- | --- |
| `!knowledge help` | Shows available actions in a threaded reply |
| `!knowledge synthesize` | Drafts findings from up to six recent discussion messages; when replying to a message, uses the referenced message(s) |
| `!knowledge synthesize EVENT_ID ...` | Selects up to six specific supporting messages from this channel |
| `!knowledge propose Title \| finding or decision` | Submits a human or sponsored agent proposal; reply to original messages to attach evidence |
| `!knowledge list` | Lists channel knowledge allowed for the caller's role |
| `!knowledge show DOCUMENT_ID` | Shows evidence, state and the exact review revision |
| `!knowledge approve DOCUMENT_ID REVISION` | Human owner/admin approves the inspected revision |
| `!knowledge reject DOCUMENT_ID REVISION` | Human owner/admin rejects a proposal |
| `!knowledge archive DOCUMENT_ID REVISION` | Human owner/admin retires knowledge |
| `!knowledge ask your question` | Answers using approved channel knowledge with source references |
| `!knowledge feedback DOCUMENT_ID outdated your note` | Records feedback; categories also include incorrect, missing and helpful |

The proposal reply includes its preview and a complete approval command to copy. Review it before approving. Synthesis is a model draft, or source excerpts if inference fails; it never approves itself. A changed document revision requires a fresh review. Approvals assign the reviewer as owner with a 90-day review date.

## Trust and delivery

- Commands come from persisted Buzz kind 9/40002 events. Their event hash, Schnorr signature and channel tag are verified. Current user and channel membership are checked before execution and again before delivery.
- Guests cannot propose or approve. Agents require a registered agent identity and an active human sponsor in the channel. Agents can propose; only human owners/admins can approve, reject or archive. The Knowledge agent ignores its own commands/replies to avoid loops.
- Replies are channel-visible. Documents with explicit reader lists are excluded from chat answers and details, even when the requester personally has access. They remain accessible through the existing private-reader mechanisms, not broadcast into Buzz.
- Selected source events and generated drafts are recorded for retry stability. Proposals preserve event IDs and authorship; generated knowledge replies cannot be used as independent synthesis evidence.
- The inbox and signed reply outbox survive restarts. A PostgreSQL advisory lock serializes worker execution. Repeated delivery reuses the same signed event ID; ingestion and review preserve their idempotency behavior. Execution has three attempts and a four-minute budget; delivery retries every 30 seconds. Replies older than two minutes are suppressed rather than broadcasting old results; an executed change can still be inspected with list/show.
- Removed source events hide dependent approved knowledge from every read path immediately (`gcor.knowledge_evidence_current`, migration 0010) and withdraw approval through governance publication on the next successful worker cycle. Deletion does not purge archived evidence or backup artifacts; retention remains a separate policy.
- Channel enrollment starts command processing from enrollment time, avoiding execution of historical commands. Synthesis can still explicitly use earlier discussion messages. Existing conversation capture continues separately.

## Operation and fallback

Migration `0009_buzz_knowledge_commands.sql` adds enrollment, inbox and outbox tables. `docker-compose.buzz.yml` adds a dedicated worker using the existing GCOR libraries, database, object storage and local inference. No new public port is exposed. Include this overlay when operating the full stack, together with the existing managed core, enterprise and observability overlays.

`BUZZ_KNOWLEDGE_PRIVATE_KEY` is a dedicated service identity stored in the local environment, never an employee key. Preserve it across restart; use deployment secret storage for a production host. `BUZZ_KNOWLEDGE_AUTH_URL` must match the public Buzz authority; the worker connects over Docker DNS while retaining that authority for community routing.

`scripts/setup-buzz-knowledge.py CHANNEL_UUID`, run inside the configured service with `PYTHONPATH=/app`, enrolls the agent in the selected channel with an active human owner as sponsor. It inserts only the dedicated agent's user/membership and channel enrollment records. It does not restore revoked membership or overwrite existing identities. New channels must be explicitly enrolled.

`scripts/check-buzz-knowledge.py` checks NIP-42 authentication without posting a chat message. Docker health monitors the worker heartbeat. Inspect `gcor.buzz_knowledge_commands` for attempts, error, response and delivery state; `StaleReplySuppressed` means a change may have completed without a delivered confirmation. Failed commands can be reissued as new Buzz messages after resolving the cause. Do not reset signed command IDs or delete inbox history to retry them.

Stop only `buzz-knowledge` to disable the integration. Buzz chat, existing GCOR APIs and stored knowledge remain operational. Disable an enrollment by setting its `enabled` flag false; removing the bot or its sponsor's membership also prevents operation in that channel. Keep the additive tables and pre-deployment database backup during rollback.

## Validation and limits

On 8 September 2026, 64 proxy unit tests passed. The isolated storage lifecycle verified signatures, sponsorship, role denial, proposal/approval, replay, restricted-document exclusion and revocation. A separate actual Buzz 0.2.1 relay test verified real WebSocket messages and threaded bot replies through discussion, synthesis, human approval, cited retrieval and source-deletion invalidation. Tests used synthetic identities and deterministic inference/excerpt fallback; they are not an answer-quality benchmark.

This closes the first chat-native collaboration loop. Rich Buzz cards/buttons, automatic continuous synthesis, task delegation, correction/supersession shortcuts, customizable review schedules, restricted-document private replies, and full knowledge-owner administration inside Buzz remain future increments. No real employee signing key was used in testing. The standalone page remains a development/review tool rather than the primary interface.

Live deployment verification: the Knowledge agent passed NIP-42 authentication without posting a message; its heartbeat was fresh and all 21 long-running services remained running. The pre-migration database backup is "backups/buzz-chat-before.dump". Existing core and reader images were retained.

Native cards: [card renderer, trust checks and Desktop packaging status](buzz-knowledge-cards.md). The agent supplies card metadata with its text replies; displaying buttons requires the updated Desktop build.
