# buzzG

buzzG adds GCOR (Graph-Centric Orchestrated Retrieval) to a local [Buzz](https://github.com/block/buzz) deployment. The stack stores documents and message-derived content in Postgres with pgvector embeddings, retains original content in MinIO, and makes retrieval available through both HTTP and MCP.

Docker Compose starts a local Buzz relay, Postgres with pgvector, Redis, MinIO, Ollama with `nomic-embed-text`, the GCOR proxy, and the GCOR MCP server.

## What Runs Where

| Service | Host URL or port | Purpose |
| --- | --- | --- |
| Buzz relay | `ws://127.0.0.1:3000` | WebSocket endpoint for Buzz Desktop, `buzz-acp`, and other Buzz clients. |
| Relay health | `http://127.0.0.1:3000/_readiness` | Relay readiness check. The relay root (`/`) returns JSON metadata, not a web UI. |
| GCOR proxy | `http://127.0.0.1:5001` | HTTP ingestion, retrieval, collection, health, and metrics API. |
| GCOR health | `http://127.0.0.1:5001/health` | GCOR proxy health check. |
| GCOR MCP SSE | `http://127.0.0.1:8765/sse` | MCP endpoint for local MCP clients. |
| MinIO API | `http://127.0.0.1:9000` | S3-compatible object API. |
| MinIO console | `http://127.0.0.1:9001` | MinIO administration console. |

Buzz Desktop is the human-facing client. The relay does not expose the Buzz app at `http://127.0.0.1:3000`; visiting that URL returns the Nostr relay information document by design.

GCOR is not a Buzz channel bot. It is an ingestion/retrieval service. A Buzz agent can use it through MCP, or a Buzz workflow can send channel content to it automatically.

GCOR keeps the retrieval and graph workload Postgres-native: `pgvector` HNSW provides vector candidates, a GIN `tsvector` index provides full-text candidates, and a recursive SQL CTE expands graph relationships. Ingestion also extracts conservative proper-name and quoted Concept labels, then connects each matching Chunk to a shared Concept node with an `ABOUT` edge.

## Prerequisites

- Docker Desktop running with Docker Compose v2.
- Windows PowerShell 5.1 or PowerShell 7.
- A Buzz client such as Buzz Desktop, configured to use `ws://127.0.0.1:3000`.

## Start The Local Stack

1. Generate a local environment file. This creates stable local-only secrets:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/new-local-env.ps1
   ```

   If `.env` already exists and it is safe to replace every existing secret, run:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts/new-local-env.ps1 -Force
   ```

2. In `.env`, ensure the public relay URL is set exactly as follows. Use forward slashes in a WebSocket URL:

   ```env
   RELAY_URL=ws://127.0.0.1:3000
   ```

   Keep `BUZZ_RELAY_PRIVATE_KEY`, `BUZZ_GIT_HOOK_HMAC_SECRET`, database, Redis, S3, and ingestion secrets stable after the first startup. Replacing them can invalidate existing identities or stored credentials.

3. Build and start all services:

   ```powershell
   docker compose up -d --build
   ```

   For stack-only mode (recommended for production-like local runs), start with the override that removes host port publishing for GCOR proxy and MCP:

   ```powershell
   docker compose -f docker-compose.yml -f docker-compose.stack-only.yml up -d --build
   ```

   On the first run, Ollama downloads `nomic-embed-text`. The GCOR proxy waits for that download, MinIO initialization, and the database migration, so the first startup can take several minutes.

4. Check the service state:

   ```powershell
   docker compose ps
   curl.exe http://127.0.0.1:3000/_readiness
   curl.exe http://127.0.0.1:5001/health
   ```

   `gcor-migrate`, `minio-init`, and `ollama-init` are one-shot setup services and should show `Exited (0)`. The relay, PostgreSQL, Redis, MinIO, Ollama, GCOR proxy, and MCP server should be running.

   In stack-only mode, `docker compose ps` should show no published host ports for `gcor-proxy` and `mcp-postgres-gcor`.

## Connect Buzz Clients And Agents

Configure Buzz Desktop and any local Buzz agent harness with this endpoint:

```text
ws://127.0.0.1:3000
```

For `buzz-acp` or another process that honors Buzz environment variables:

```powershell
$env:BUZZ_RELAY_URL = 'ws://127.0.0.1:3000'
```

The relay container is not itself an AI agent. If messages in Buzz channels do not receive responses, make sure a separate agent harness such as `buzz-acp` is running and connected to this same WebSocket URL.

## Ingest Content Through HTTP

The GCOR proxy accepts a multipart `POST /api/ingest` request. Supply exactly one of `file`, `text`, `file_url`, or `session_json`.

Optional metadata fields:

- `title`, `access_level`, `agent_id`, `source_uri`
- `channel_name`, `channel_id`
- `event_id`, `event_kind`, `event_timestamp`, `author_pubkey`
- `file_name` (for URL ingestion)
- `attachments_json` (JSON array of attachment objects containing `url`)
- `metadata_json` (extra JSON object merged into `gcor.documents.metadata`)

For chat-session replay, pass `session_json` as a JSON object matching [schemas/chat-session.schema.json](schemas/chat-session.schema.json). GCOR validates required fields, stores the raw JSON record, generates a Markdown envelope, indexes that envelope, and then replays attachment URLs into the same channel bucket.

You can fetch canonical examples directly from the proxy:

```powershell
curl.exe -H "X-Gcor-Webhook-Secret: $stackSecret" http://127.0.0.1:5001/api/examples/session-json
curl.exe -H "X-Gcor-Webhook-Secret: $stackSecret" http://127.0.0.1:5001/api/examples/markdown-envelope
```

These endpoints return schema path plus a validated example payload for client bootstrapping and integration tests.

If you pass `metadata_json` from PowerShell, put the JSON in a variable or here-string first so quoting stays valid.

When `channel_name` is provided, GCOR creates or reuses a MinIO bucket derived from the channel name and stores the object there. This enables per-channel object segregation for later lifecycle, ACL, or archival policies.

Read the local stack API secret from `.env` once per PowerShell session:

```powershell
$stackSecret = (Get-Content .env | Where-Object { $_ -match '^STACK_API_SECRET=' } | Select-Object -First 1) -replace '^STACK_API_SECRET=', ''
if (-not $stackSecret) {
   $stackSecret = (Get-Content .env | Where-Object { $_ -match '^INGEST_WEBHOOK_SECRET=' } | Select-Object -First 1) -replace '^INGEST_WEBHOOK_SECRET=', ''
}
```

When `ENFORCE_STACK_API_SECRET=true` (default), send `X-Gcor-Webhook-Secret: $stackSecret` on all `/api/*` calls except `/health` and `/metrics`.

Ingest plain text:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/ingest `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
  -F "text=The release cadence is every two weeks, with a freeze on Thursdays." `
  -F "title=Release cadence decision" `
  -F "access_level=public" `
  -F "source_uri=manual://release-cadence"
```

Ingest a chat session JSON record and replay embedded attachments:

```powershell
$session = @'
{
   "session_id": "session-001",
   "channel_name": "Architecture Review",
   "channel_id": "chan-42",
   "started_at": "2026-08-08T10:00:00Z",
   "messages": [
      {
         "message_id": "msg-1",
         "author_pubkey": "npub1...",
         "created_at": "2026-08-08T10:01:00Z",
         "content": "Please review the design notes.",
         "attachments": [
            {"url": "https://example.local/design-notes.md", "name": "design-notes.md"}
         ]
      }
   ]
}
'@

curl.exe -X POST http://127.0.0.1:5001/api/ingest `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -F "session_json=$session" `
   -F "access_level=public"
```

Ingest a PDF or text file:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/ingest `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
  -F "file=@C:\path\to\report.pdf" `
  -F "title=Q4 report" `
  -F "access_level=public"
```

Ingest a file from a URL (for real-time workflow file events):

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/ingest `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -F "file_url=https://example.local/path/spec.pdf" `
   -F "file_name=spec.pdf" `
   -F "channel_name=release-planning" `
   -F "event_id=abc123" `
   -F "event_timestamp=2026-08-07T10:15:00Z" `
   -F "source_uri=buzz://abc123" `
   -F "access_level=public"
```

Successful ingestion returns `document_id`, chunk count, the target `bucket`, and `object_key`. Re-ingesting identical content returns `deduplicated: true` rather than creating duplicate chunks.

When attachment replay is triggered, the response also includes `attachments_ingested` with per-URL status.

Attachment replay uses retry with exponential backoff and timeout controls:

```env
ATTACHMENT_FETCH_TIMEOUT_SECONDS=20
ATTACHMENT_FETCH_MAX_RETRIES=2
ATTACHMENT_FETCH_RETRY_BACKOFF_SECONDS=0.5
ATTACHMENT_FETCH_RETRY_BACKOFF_MAX_SECONDS=8
```

For production hardening of `file_url` and attachment replay URLs, enable remote fetch policy controls:

```env
MAX_ATTACHMENTS_PER_REQUEST=100
REMOTE_FETCH_ALLOWED_HOSTS=raw.githubusercontent.com,files.example.com,*.cdn.example.com
REMOTE_FETCH_BLOCK_PRIVATE_HOSTS=true
```

- `MAX_ATTACHMENTS_PER_REQUEST` limits replay fan-out per ingest call.
- `REMOTE_FETCH_ALLOWED_HOSTS` (optional) restricts URL ingestion to trusted hosts.
- `REMOTE_FETCH_BLOCK_PRIVATE_HOSTS=true` blocks hosts that resolve to private, loopback, link-local, reserved, multicast, or unspecified IP addresses.

Replay metrics exported at `/metrics`:

- `gcor_attachment_replay_attempts_total`
- `gcor_attachment_replay_retries_total`
- `gcor_attachment_replay_failures_total`
- `gcor_attachment_replay_timeouts_total`
- `gcor_attachment_fetch_duration_seconds`
- `gcor_remote_fetch_blocked_total{reason=...}`

## Retrieve Indexed Content

Query the semantic index with `POST /api/retrieve`:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/retrieve `
  -H "Content-Type: application/json" `
  -d '{"query":"What is the release cadence?","top_k":8,"hops":2,"access_level":"public"}'
```

`top_k` controls how many results are returned. `hops` controls graph expansion from matching chunks; use `0` for search only. Results contain `chunks`, `graph_nodes`, and a `reflection` value of `graph`, `chunks`, or `empty`. Each returned chunk includes `vector_score`, `lexical_score`, and the combined `score`.

Retrieval uses normalized weights of `0.75` for vector similarity and `0.25` for full-text rank by default. Override them per request when a query requires more exact-keyword matching:

```json
{
   "query": "exact project code or release number",
   "top_k": 8,
   "vector_weight": 0.4,
   "lexical_weight": 0.6
}
```

List indexed document collections and inspect service health:

```powershell
curl.exe -H "X-Gcor-Webhook-Secret: $stackSecret" http://127.0.0.1:5001/api/collections
curl.exe http://127.0.0.1:5001/health
curl.exe -H "X-Gcor-Webhook-Secret: $stackSecret" http://127.0.0.1:5001/api/capabilities
```

`/api/capabilities` reports whether `pgvector`, Apache AGE, and pgvectorscale are enabled or available in the current database image. The supplied `pgvector/pgvector:pg17` image enables `pgvector`; Apache AGE and pgvectorscale are intentionally not installed because neither package is available in that pinned image. The current relational graph plus recursive CTE traversal remains the active graph backend.

## Chat-Native Knowledge Loop

GCOR now supports a direct ask/promote/correct loop intended for Buzz-first interaction:

- `POST /api/ask` returns a grounded answer plus citations from indexed chunks.
- `POST /api/ask/reply` returns a workflow-friendly reply envelope (`reply.text`) for in-channel posting.
- `POST /api/knowledge/promote` stores a curated text artifact as durable knowledge.
- `POST /api/knowledge/correct` stores a correction and links it to a target document (`CONTRADICTS` edge).
- `POST /api/knowledge/approve` transitions proposed knowledge into approved state.
- `POST /api/knowledge/transition` updates lifecycle state (`approved`, `superseded`, `archived`, `rejected`, `proposed`).

`/api/ask` and `/api/ask/reply` now enforce channel scoping. Provide at least one of `channel_id` or `channel_name`.
`/api/ask` and `/api/ask/reply` default to `approved_only=true`, so proposed knowledge is hidden until approved.
`/api/ask` and `/api/ask/reply` also default to `prefer_recent_approved=true`, which boosts newer approved knowledge over older approved entries.

Ask example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/ask `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"query":"what was decided for release cadence?","top_k":6,"hops":1,"access_level":"public","channel_id":"chan-1","channel_name":"release-planning"}'
```

Promote example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/knowledge/promote `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"title":"Release cadence decision","content":"We ship every two weeks with Thursday freeze.","source_uri":"buzz://msg-123","channel_name":"release-planning","channel_id":"chan-1","access_level":"public"}'
```

Correct example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/knowledge/correct `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"target_source_uri":"buzz://msg-123","correction_text":"Freeze moved from Thursday to Wednesday.","source_uri":"buzz://msg-456","channel_name":"release-planning","channel_id":"chan-1","access_level":"public"}'
```

Approve example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/knowledge/approve `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"target_source_uri":"buzz://msg-123","approved_by":"npub1admin","note":"Reviewed and approved"}'
```

Supersede example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/knowledge/transition `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"transition":"superseded","target_source_uri":"buzz://msg-123","superseded_by_source_uri":"buzz://msg-789","changed_by":"npub1admin","note":"Replaced by newer canonical note"}'
```

Archive example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/knowledge/transition `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"transition":"archived","target_source_uri":"buzz://msg-123","changed_by":"npub1admin","note":"No longer relevant"}'
```

Run an end-to-end lifecycle smoke test (promote, approve, supersede, archive, and scoped ask checks):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/gcor-knowledge-smoke.ps1
```

Emit CI-friendly JSON output with exit code semantics (`0` on pass, `1` on failure):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/gcor-knowledge-smoke.ps1 -Json
```

Run the same check from VS Code:

```text
Task: GCOR: Knowledge Smoke Test
```

Run a stricter regression gate that creates an isolated channel per run and enforces exact citation expectations:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/gcor-knowledge-gate.ps1 -Json
```

Use lenient mode if your environment intentionally includes extra matching citations:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/gcor-knowledge-gate.ps1 -Json -AllowExtraCitations
```

VS Code task for the strict gate:

```text
Task: GCOR: Knowledge Regression Gate
```

Optional overrides:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/gcor-knowledge-smoke.ps1 `
   -BaseUrl http://127.0.0.1:5001 `
   -ChannelId chan-lifecycle-smoke `
   -ChannelName lifecycle-smoke `
   -ApprovedBy npub1admin
```

Ask-reply envelope example:

```powershell
curl.exe -X POST http://127.0.0.1:5001/api/ask/reply `
   -H "X-Gcor-Webhook-Secret: $stackSecret" `
   -H "Content-Type: application/json" `
   -d '{"query":"/ask what is release cadence?","top_k":6,"hops":1,"max_citations":4,"channel_id":"chan-1","channel_name":"release-planning","source_event_id":"msg-999"}'
```

The response includes `reply.text`, which can be posted back to the channel by your workflow runner.

## Use GCOR From An MCP Agent

The MCP server exposes these core tools:

- `ingest_document` ingests text or base64-encoded UTF-8 content, and can also carry channel, event, file, and structured metadata fields to match the HTTP ingest API.
- `semantic_search` performs vector retrieval without graph expansion.
- `graph_expand` performs retrieval plus bounded graph expansion.
- `ask_knowledge` returns grounded answers and citations from GCOR.
- `ask_knowledge_reply` returns a posting-friendly reply envelope for chat workflows.
- `promote_knowledge` stores curated channel knowledge as durable memory.
- `correct_knowledge` records corrections linked to existing knowledge targets.
- `approve_knowledge` marks proposed knowledge as approved for default ask visibility.
- `transition_knowledge` applies lifecycle transitions including supersede/archive/reject.

For `ask_knowledge` and `ask_knowledge_reply`, pass `channel_id` or `channel_name` so retrieval is constrained to the channel scope.

For a local desktop MCP client, register this SSE endpoint:

```text
http://127.0.0.1:8765/sse
```

The repository includes the corresponding VS Code MCP configuration in [.vscode/mcp.json](.vscode/mcp.json). From an agent running inside the Docker Compose network, use `http://mcp-postgres-gcor:8765/sse` instead.

Registering MCP tools makes GCOR available to an agent; it does not create a visible `gcore-bot` or `gcor-bot` user in Buzz. Create and operate a dedicated Buzz agent identity if a channel-visible ingestion bot is required.

## Automatically Index Buzz Messages

[example-buzz-workflow.gcor-ingest.yml](example-buzz-workflow.gcor-ingest.yml) is a workflow template intended to forward channel discussions and uploaded files to GCOR in near real time. It is not automatically installed by Docker Compose.

[example-buzz-workflow.gcor-knowledge.yml](example-buzz-workflow.gcor-knowledge.yml) is a companion template for chat-native ask/promote/correct interactions.

`example-buzz-workflow.gcor-knowledge.yml` includes an `/approve` path for two-step knowledge governance.

[example-buzz-workflow.gcor-ask-reply.yml](example-buzz-workflow.gcor-ask-reply.yml) demonstrates a two-step ask-and-post pattern: call `POST /api/ask/reply`, then send `reply.text` back to the same channel.

Before installing it in your Buzz deployment:

1. Adapt the `on` trigger names and `${event.*}` fields to the workflow schema supported by the deployed Buzz version.
2. Set the workflow secret variable named `GCOR_STACK_API_SECRET` to the value of `STACK_API_SECRET` in `.env` (or `INGEST_WEBHOOK_SECRET` if you intentionally keep one shared secret).
3. Confirm the workflow runner can reach `http://gcor-proxy:5001/api/ingest` on the Compose network. If it runs on the Windows host instead, use `http://127.0.0.1:5001/api/ingest`.
4. Ensure the event payload fields used by the template exist in your Buzz version, especially `channel_name`, `channel_id`, `file_url`, `attachments`, and `created_at`.
5. Send a test message and upload a test file in a channel, then confirm entries appear at `/api/collections` and in the corresponding channel-derived MinIO bucket.

The workflow stores every forwarded event using `access_level: public`. Review that policy before enabling automatic indexing on channels that may contain restricted content.

## Embedding Configuration

The default configuration uses local Ollama embeddings:

```env
EMBEDDING_BACKEND=ollama
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_DIMS=768
```

To use OpenAI embeddings instead, set all of the following and recreate the GCOR proxy:

```env
EMBEDDING_BACKEND=openai
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMS=1536
OPENAI_API_KEY=your-key
```

Changing `EMBEDDING_DIMS` after documents have been indexed requires a deliberate schema migration and re-ingestion; do not change it casually on an active collection.

The Compose initializer downloads the configured model before `gcor-proxy` starts. Confirm it is available with:

```powershell
docker compose exec ollama ollama list
```

## Troubleshooting

| Symptom | Check and resolution |
| --- | --- |
| `http://127.0.0.1:3000` shows JSON | Expected. It is relay metadata, not the Buzz application. Use Buzz Desktop with `ws://127.0.0.1:3000`. |
| Relay restarts continuously | Run `docker compose logs relay`. Confirm `BUZZ_RELAY_PRIVATE_KEY` and `BUZZ_GIT_HOOK_HMAC_SECRET` are valid 64-character hexadecimal values. |
| GCOR proxy is not running | Run `docker compose ps` and `docker compose logs gcor-proxy ollama-init`. The proxy waits for `ollama-init` to pull the configured embedding model. |
| `POST /api/ingest` or `/api/ask` returns `401` | Send `X-Gcor-Webhook-Secret` with `STACK_API_SECRET` (or `INGEST_WEBHOOK_SECRET` when shared) from `.env`. |
| Retrieval is empty | Ingest content first, use the same `access_level` used at ingestion, and check `GET /api/collections`. |
| Ingestion returns an Ollama error | Run `docker compose up -d --force-recreate ollama-init`, wait for it to exit with code `0`, then run `docker compose exec ollama ollama list`. The configured embedding model must be listed. |
| Agents do not respond in Buzz | The relay is not an agent. Start the agent harness and configure its `BUZZ_RELAY_URL` as `ws://127.0.0.1:3000`. |
| GCOR tools do not appear to an agent | Confirm the client is connected to `http://127.0.0.1:8765/sse` and the MCP server is up with `docker compose ps mcp-postgres-gcor`. |

## Stop Or Reset

Stop services while preserving all data:

```powershell
docker compose down
```

To remove the containers and persistent local data, including the relay database, indexed documents, MinIO data, Git data, and downloaded Ollama models:

```powershell
docker compose down --volumes
```

## Security Notes

The Compose setup is intended for local development. The proxy enforces the caller-supplied `access_level` and optional `agent_id` filters during retrieval, but an untrusted API caller can choose those fields. For production, keep the GCOR proxy and MCP server on a private network, use strong stable secrets, terminate public access at a dedicated ingress layer, and derive ACL fields from trusted Buzz identity rather than from caller-controlled request data.