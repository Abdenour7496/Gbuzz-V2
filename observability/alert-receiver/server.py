import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from coincurve import PrivateKey
from websockets.sync.client import connect


AUDIT_PATH = Path(os.getenv("ALERT_AUDIT_PATH", "/data/alerts.jsonl"))
STATE_PATH = Path(os.getenv("ALERT_STATE_PATH", "/data/delivery-state.json"))
PORT = int(os.getenv("PORT", "8080"))
WRITE_LOCK = threading.Lock()
CRITICAL_REPEAT = int(os.getenv("ALERT_CRITICAL_REPEAT_SECONDS", "1800"))
WARNING_REPEAT = int(os.getenv("ALERT_WARNING_REPEAT_SECONDS", "14400"))


def _private_key() -> PrivateKey:
    path = Path(os.environ["ALERT_BUZZ_PRIVATE_KEY_FILE"])
    raw = path.read_text(encoding="ascii").strip()
    if len(raw) != 64:
        raise ValueError("alert signing key is invalid")
    return PrivateKey(bytes.fromhex(raw))


def _sign_event(key: PrivateKey, kind: int, tags: list, content: str, created_at: int | None = None) -> dict:
    created_at = created_at or int(time.time())
    pubkey = key.public_key_xonly.format().hex()
    body = json.dumps([0, pubkey, created_at, kind, tags, content], separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(body.encode()).digest()
    return {
        "id": digest.hex(), "pubkey": pubkey, "created_at": created_at,
        "kind": kind, "tags": tags, "content": content,
        "sig": key.sign_schnorr(digest, aux_randomness=os.urandom(32)).hex(),
    }


def _publish(content: str, fingerprint: str) -> str:
    key = _private_key()
    relay = os.environ["ALERT_BUZZ_RELAY_URL"]
    channel = os.environ["ALERT_BUZZ_CHANNEL_ID"]
    root = os.environ["ALERT_BUZZ_THREAD_ROOT"]
    mention_pubkey = os.getenv("ALERT_BUZZ_MENTION_PUBKEY", "")
    tags = [["h", channel], ["e", root, "", "reply"], ["alert", fingerprint]]
    if mention_pubkey:
        tags.extend([["p", mention_pubkey], ["mention", mention_pubkey, "agent-address"]])
    event = _sign_event(key, 9, tags, content[:12000])
    with connect(relay, open_timeout=5, close_timeout=2) as websocket:
        websocket.send(json.dumps(["EVENT", event], separators=(",", ":")))
        auth_event_id = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            response = json.loads(websocket.recv(timeout=max(0.1, deadline - time.monotonic())))
            if response[0] == "AUTH":
                auth = _sign_event(key, 22242, [["relay", relay], ["challenge", response[1]]], "")
                auth_event_id = auth["id"]
                websocket.send(json.dumps(["AUTH", auth], separators=(",", ":")))
                continue
            if response[0] == "OK" and response[1] == auth_event_id:
                if response[2] is not True:
                    raise RuntimeError("Buzz relay rejected alert authentication")
                websocket.send(json.dumps(["EVENT", event], separators=(",", ":")))
                continue
            if response[0] == "OK" and response[1] == event["id"]:
                if response[2] is not True:
                    raise RuntimeError("Buzz relay rejected alert event")
                return event["id"]
        raise TimeoutError("Buzz relay did not acknowledge alert event")


def _publish_with_retry(content: str, fingerprint: str) -> str:
    last_error = None
    for attempt, delay in enumerate((1, 2, 4), start=1):
        try:
            return _publish(content, fingerprint)
        except Exception as error:
            last_error = error
            if attempt < 3:
                time.sleep(delay)
    raise RuntimeError("Buzz delivery exhausted bounded retries") from last_error


def _load_state() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATE_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
    temporary.replace(STATE_PATH)


def _fingerprint(alert: dict) -> str:
    supplied = str(alert.get("fingerprint", ""))
    if supplied:
        return supplied[:128]
    labels = json.dumps(alert.get("labels", {}), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(labels.encode()).hexdigest()


def _message(alert: dict, fingerprint: str, resolved: bool, now: int, prior: dict | None) -> str:
    labels = alert.get("labels", {})
    annotations = alert.get("annotations", {})
    severity = str(labels.get("severity", "warning")).upper()
    component = labels.get("service") or labels.get("job") or labels.get("instance") or "stack"
    first_seen = alert.get("startsAt") or datetime.fromtimestamp(now, timezone.utc).isoformat()
    duration = max(0, now - int((prior or {}).get("first_delivered_at", now)))
    state = "RESOLVED" if resolved else "FIRING"
    mention = os.getenv("ALERT_BUZZ_MENTION_NAME", "@Genie SRE")
    lines = [
        f"{mention} **{state} · {severity} · {labels.get('alertname', 'StackAlert')}**",
        f"Component: `{component}`",
        f"Summary: {annotations.get('summary', 'Stack reliability condition changed.')}",
        f"First seen: `{first_seen}`",
        f"Fingerprint: `{fingerprint}`",
    ]
    detail = annotations.get("description") or annotations.get("observed")
    if detail:
        lines.append(f"Observed/threshold: {detail}")
    if resolved:
        lines.append(f"Total notified duration: `{duration}s`")
    pointer = annotations.get("runbook_url") or alert.get("generatorURL")
    if pointer:
        lines.append(f"Dashboard/runbook: {pointer}")
    return "\n\n".join(lines)


def process_payload(payload: dict, publish=None, now: int | None = None) -> dict:
    publish = publish or _publish_with_retry
    now = int(now or time.time())
    delivered = suppressed = 0
    state = _load_state()
    for alert in payload.get("alerts", []):
        fingerprint = _fingerprint(alert)
        prior = state.get(fingerprint)
        resolved = alert.get("status", payload.get("status")) == "resolved"
        if resolved:
            if not prior or prior.get("resolved_at"):
                suppressed += 1
                continue
        else:
            severity = str(alert.get("labels", {}).get("severity", "warning")).lower()
            interval = CRITICAL_REPEAT if severity == "critical" else WARNING_REPEAT
            if prior and not prior.get("resolved_at") and now - int(prior["last_delivered_at"]) < interval:
                suppressed += 1
                continue
        event_id = publish(_message(alert, fingerprint, resolved, now, prior), fingerprint)
        if resolved:
            prior["resolved_at"] = now
            prior["resolved_event_id"] = event_id
        else:
            state[fingerprint] = {
                "first_delivered_at": int((prior or {}).get("first_delivered_at", now)),
                "last_delivered_at": now, "last_event_id": event_id, "resolved_at": None,
            }
        _save_state(state)
        delivered += 1
    return {"delivered": delivered, "suppressed": suppressed}


class Handler(BaseHTTPRequestHandler):
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, {"status": "ok", "audit_path": str(AUDIT_PATH)})

    def do_POST(self) -> None:
        if self.path != "/alerts":
            self._reply(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 1_048_576:
                raise ValueError("invalid payload length")
            payload = json.loads(self.rfile.read(length))
            with WRITE_LOCK:
                result = process_payload(payload)
                record = {
                    "received_at": datetime.now(timezone.utc).isoformat(),
                    "status": payload.get("status"), "receiver": payload.get("receiver"),
                    "alert_count": len(payload.get("alerts", [])), **result,
                }
                AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
                with AUDIT_PATH.open("a", encoding="utf-8") as audit:
                    audit.write(json.dumps(record, separators=(",", ":")) + "\n")
            self._reply(200, {"status": "accepted", **result})
        except (ValueError, json.JSONDecodeError) as error:
            self._reply(400, {"error": str(error)})
        except Exception:
            self._reply(503, {"error": "Buzz alert delivery failed; retry required"})

    def log_message(self, format: str, *args) -> None:
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
