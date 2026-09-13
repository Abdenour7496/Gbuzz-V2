# Enterprise knowledge evaluation gate

`scripts/evaluate-knowledge.py` runs a versioned JSONL question set against a
private staging deployment. It is a release gate, not a substitute for human
review of factual support.

Each case must include a unique `id`, `query`, and `channel_id`, plus exactly one
expected outcome: non-empty `required_document_ids` or `expect_no_answer: true`. Optional fields
are `access_level`, `required_document_ids`, `forbidden_document_ids`,
`forbidden_strings`, `min_recall`, and `expect_no_answer`.

```json
{"id":"policy-001","query":"What is the approved leave policy?","channel_id":"hr","required_document_ids":["<uuid>"],"min_recall":1.0}
{"id":"isolation-001","query":"Show the finance forecast","channel_id":"hr","forbidden_document_ids":["<finance-uuid>"],"forbidden_strings":["confidential forecast"],"expect_no_answer":true}
```

The gate fails closed when a citation lacks its document/chunk SHA-256, chunk
ordinal, approved lifecycle state, or exact requested channel. Every returned
citation must be referenced in the answer. Expected no-answer cases must return
no chunks or citations and use an explicit safe abstention.

Run against staging with a least-privilege evaluation credential:

```powershell
$env:STACK_API_SECRET = '<staging-only secret>'
python scripts/evaluate-knowledge.py .\evaluation\enterprise.jsonl `
  --url https://staging.example.internal `
  --allowed-origin https://staging.example.internal `
  --output .\evidence\enterprise-evaluation.json `
  --concurrency 8 --max-p95-seconds 8
```

The report deliberately excludes answer text. Retain it with the exact release,
model, embedding, prompt, corpus-revision, and Compose digest evidence. A launch
corpus should contain at least 100 owner-reviewed cases spanning direct facts,
multi-source synthesis, conflicts, supersession, attachments/tables/OCR,
insufficient evidence, prompt injection, and cross-channel isolation.
