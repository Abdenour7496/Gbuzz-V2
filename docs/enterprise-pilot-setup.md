# Buzz / Nostr identity pilot

The knowledge reader uses existing Buzz identities. It verifies a signed Nostr HTTP authentication event and checks the signer against Buzz's current users and channel_members tables. No external identity tenant, app registration, password, or new user directory is required.

## Access policy

- A kind 27235 NIP-98 event proves ownership of the Nostr public key. Sign with the existing client/wallet; never send private keys to GCOR.
- Sign the exact URL, POST method, and SHA-256 hash of the exact JSON request bytes. Authentication events expire after 60 seconds, including future timestamp checks. Payload hashes are mandatory here.
- Supply an existing Buzz channel UUID in channel_id. The API derives the public/private classification from Buzz, rather than trusting the request's classification.
- The signer must be an active Buzz user and an active member of that channel in the same community. Removed memberships, deactivated users, deleted channels and archived channels are denied. Even public channels require explicit membership for this initial knowledge pilot. Channel owners/admins receive no implicit membership bypass.
- Membership is queried on every request; it is not cached. Requests admitted just before a membership change may finish. Signed requests can be replayed within their 60-second window; they cannot be used for another body, URL or method. Submission, review and feedback writes use request identifiers for idempotency.
- Signed POST /api/ask, /api/ask/reply, /api/retrieve and the scoped /api/workspace endpoints are allowed. Workspace writes enforce Buzz roles. Legacy governance, exports, sessions and operational endpoints remain private. Health probes and workspace assets remain available.
- Missing or invalid signatures never fall back to the stack secret. Bearer tokens are not accepted on the Buzz reader.

This is an HTTP adapter for Buzz's Nostr keys, not a claim that Buzz Desktop already calls the knowledge HTTP endpoint. A client or agent must sign each request using its existing signing facility. Display names, npub text or caller-supplied author_pubkey fields alone do not authenticate a request.

## Deployment

The optional docker-compose.enterprise.yml adds gcor-enterprise on localhost:5011 alongside the private gcor-proxy. Existing ingestion, governance, MCP and projectors retain their current path.

```powershell
docker compose -f docker-compose.yml -f docker-compose.enterprise.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.enterprise.yml up -d --no-deps --build gcor-enterprise
```

Start the base stack and apply its existing migrations first. Set GCOR_PUBLIC_ORIGIN to the exact external origin clients sign, default http://127.0.0.1:5011. Behind TLS ingress, set the HTTPS origin explicitly. The validator does not trust caller Host or forwarded headers to select the signed destination. Query parameters are included in validation. Do not route employee traffic to the legacy proxy, MCP, Graphiti, object store or recovery controller.

The reader checks Buzz 0.2.1's installed schema. Its policy deliberately requires explicit membership rather than guessing broader upstream public-channel or administrator rules. Schema/query failures deny access. Buzz identities and memberships are read only; this service does not create users or alter relay data.

## Signing a request

Use a NIP-compatible signing facility with the user's existing Buzz key. The event template is:

```json
{"kind":27235,"created_at":1700000000,"content":"","tags":[["u","http://127.0.0.1:5011/api/ask"],["method","POST"],["payload","SHA256-OF-EXACT-REQUEST-BYTES"]]}
```

Use the current Unix timestamp, have the signer return the complete event including id, pubkey and Schnorr signature, and send base64(JSON(event)) as Authorization: Nostr <encoded-event>. Include channel_id in the signed JSON body. The helper in clients/buzz-knowledge.mjs accepts a signing callback without handling private keys.

Protocol reference: [NIP-98 HTTP authentication](https://github.com/nostr-protocol/nips/blob/master/98.md). Signature verification uses [coincurve's x-only Schnorr verifier](https://ofek.dev/coincurve/api/).

## Retrieval and evidence

Authenticated readers can retrieve only explicitly approved knowledge in their selected channel. Missing-state and proposed records are excluded even if approved_only=false is requested. Owners/admins can approve the pilot corpus through the workspace before expecting answers. Optional document reader lists further restrict channel membership. Each graph traversal step receives the same restrictions. Direct Graphiti tools remain an internal surface.

Generation receives the same evidence map as the returned citations. Invalid numeric references fall back to source excerpts. Reference validity does not establish semantic claim support; owner-reviewed answer evaluation remains necessary. Embedding outages fall back to keyword retrieval, and generation outages return cited excerpts.

## Workspace and verification

Open http://127.0.0.1:5011/workspace using an existing NIP-07 signer or integrate the signing helper with the Buzz host. The workspace provides proposals, jobs, approval, ownership, review dates, document readers and feedback. Buzz Desktop itself has not been modified. A real user signing session still needs pilot validation; no private identity keys were accessed.

See [Enterprise workflows](enterprise-workflows.md) for the deployed features, validation evidence, operating instructions and remaining production gates. External identity configuration has been removed; Buzz/Nostr remains the identity authority.
