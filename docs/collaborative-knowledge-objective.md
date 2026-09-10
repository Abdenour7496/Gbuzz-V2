# Knowledge generated through human–AI collaboration

Objective confirmed on 8 September 2026: knowledge originates inside the stack through collaboration between people and AI agents. External business sources and connectors are not prerequisites or planned priorities. Buzz/Nostr remains the identity authority. Buzz itself is the chat interface and the workspace where humans and agents create and manage knowledge. The separate GCOR workspace page is a temporary development/review tool, not the target employee interface.

Implementation update: the [chat-native knowledge loop](buzz-chat-knowledge.md) and rich cards connect discussion evidence to proposals, human approval and retrieval. The [living knowledge extension](buzz-living-knowledge.md) adds topic browsing, revision proposals, atomic supersession, saved answers, backlinks, history and requested maintenance checks. Continuous background synthesis remains future work.

## Knowledge lifecycle

1. People and agents collaborate in a Buzz channel/session to investigate a question or complete work.
2. Preserve the original contributions, their verified authors, timestamps, channel permissions and revisions as evidence.
3. Agents propose structured findings: decisions, explanations, procedures, unresolved questions and conflicting claims. Each proposal points to the exact supporting contributions and distinguishes observations from inference.
4. Authorized humans review the proposals, resolve disagreements and approve reusable knowledge. Model output and conversational agreement alone do not establish authority.
5. Approved knowledge becomes available to subsequent authorized conversations through retrieval and the rebuildable graph.
6. New evidence and feedback trigger review or supersession while preserving the original discussion and decision history.

## Existing foundation and actual gaps

The repository already models knowledge sessions, human/agent/document participants and entries, with private ingestion and MCP tools for session storage and inspection. The new workspace supports signed text proposals, review, ownership, feedback and governed retrieval. These are useful components, but they do not yet establish a complete automatic collaboration loop.

The session APIs and broad service-credential MCP tools remain private. Caller-supplied agent identifiers are not independently verified agent identities. The signed workspace currently prevents bot-role contributions; changing that needs a separate scoped agent contribution policy, not granting bots reviewer authority.

## Revised implementation priorities

1. **Secure collaborative sessions:** expose channel-authorized session/entry operations, bind authors to verified Buzz identities, preserve signed contribution provenance, and provide scoped agent participation with explicit sponsor/owner and revocation. Keep legacy service tools internal.
2. **Durable discussion capture:** connect Buzz conversation events to session entries with event identifiers, ordering, replay protection, edits/deletions and recovery checkpoints. Test the full path rather than assuming stored session support captures every conversation automatically.
3. **Proposal generation:** run bounded, retryable synthesis jobs over explicit session revisions. Extract findings and decisions with entry-level evidence, model/run provenance and unresolved conflicts. Require human approval before publication; prevent recursive model summaries from becoming independent evidence.
4. **Buzz as the collaboration workspace:** implement knowledge interactions within Buzz channels and conversations, connecting discussions, agent tasks, proposals, supporting messages and review decisions. Do not require users to switch to a separate knowledge portal. Let reviewers accept, amend or reject proposals and request further investigation without losing the original evidence.
5. **Close the learning loop:** retrieve approved knowledge into new sessions, collect corrections, identify affected claims and queue reviews when supporting contributions change. Preserve superseded versions and channel/document restrictions throughout graph traversal.
6. **Evaluate collaboration outcomes:** use representative human–agent sessions to test decision capture, evidence support, conflict detection, permission boundaries, duplicate delivery, agent revocation and recovery. Track useful approved knowledge and correction rates, alongside answer quality and latency.

Off-host backups, retention rules, operational alerts, release hardening and workload/availability testing still apply. Source connector delivery, source ACL synchronization and document-format expansion are outside the current objective unless explicitly requested later.

## Interface boundary

Buzz owns the human and agent experience: conversation, investigation, proposing knowledge, viewing evidence, review, approval, correction and discovery. GCOR supplies authorized knowledge operations behind that interface; PostgreSQL and MinIO preserve authoritative records and artifacts, while Graphiti supplies a rebuildable projection. The existing standalone workspace can support development until equivalent Buzz interactions are implemented. This interface decision is a requirement, not a claim that Buzz integration is already complete.
