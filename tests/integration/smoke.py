import json
import os
import urllib.parse
import urllib.request
import uuid


BASE = os.environ["GCOR_URL"].rstrip("/")
SECRET = os.environ["STACK_API_SECRET"]
HEADERS = {"X-Gcor-Webhook-Secret": SECRET}


def request(path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = dict(HEADERS)
    if data is not None:
        headers["Content-Type"] = "application/json"
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=headers), timeout=30) as response:
        return json.load(response)


token = f"integration-{uuid.uuid4()}"
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
print("integration smoke test passed")
