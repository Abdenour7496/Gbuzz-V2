"""Enqueue with MinIO stopped; verify after API and MinIO restart. Isolated stack only."""
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

HEADERS = {"X-Gcor-Webhook-Secret": os.environ["STACK_API_SECRET"]}
EVENT_FILE = Path("/tmp/governance-outage-event.json")


def request(path, payload=None):
    headers = dict(HEADERS)
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        headers["Idempotency-Key"] = str(uuid4())
        data = json.dumps(payload).encode()
    with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:5001" + path,
                                                       data=data, headers=headers), timeout=10) as response:
        return json.load(response)


if sys.argv[1] == "enqueue":
    doc = request("/api/collections")[0]
    event = request("/api/knowledge/approve", {"target_document_id": doc["id"], "approved_by": "outage-test"})
    assert event["knowledge_state"] == "approved", event
    assert request("/api/governance/events/" + event["governance_event_id"])["publication_status"] == "pending"
    EVENT_FILE.write_text(json.dumps(event))
    print("Governance committed during real MinIO outage; publication is pending")
elif sys.argv[1] == "verify":
    event = json.loads(EVENT_FILE.read_text())
    for _ in range(30):
        try:
            status = request("/api/governance/events/" + event["governance_event_id"])
            if status["publication_status"] == "published":
                print("Pending governance event published after API and MinIO restart")
                break
        except urllib.error.URLError:
            pass
        time.sleep(2)
    else:
        raise AssertionError("Governance event did not recover after restart")
else:
    raise ValueError("Expected enqueue or verify")
