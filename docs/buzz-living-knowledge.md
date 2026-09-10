# Living knowledge inside Buzz

Buzz remains the collaboration workspace and Nostr identity authority. This implementation adapts the persistent-wiki pattern to reviewed channel knowledge, without adding external sources or a second employee portal.

## Human and agent workflow

1. Discuss and investigate in Buzz. Use **Draft from discussion** for evidence-linked findings, or `!knowledge topic Title | explanation` for a topic proposal. Similar titles prompt inspection of existing pages first.
2. Browse **Topic index**. Open a page and use **Related knowledge** for backlinks and suggested related titles. Suggestions are labelled; similarity does not establish a factual relationship.
3. On an approved page, select **Propose revision** and supply the complete replacement text. The previous approved content remains available during review.
4. A human owner/admin uses **Review changes**, then approves or rejects. Approval checks the proposal revision and the exact approved base. Supersession, approval, audit outbox and graph jobs commit in one database transaction. Competing stale proposals must be recreated against the latest version.
5. On a cited answer, **Propose as knowledge** preserves the answer and its source-document revisions. It is still a proposal. Generated replies never become independent discussion evidence.
6. **Check knowledge** reports overdue reviews, missing references, changed sources and likely duplicates. If generation is configured, a bounded AI pass suggests contradictions, gaps and links for human investigation. It does not change knowledge or automatically approve anything.

Agents may use the same signed commands if they have active channel membership and an active human sponsor. Only human owners/admins approve knowledge. Every linked page is checked again when opened; restricted documents are excluded from channel replies and indexes.

## Commands

```text
!knowledge index
!knowledge topic Title | explanation
!knowledge revise DOCUMENT_ID REVISION | complete replacement text
!knowledge changes DOCUMENT_ID
!knowledge related DOCUMENT_ID
!knowledge history DOCUMENT_ID
!knowledge save ANSWER_EVENT_ID
!knowledge lint
```

Existing propose, synthesize, ask, show, approve, reject and feedback commands remain available. Commands wrapped in inline backticks are accepted. Old clients retain readable text replies.

## Storage and boundaries

Each proposed revision is a separate existing GCOR document. Metadata records its topic lineage, approved base revision and source dependencies. Original content and governance events remain available through history. No new database migration is required. PostgreSQL remains authoritative; graph jobs are transactional projections.

Evidence validity is enforced at read time. Migration 0010 adds `gcor.knowledge_evidence_current(document_id)`, and every approved-only read path — hybrid retrieval, graph expansion, Graphiti candidate resolution, the enterprise document list and reader, `revise`, `save` and dependency approval — applies it. An approved page whose supporting Buzz message was deleted or moved, or whose approved source was superseded, restricted, re-approved later or itself invalidated (recursively, up to eight hops), disappears from cited answers immediately, even while the Knowledge worker is stopped. The worker still withdraws approval on its cycles so the change is recorded durably through governance publication; it uses the same predicate. When GCOR does not share the relay database (`public.events` absent), message references cannot be checked and only dependency validity applies.

Maintenance is explicitly requested, not scheduled automatically. The index and deterministic checks inspect at most 200 current channel-visible pages; the display lists up to 40 index entries and six clickable links. AI maintenance reviews at most ten pages with bounded excerpts and a 30-second deadline. It is advisory, not exhaustive contradiction detection. Revision text is supplied by the human or agent invoking the operation; automatic background rewriting is not enabled.

## Local release verification

The backend release `gbuzz-knowledge-wiki:20260908` is deployed to the core API, enterprise API and Knowledge worker. All three passed rollout health checks; previous images are retained with `before-wiki` tags and the prior managed override is in `backups/wiki-rollout-before.json`.

Verification passed: 55 proxy unit tests, seven governance fault tests, seven native card tests, and the isolated signed-chat lifecycle including saved answers, revision approval, stale competing proposals, supersession, source invalidation and ACL checks. The isolated test stack was removed afterward.

The native Windows build and installer completed. Buzz was updated and reopened; the installed startup policy and all five sidecars were verified. The installed executable differs from the raw build only in Tauri's expected `UNK` to `NSS` installer marker. Release hashes are in `backups/wiki-release-manifest.json`; rollback application binaries are in `backups/releases/buzz-before-living-knowledge`. The local installer remains unsigned. Native UI interactions are covered by component tests; a complete live human review session with the new controls remains to be exercised.
