import json
import time
import urllib.error
import os
import urllib.parse
import urllib.request
import uuid


BASE = os.environ["GCOR_URL"].rstrip("/")
SECRET = os.environ["STACK_API_SECRET"]
HEADERS = {"X-Gcor-Webhook-Secret": SECRET}


def request(path, payload=None, extra_headers=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(HEADERS)
    headers.update(extra_headers or {})
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=30) as response:
        return json.load(response)


token = f"integration-{uuid.uuid4()}"
assert request("/health/live")["status"] == "ok"
ready = request("/health/ready")
assert ready["status"] == "ready", ready
assert ready["dependencies"] == {"postgres": "ok", "object_storage": "ok", "governance_schema": "ok"}, ready
payload = {
    "text": f"The release marker is {token}.",
    "title": "Integration smoke test",
    "source_uri": f"test://{token}",
    "access_level": "public",
}

# application/x-www-form-urlencoded is accepted by the FastAPI form endpoint.
encoded = urllib.parse.urlencode(payload).encode()
ingest_request = urllib.request.Request(BASE + "/api/ingest", data=encoded, headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"})
with urllib.request.urlopen(ingest_request, timeout=30) as response:
    first = json.load(response)
with urllib.request.urlopen(ingest_request, timeout=30) as response:
    second = json.load(response)

assert first["document_id"] == second["document_id"], (first, second)
assert second.get("deduplicated") is True, second

result = request("/api/retrieve", {"query": token, "top_k": 5, "hops": 0, "access_level": "public"})
assert result.get("chunks"), result
assert any(token in chunk.get("content", "") for chunk in result["chunks"]), result

records = request("/api/recovery/records")
assert isinstance(records, list) and len(records) >= 2, records

approval = {"target_document_id": first["document_id"], "approved_by": "integration-reviewer"}
key = {"Idempotency-Key": token}
approved = request("/api/knowledge/approve", approval, key)
assert approved == request("/api/knowledge/approve", approval, key)
assert approved["knowledge_state"] == "approved", approved
try:
    request("/api/knowledge/approve", {**approval, "note": "different"}, key)
    raise AssertionError("Idempotency conflict was accepted")
except urllib.error.HTTPError as error:
    assert error.code == 409, error.code
for _ in range(20):
    publication = request("/api/governance/events/" + approved["governance_event_id"])
    if publication["publication_status"] == "published":
        break
    time.sleep(1)
else:
    raise AssertionError(f"Governance publication did not complete: {publication}")
assert request("/api/governance/outbox")["published"] >= 1

# An untrusted file URL must not reach an internal service, including a redirect target.
blocked_request = urllib.request.Request(BASE + "/api/ingest",
    data=urllib.parse.urlencode({"file_url": "http://minio:9000/"}).encode(),
    headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"})
try:
    urllib.request.urlopen(blocked_request, timeout=10)
    raise AssertionError("Private remote destination was accepted")
except urllib.error.HTTPError as error:
    assert error.code == 422, error.code
print("integration smoke test passed")
