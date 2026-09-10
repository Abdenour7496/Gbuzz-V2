# Optional Graphiti / FalkorDB extension

The default stack runs without FalkorDB, Graphiti MCP, its projector, or graph-only model downloads. Buzz conversation capture, GCOR approval/correction, citations, PostgreSQL hybrid retrieval and the PostgreSQL-native graph remain available. Temporal Graphiti enrichment is optional.

Existing FalkorDB volumes, source code, migrations and PostgreSQL projection records are retained. New knowledge continues to queue durable projection work; this consumes database space over time but does not invoke graph inference. Pending graph work is expected while disabled. Backlog alerts apply only when the graph projector is deployed. Core production preflight/update skips the graph gate; pass `-IncludeGraph` for a graph-enabled release/update.

## Enable later

For a base deployment:

```powershell
docker compose -f docker-compose.yml -f docker-compose.graph.yml up -d --build graphiti-model-init falkordb graphiti-mcp graphiti-projector
```

For an existing deployment, preserve all its current overlays and append `-f docker-compose.graph.yml`. The equivalent base service profile is `--profile graph`. Reuse the existing FalkorDB volume and existing inference/embedding settings; the projector catches up retained work. Inspect status and run `scripts/graphiti-projection-release-gate.ps1` before relying on the projection. Exhausted entries may require the existing bounded replay tool. Long periods disabled can require substantial catch-up inference.

The local deployment currently uses the base, `docker-compose.safeguards.override.json`, enterprise, observability and Buzz overlays. Its reactivation command is:

```powershell
docker compose -f docker-compose.yml -f docker-compose.safeguards.override.json -f docker-compose.enterprise.yml -f docker-compose.observability.yml -f docker-compose.buzz.yml -f docker-compose.graph.yml --profile observability up -d --no-deps --build graphiti-model-init falkordb graphiti-mcp graphiti-projector
```

Because `--no-deps` preserves the running core, confirm Ollama, PostgreSQL and GCOR are healthy first; graph initialization may take several minutes. Follow with health and projection checks.

To use graph candidates in enterprise answers, set `GRAPHITI_RETRIEVAL_ENABLED=true` in `.env` only after catch-up and recreate `gcor-enterprise` with its existing deployment overlays and `--no-deps --no-build --pull never`. Keeping this false still allows direct private Graphiti MCP use. Unavailable graph retrieval falls back to document retrieval.

External Graphiti inference is now a separate decision: append `docker-compose.graph-production.yml` after the graph overlay. `docker-compose.production.yml` alone no longer changes the graph inference provider or requires a graph API key. Switching embedding models/dimensions requires deliberate projection rebuild; do not apply an external inference overlay casually to an existing graph.

Set `AUTO_UPDATE_INCLUDE_GRAPH=true` only if the opt-in updater should manage this extension. For deployments with custom overlays, continue using their deployment workflow rather than the base-stack updater.

## Disable an existing extension

Set `GRAPHITI_RETRIEVAL_ENABLED=false` and recreate only the enterprise API with its existing overlays. Stop the graph projector first, then Graphiti MCP and FalkorDB. Remove only those stopped containers if desired, without volume deletion. Omit the graph overlay/profile from future starts; do not use `down --volumes` or broad orphan removal. Keep downloaded model files to make reactivation easier.

After removing graph containers, restart the recovery controller to clear its in-memory missing-service inventory and reload Prometheus after updating alert rules. Its persistent recovery audit remains intact.

The knowledge API's graph status endpoint continues reporting retained projection counts. A pending queue while intentionally disabled is not a core health failure. Native PostgreSQL graph links and knowledge provenance are independent of the optional Graphiti service.

## Local application record — 9 September 2026

Disabled enterprise graph retrieval, recreated only the enterprise API with its existing overlays/image, and stopped/removed the graph projector, Graphiti MCP and FalkorDB containers. Retained `gbuzz_buzz-falkordb-data`, model files and all authoritative data. Reloaded Prometheus and restarted the recovery controller to refresh its inventory.

Verification: four Compose configurations passed (core, local graph, hardened core, external graph); nine graph helper/projector tests passed; all 18 alert rules passed syntax validation. Live GCOR retrieval returned successfully with graph containers absent, and enterprise readiness returned HTTP 200. The initial projector test invocation lacked test database environment variables; rerunning with dummy test configuration passed. No full graph catch-up or end-to-end human review was run during this simplification.
