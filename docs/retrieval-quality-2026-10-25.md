# Retrieval quality improvements (2026-10-25)

Builds on `fix/production-blockers-20261022`. Every change is either a correction that cannot change
results without ties (fractional ranking) or is off / auto-detected by default. Nothing here was
deployed to the lab.

## Changes

| Change | Default | Files |
| --- | --- | --- |
| **Fractional ranking in reciprocal-rank fusion.** Tied scores now share their average position. Before, every chunk with the same keyword score got the best rank in the tie. In a channel full of boilerplate ("root cause review …"), hundreds of chunks all inherited keyword rank 2 and pushed the one document that actually named `INC-6818` out of the top 6. | always on | `proxy/main.py` |
| **pgvector iterative index scans** (`hnsw.iterative_scan`), so filtered HNSW searches are not starved by channel/access/RLS filters. The pgvector version is read at startup and the setting is applied only when the version is ≥ 0.8, because an unknown `hnsw.*` setting would break every connection. | `auto` | `proxy/main.py` |
| **Optional local cross-encoder reranker.** After fusion the top `RERANK_CANDIDATES` rows are re-scored by a local model (llama.cpp `--reranking`, e.g. bge-reranker-v2-m3 GGUF). The reranker only reorders rows the database query already authorised, and it fails open: any error or timeout keeps the fused order and starts a 30 s cooldown. Metrics: `gcor_rerank_total{outcome}` and `gcor_rerank_duration_seconds`. | off (`RERANKER_URL` empty) | `proxy/reranker.py`, `docker-compose.reranker.yml`, `scripts/fetch-reranker-model.{ps1,sh}` |
| Per-document chunk cap. | off (0) | `proxy/reranker.py`, `proxy/main.py` |
| **Advisory answer-faithfulness screen.** A local Ollama judge checks each sentence of a generated answer against the evidence it cites, alongside abstention, reference-bounds and expected-content checks. The output is advisory: it is not owner review and `check-knowledge-quality.py` does not accept it. | script | `scripts/judge-answers.py` |

## Measurements (isolated replica, 100k chunks, 294 signed questions)

Setup: PostgreSQL 16.2, pgvector 0.6.2 for the baseline and 0.8.1 after the upgrade. The substitute embedding model (WordLlama 256-d) is weaker than `nomic-embed-text`. There is no generation model.

| Run | Required source in citations | Required source in top 6 | Source ranked 1st | MRR | Leaks / bad citations | No-answer abstained | p50 latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline (`501e59b`) | 76.0% | 83.7% | 39.0% | 0.551 | 0 / 0 | 6/6 | 0.42 s |
| Fractional ranking | **98.8%** | **100%** | 56.5% | 0.740 | 0 / 0 | 6/6 | 0.40 s |
| + pgvector 0.8.1, iterative scan on | 98.8% | 100% | 56.5% | 0.740 | 0 / 0 | 6/6 | 0.42 s |
| + reranker endpoint returning errors | 98.8% | 100% | 56.5% | 0.740 | 0 / 0 | 6/6 | 0.47 s |
| + reranker endpoint 5 s slow (timeout 1 s, cooldown) | 98.8% | 100% | 56.5% | 0.740 | 0 / 0 | 6/6 | 0.42 s |
| + reranker wiring with a keyword-overlap stand-in scorer\* | 100% | 100% | 91.9% | 0.953 | 0 / 0 | 6/6 | 0.46 s |
| Keyword-only fallback (embeddings down), base vs new | 100% / 100% | 100% / 100% | 81.7% / 81.7% | 0.905 / 0.905 | 0 / 0 | 6/6 | 0.24 s |

\* The stand-in proves the wiring, cooldown and safety: no rows added, no leaks. It is **not** a
measurement of model quality, because model weights are not reachable from the test environment.
Measure the real reranker on the lab.

5k-chunk restored database, base vs new: citations 93.1% → 98.4%, top 6 95.1% → 100%, 0 leaks,
6/6 abstentions.

Load at 100k chunks, base vs new: 9.1 vs 9.1 asks/s at 4 concurrent, 9.6 vs 9.4 at 16, with 0%
errors or rejections.

Iterative scans had no effect here, because each test channel holds about 5k chunks and the planner
uses the channel index with an exact sort instead of HNSW. The setting matters once a single
channel is large enough for the planner to choose the HNSW index.

## Verification

- Unit tests: 17 new retrieval tests and 9 judge tests. All 163 proxy tests and the
  projector, graphiti-projector, relationship-projector, MCP and recovery-controller suites pass.
- Integration scripts against the replica: smoke, rls_scope, enterprise_boundaries,
  nostr_membership, workspace_lifecycle, buzz_chat_lifecycle, governance_faults, relationship_rls
  and attachment_lifecycle all pass.
- All 12 Compose combinations render, including the new `reranker` profile, which CI now validates.

- CI on PR #33: 23 of 24 jobs pass, including the Docker integration lifecycle, the real Buzz relay
  knowledge loop, Trivy and all Compose validations. The exception is
  `tests/test_lan_acl_behavior.ps1` on the Windows runner, which also fails on the base branch
  (LAN hardening, not used on the lab).
- In the same PR, `agent-tools/gcor-agent` dependencies were patched for advisories published after
  23 Oct: zod 4.6.7 (GHSA-mrhx-c7xc-m3w5), fast-uri 3.1.9 and ip-address 10.7.5. The agent tools
  must be re-installed when deployed.

## Lab rollout (when approved)

1. Check pgvector on the lab: `SELECT extversion FROM pg_extension WHERE extname='vector';`.
   Iterative scans need ≥ 0.8; `ALTER EXTENSION vector UPDATE;` once the image provides it.
2. Deploy the image as in `docs/production-blockers-2026-10-22.md`. Fractional ranking applies
   immediately and no migration is needed.
3. Optional reranker:
   - Run `scripts/fetch-reranker-model.ps1` (verifies the SHA-256 Hugging Face records).
   - Set `RERANKER_URL=http://reranker:8080/v1/rerank`.
   - Start it with `-f docker-compose.reranker.yml --profile reranker up -d reranker`.
   - Recreate the three knowledge services.
   - Compare `scripts/evaluate-knowledge.py` runs with and without the reranker, and watch
     `gcor_rerank_duration_seconds`. On CPU, if p95 exceeds about 1 s, lower `RERANK_CANDIDATES` or
     use a smaller reranker.
4. Run the faithfulness screen against the `qwen2.5` answers:
   ```
   python scripts/judge-answers.py cases.jsonl --identities ids.json --judge-model qwen2.5:7b --output judge.json
   ```

Rollback: redeploy the previous image. Unset `RERANKER_URL` to disable the reranker without
redeploying.

## Deferred

Docling-based PDF layout and table parsing. Its layout and table models download from Hugging Face,
which the test environment cannot reach, so it could not be tested. Do it on the lab, or with a
pre-fetched model bundle baked into the isolated parser image.
