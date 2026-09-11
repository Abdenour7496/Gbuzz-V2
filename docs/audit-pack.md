# Audit Pack workflow

Audit Packs provide a channel-scoped, verifiable evidence export. GCOR retrieval can
identify relevant material, but the pack is built from authoritative signed Buzz
events and archived MinIO objects rather than embeddings or generated summaries.

## Security boundary

`POST /api/audit-packs` accepts a channel UUID, inclusive start time, exclusive end
time, purpose, and an attachment flag. In Buzz identity mode, only an active channel
owner or admin may export, and the requested channel must match the authenticated
scope. Internal automation may call the endpoint with the stack credential. Export
requests are bounded by event count, date range, and total bytes.

Set a dedicated `AUDIT_PACK_SIGNING_KEY` containing a 32-byte lowercase hexadecimal
secp256k1 private key. Do not reuse a relay, user, or agent key. The key never appears
in the pack; the corresponding x-only public key does.

## Evidence contents

The ZIP contains:

- canonical signed Nostr events with ID and BIP340 signature verification results;
- the original, normalized Markdown, and record manifest for every matching GCOR
  ingestion record;
- hash-verified Buzz media attachments when requested;
- `chronology.md`, ordered by signed event time;
- `manifest.json`, listing every evidence file, byte length, SHA-256 digest, scope,
  requester, purpose, counts, and a BIP340 signature over the canonical manifest.

Generation fails closed when an event signature, event ID, archive digest, attachment
digest, required object, signing configuration, or export-log write cannot be verified.
The completed ZIP is retained under `audit-packs/YYYY/MM/DD/<pack-id>.zip` in the
channel bucket. `gcor.audit_pack_exports` records who exported it, why, its scope,
counts, object location, pack digest, manifest digest, and signing public key.

## Deployment

Apply `migrations/0013_audit_pack_exports.sql`, configure the signing key, rebuild the
proxy image, and restart the proxy. Validate with an owner/admin identity and confirm
that a member identity receives HTTP 403. Independently recalculate all file hashes,
the canonical manifest digest, and the BIP340 seal before accepting an Audit Pack.
